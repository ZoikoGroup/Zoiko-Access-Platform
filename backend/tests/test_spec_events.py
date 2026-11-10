"""Events the specification names (GHE-06): delegation.revoked.v1,
professional.privilege.revoked.v1 and access.denied.v1. They carry references only, and a quiet list
filter never raises one. DEV-116."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from modules.policy.models import AccessDecision
from modules.trust import api as trust
from modules.trust.models import RoleAssignment
from tests.test_api import book
from tests.test_clinician import Practice
from tests.test_family import approved

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def sent(event_type):
    return list(OutboxEvent.objects.filter(event_type=event_type).order_by("created_at"))


# ------------------------------------------------------------------ delegation.revoked.v1
def test_ending_a_guardians_access_announces_it_once_with_references_only(p):
    made = approved(p)
    assert sent("delegation.revoked.v1") == []
    assert p.post(f"family/{made['id']}/withdraw", subject="pat").status_code == 200
    (event,) = sent("delegation.revoked.v1")
    assert set(event.payload) == {"grant_ref", "delegate_ref"}
    assert event.payload["delegate_ref"] == p.patient_actor
    text = json.dumps(event.payload)
    assert p.person not in text and made["person"] not in text
    p.post(f"family/{made['id']}/withdraw", subject="pat")             # repeating changes nothing
    assert len(sent("delegation.revoked.v1")) == 1


# ---------------------------------------------------------- professional.privilege.revoked.v1
def end_role(p, actor, code):
    with tenant_transaction(p.clinic.ctx):
        held = RoleAssignment.objects.get(actor__public_id=actor, role__role_code=code)
        trust.end_assignment(held.public_id, reason="left_clinic", by="system")


def test_a_doctor_leaving_the_clinic_announces_the_ended_privilege_once(p):
    end_role(p, p.doc, "R03")
    (event,) = sent("professional.privilege.revoked.v1")
    assert event.payload["role_code"] == "R03" and event.payload["actor_ref"] == p.doc
    assert event.payload["organization_ref"] == p.clinic.org
    end_role(p, p.doc, "R03")                                          # already ended: nothing new
    assert len(sent("professional.privilege.revoked.v1")) == 1


def test_a_receptionist_leaving_is_not_a_privilege_event(p):
    end_role(p, p.desk, "R04")
    assert sent("professional.privilege.revoked.v1") == []


# ------------------------------------------------------------------ access.denied.v1
def test_a_refusal_the_person_hit_is_announced_without_saying_who_or_about_whom(p):
    before = len(sent("access.denied.v1"))
    body = {"organization": p.clinic.org, "name": "X", "category": "dermatology",
            "duration_minutes": 20}
    assert p.post("services", body, subject="pat").status_code == 403   # a patient may not
    events = sent("access.denied.v1")[before:]
    assert len(events) == 1
    payload = events[0].payload
    assert set(payload) == {"operation", "purpose", "reason"}
    assert payload["operation"] == "write:catalog" and payload["purpose"] == "SELF_SERVICE"
    text = json.dumps(payload)
    assert p.patient_actor not in text and p.person not in text and p.clinic.org not in text


def test_the_quiet_checks_that_filter_a_list_raise_no_event(p):
    p.add_times(p.doc)
    made = book(p, p.slot_of("doc"), subject="pat").json()["booking"]["id"]
    p.post(f"bookings/{made}/confirm", subject="ops")
    before = len(sent("access.denied.v1"))
    got = p.get("clinician/patients", subject="doc", HTTP_X_ZOIKO_PURPOSE="OPERATIONS")
    assert json.loads(got.content)["patients"] == []                     # filtered out, quietly
    with tenant_transaction(p.clinic.ctx):
        assert AccessDecision.objects.filter(actor_ref=p.doc, allowed=False).exists()
    assert len(sent("access.denied.v1")) == before
