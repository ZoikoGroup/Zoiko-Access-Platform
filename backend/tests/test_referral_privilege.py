"""A doctor named to answer referrals who loses the role at the site must not stay named
(GHE-06 professional.privilege.revoked.v1: revoke service actions; a clinical fallback is required).
DEV-117."""
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from modules.evidence.models import AuditEvent
from modules.referral.subscribers import register_subscribers
from modules.trust import api as trust
from modules.trust.models import RoleAssignment
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    register_subscribers()


@pytest.fixture
def c():
    clinic = Clinic2()                    # doc2 is the named owner, no fallback yet
    clinic.third = clinic.login("doc3", trust.ActorType.CLINICIAN, "R03")
    return clinic


def name(c, owner, fallback=""):
    r = c.post("staff/referral-inbox", {"owner": owner, "fallback": fallback}, subject="boss")
    assert r.status_code == 200, r.content


def inbox(c):
    return json.loads(c.get("staff/referral-inbox", subject="boss").content)


def leave(c, actor):
    """The doctor's role ends, and the worker delivers the news."""
    with tenant_transaction(c.clinic.ctx):
        held = RoleAssignment.objects.get(actor__public_id=actor, role__role_code="R03")
        trust.end_assignment(held.public_id, reason="left_clinic", by="system")
    for _ in range(4):
        relay_once(dispatch.dispatch)


def answerer_list(c, subject):
    r = c.get("clinician/referral-inbox", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["referrals"]


def test_when_the_owner_leaves_the_fallback_steps_up_and_waiting_referrals_move(c):
    name(c, c.doc2, c.third)
    made = issued(c)
    leave(c, c.doc2)
    box = inbox(c)
    assert box["owner"]["id"] == c.third and box["fallback"] is None
    assert [r["id"] for r in answerer_list(c, "doc3")] == [made["id"]]
    assert c.incoming()[0]["owner"]["id"] == c.third
    assert c.decide(made["id"], "accepted", subject="doc3").status_code == 200


def test_the_access_the_referral_gave_the_leaver_ends(c):
    name(c, c.doc2, c.third)
    issued(c)
    assert len(answerer_list(c, "doc2")) == 1
    leave(c, c.doc2)
    assert c.get("clinician/referral-inbox", subject="doc2").status_code == 403   # no role at all


def test_with_no_fallback_nobody_is_named_and_nobody_can_accept(c):
    made = issued(c)                                     # doc2 owns it, there is no fallback
    leave(c, c.doc2)
    assert inbox(c)["owner"] is None
    assert c.incoming()[0]["owner"] is None and c.incoming()[0]["state"] == "issued"
    for who in ("doc", "doc2", "doc3"):
        assert c.decide(made["id"], "accepted", subject=who).status_code == 403


def test_naming_a_new_owner_afterwards_puts_the_referral_back_in_someones_hands(c):
    made = issued(c)
    leave(c, c.doc2)
    name(c, c.third)
    assert [r["id"] for r in answerer_list(c, "doc3")] == [made["id"]]
    assert c.decide(made["id"], "accepted", subject="doc3").status_code == 200


def test_when_only_the_fallback_leaves_the_owner_stays(c):
    name(c, c.doc2, c.third)
    issued(c)
    leave(c, c.third)
    box = inbox(c)
    assert box["owner"]["id"] == c.doc2 and box["fallback"] is None
    assert len(answerer_list(c, "doc2")) == 1


def test_a_doctor_who_was_never_named_leaving_changes_nothing(c):
    issued(c)
    leave(c, c.third)
    assert inbox(c)["owner"]["id"] == c.doc2


def test_a_doctor_who_has_the_role_again_by_delivery_time_is_left_alone(c):
    with tenant_transaction(c.clinic.ctx):
        held = RoleAssignment.objects.get(actor__public_id=c.doc2, role__role_code="R03")
        trust.end_assignment(held.public_id, reason="by_mistake", by="system")
        trust.assign_role(actor_public_id=c.doc2, organization_public_id=c.clinic.org,
                          role_code="R03", by="system")
    for _ in range(4):
        relay_once(dispatch.dispatch)
    assert inbox(c)["owner"]["id"] == c.doc2


def test_the_change_is_recorded_as_the_systems_own_act(c):
    name(c, c.doc2, c.third)
    leave(c, c.doc2)
    with tenant_transaction(c.clinic.ctx):
        found = AuditEvent.objects.filter(action="referral.inbox_changed")
        assert found.count() == 1
        event = found.get()
        assert event.actor_ref == "system" and event.reason_code == "role_ended"
