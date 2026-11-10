"""The specification's relationship-grant statuses (GHE-06): proposed, proof required, approval
pending, active, rejected, suspended, revoked, expired. DEV-119."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from foundation.timeutil import utc_now
from modules.policy.api import decide as policy_decide
from modules.trust.models import ProxyGrant
from tests.test_clinician import Practice
from tests.test_family import decide, requested

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def step(p, grant, name, subject="ops", **body):
    return p.post(f"staff/family-requests/{grant}/{name}", body, subject=subject)


def staff_view(p):
    r = p.get("staff/family-requests", subject="ops")
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def mine(p):
    return json.loads(p.get("family", subject="pat").content)["family"]


def may(p, operation, child):
    with tenant_transaction(p.clinic.ctx):
        return policy_decide(actor_public_id=p.patient_actor, operation=operation,
                             purpose_code="SELF_SERVICE", subject_ref=child).allowed


def revoked_events():
    return list(OutboxEvent.objects.filter(event_type="delegation.revoked.v1"))


# ------------------------------------------------------------------ the waiting steps
def test_asking_for_proof_and_marking_it_received_move_the_request_along(p):
    made = requested(p)
    assert step(p, made["id"], "proof-needed").status_code == 200
    assert [g["status"] for g in staff_view(p)["requests"]] == ["proof_required"]
    assert [g["status"] for g in mine(p)] == ["proof_required"]
    assert step(p, made["id"], "proof-received").status_code == 200
    assert [g["status"] for g in staff_view(p)["requests"]] == ["approval_pending"]
    assert decide(p, made["id"], "approve", verified=True).status_code == 200
    assert [g["status"] for g in mine(p)] == ["active"]


def test_a_waiting_request_gives_no_access_in_any_waiting_state(p):
    made = requested(p)
    child = made["person"]
    for name in (None, "proof-needed", "proof-received"):
        if name:
            step(p, made["id"], name)
        assert not may(p, "read:person", child) and not may(p, "read:booking", child)


def test_the_steps_only_apply_to_the_right_state(p):
    made = requested(p)
    assert step(p, made["id"], "proof-received").status_code == 200   # proof may arrive at once
    assert step(p, made["id"], "proof-needed").status_code == 409     # but not be asked again
    assert decide(p, made["id"], "approve", verified=True).status_code == 200
    assert step(p, made["id"], "proof-received").status_code == 409   # an active link is past this


def test_only_clinic_staff_can_take_these_steps(p):
    made = requested(p)
    for who in ("pat", "doc"):
        assert step(p, made["id"], "proof-needed", subject=who).status_code in (403, 404)


# ------------------------------------------------------------------ rejected is not revoked
def test_a_refused_request_is_rejected_and_announces_no_revoked_grant(p):
    made = requested(p)
    step(p, made["id"], "proof-needed")
    assert decide(p, made["id"], "reject").status_code == 200
    with tenant_transaction(p.clinic.ctx):
        assert ProxyGrant.objects.get(public_id=made["id"]).status == "rejected"
    assert mine(p) == [] and staff_view(p)["requests"] == []
    assert revoked_events() == []                 # it never authorized anything to cease


def test_withdrawing_a_request_that_was_never_active_announces_nothing(p):
    made = requested(p)
    assert p.post(f"family/{made['id']}/withdraw", subject="pat").status_code == 200
    assert revoked_events() == []


# ------------------------------------------------------------------ suspended and resumed
def approve(p):
    made = requested(p)
    assert decide(p, made["id"], "approve", verified=True).status_code == 200
    return made


def test_a_suspended_guardian_has_no_access_until_the_clinic_resumes_it(p):
    made = approve(p)
    child = made["person"]
    assert may(p, "read:booking", child)
    assert step(p, made["id"], "suspend", reason="Safeguarding concern").status_code == 200
    assert not may(p, "read:booking", child) and not may(p, "read:comms", child)
    assert [g["status"] for g in staff_view(p)["approved"]] == ["suspended"]
    assert [g["status"] for g in mine(p)] == ["suspended"]
    assert step(p, made["id"], "resume").status_code == 200
    assert may(p, "read:booking", child)
    assert [g["status"] for g in staff_view(p)["approved"]] == ["active"]


def test_suspending_needs_a_reason_and_only_applies_to_an_active_link(p):
    waiting = requested(p)
    assert step(p, waiting["id"], "suspend", reason="Safeguarding concern").status_code == 409
    made = approve(p)
    assert step(p, made["id"], "suspend").status_code == 422
    assert step(p, made["id"], "suspend", reason="no").status_code == 422
    assert step(p, made["id"], "resume").status_code == 409          # nothing is suspended


def test_suspending_announces_that_the_grant_ceased_once(p):
    made = approve(p)
    step(p, made["id"], "suspend", reason="Safeguarding concern")
    (event,) = revoked_events()
    assert set(event.payload) == {"grant_ref", "delegate_ref"}
    step(p, made["id"], "resume")
    assert len(revoked_events()) == 1                                # resuming announces nothing


# ------------------------------------------------------------------ expired
def test_an_active_link_past_its_end_date_reads_as_expired_and_gives_no_access(p):
    made = approve(p)
    with tenant_transaction(p.clinic.ctx):
        ProxyGrant.objects.filter(public_id=made["id"]).update(
            valid_until=utc_now() - dt.timedelta(days=1))
    assert [g["status"] for g in staff_view(p)["approved"]] == ["expired"]
    assert not may(p, "read:booking", made["person"])
    assert step(p, made["id"], "resume").status_code == 409          # expired is not "paused"
