"""The stored disclosure log (DEV-352): a line is written when something about a patient is sent to
another organization, when the patient's agreement releases their details, and when they take it
back. Lines never change, and only the clinic's administrators can read them."""
import json

import pytest
from django.db import DatabaseError, connection

from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from modules.evidence import api as evidence
from modules.evidence.models import AuditEvent, Disclosure
from modules.trust import api as trust
from tests import test_claims as claims
from tests import test_exchange as exchange
from tests.conftest import publish_service
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_platform import Platform
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    """A clinic with an administrator and a patient it has seen, a lab, and a payer."""
    Platform()
    clinic = Practice()
    clinic.login("boss", trust.ActorType.FACILITY_USER, "R13")
    with tenant_transaction(clinic.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=clinic.doc, person_public_id=clinic.person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=clinic.clinic.org, by="system")
        trust.link_person_to_site(clinic.person, clinic.clinic.org, "booking")
    return clinic, exchange.Partner("R05", "lab1"), claims.Payer()


def get(clinic, person=None, subject="boss"):
    return clinic.get(f"staff/disclosures?person={person or clinic.person}", subject=subject)


def lines(clinic, person=None):
    r = get(clinic, person)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["entries"]


def stages(clinic):
    return [(e["kind"], e["stage"]) for e in lines(clinic)]


def test_nothing_sent_means_an_empty_log(world):
    clinic, _, _ = world
    assert lines(clinic) == []


def test_a_claim_is_logged_when_sent_released_and_taken_back(world):
    clinic, _, payer = world
    claim_id = claims.made(clinic, payer, consent=False)
    (sent,) = lines(clinic)
    assert (sent["kind"], sent["stage"], sent["record"]) == ("claim", "sent", claim_id)
    assert sent["to"] == payer.tenant_name and sent["by"] and sent["at"]
    assert claims.agree(clinic, claim_id).status_code == 200
    claims.agree(clinic, claim_id)                                   # again: adds no line
    assert stages(clinic) == [("claim", "released"), ("claim", "sent")]
    assert claims.agree(clinic, claim_id, "withdraw").status_code == 200
    assert stages(clinic)[0] == ("claim", "withdrawn")
    assert {e["to"] for e in lines(clinic)} == {payer.tenant_name}
    blob = json.dumps(lines(clinic))
    assert "MEM-0001" not in blob and "Consultation" not in blob     # never what was said


def test_a_declined_claim_releases_nothing(world):
    clinic, _, payer = world
    claim_id = claims.made(clinic, payer, consent=False)
    assert claims.agree(clinic, claim_id, "decline").status_code == 200
    assert stages(clinic) == [("claim", "sent")]


def test_a_request_to_a_partner_is_logged_the_same_way(world):
    clinic, lab, _ = world
    exchange.give_profile(clinic)
    request_id = exchange.made(clinic, lab, consent=False)
    assert stages(clinic) == [("request", "sent")]
    assert exchange.agree(clinic, request_id).status_code == 200
    assert exchange.agree(clinic, request_id, "withdraw").status_code == 200
    assert stages(clinic) == [
        ("request", "withdrawn"), ("request", "released"), ("request", "sent")]
    assert {(e["to"], e["record"]) for e in lines(clinic)} == {(lab.tenant_name, request_id)}
    with tenant_transaction(clinic.clinic.ctx):
        assert {d.recipient_ref for d in Disclosure.objects.all()} == {lab.tenant_public_id}
        assert {d.organization_ref for d in Disclosure.objects.all()} == {clinic.clinic.org}


def test_a_refused_send_logs_nothing(world):
    clinic, _, payer = world
    assert claims.send(clinic, payer, amount=-1).status_code == 422
    assert claims.send(clinic, payer, subject="pat").status_code == 403
    assert lines(clinic) == []


def test_a_referral_inside_one_organization_discloses_nothing():
    c = Clinic2()
    issued(c)
    assert lines(c) == []


def test_a_referral_to_another_organization_is_logged():
    c = Clinic2()
    with tenant_transaction(c.clinic.ctx):
        other = trust.create_organization(name="City Heart Centre").public_id
        desk = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="heart")
        trust.assign_role(actor_public_id=desk.public_id, organization_public_id=other,
                          role_code="R04", by="system")
    made = c.post("services", {"organization": other, "name": "Heart clinic",
                               "category": "specialist", "duration_minutes": 30}, subject="heart")
    assert made.status_code == 201, made.content
    service = json.loads(made.content)["service"]["id"]
    publish_service(c.clinic.ctx, service)
    referral = issued(c, service=service)
    (line,) = lines(c)
    assert (line["kind"], line["stage"], line["record"]) == ("referral", "sent", referral["id"])
    assert line["to"] == "City Heart Centre" and line["by"] == "doc"


def test_every_look_is_audited(world):
    clinic, _, _ = world
    lines(clinic)
    with tenant_transaction(clinic.clinic.ctx):
        seen = AuditEvent.objects.filter(action="disclosure.read", object_ref=clinic.person)
        assert seen.count() == 1


def test_only_the_clinics_administrators_can_read_it(world):
    clinic, _, payer = world
    claims.made(clinic, payer, consent=False)
    for who in ("ops", "doc", "pat"):
        assert get(clinic, subject=who).status_code == 403, who
    assert clinic.client.get(API + f"staff/disclosures?person={clinic.person}").status_code == 401
    assert clinic.post("staff/disclosures", {}, subject="boss").status_code == 405
    assert clinic.get("staff/disclosures", subject="boss").status_code == 422


def test_another_clinic_never_sees_these_lines(world):
    clinic, _, payer = world
    claims.made(clinic, payer, consent=False)
    other = Practice()
    other.login("boss", trust.ActorType.FACILITY_USER, "R13")
    r = other.get(f"staff/disclosures?person={clinic.person}", subject="boss")
    assert r.status_code in (403, 404) or json.loads(r.content)["entries"] == []


def test_a_line_can_never_be_changed_or_removed(world):
    clinic, _, payer = world
    claims.made(clinic, payer, consent=False)
    with tenant_transaction(clinic.clinic.ctx):
        line = Disclosure.objects.get()
        with pytest.raises(PermissionError):
            line.save()
        with pytest.raises(PermissionError):
            line.delete()
    for sql in ("UPDATE evidence_disclosure SET recipient_name = 'x'",
                "DELETE FROM evidence_disclosure"):
        with pytest.raises(DatabaseError), tenant_transaction(clinic.clinic.ctx):
            with connection.cursor() as cursor:
                cursor.execute(sql)
    assert len(lines(clinic)) == 1


def test_an_unknown_kind_or_stage_is_refused(world):
    clinic, _, _ = world
    for over in ({"kind": "gossip"}, {"stage": "maybe"}):
        fields = {"person_ref": clinic.person, "kind": "claim", "record_ref": "r" * 22,
                  "actor_ref": "system", "recipient_name": "x", **over}
        with pytest.raises(ZoikoError), tenant_transaction(clinic.clinic.ctx):
            evidence.record_disclosure(**fields)
