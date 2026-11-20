"""What a guardian may do for a dependent (GHE-06 W05, AT-15, AT-18). DEV-110.

Staff choose scopes when they approve; the policy engine applies them on every request; clinical
records are never granted."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.policy.api import decide as policy_decide
from tests.test_clinician import Practice
from tests.test_family import booking_for, decide, requested

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def approve_with(p, scopes):
    made = requested(p)
    assert decide(p, made["id"], "approve", verified=True, scopes=scopes).status_code == 200
    return made


def may(p, operation, child):
    """Would the guardian be allowed this operation on the dependent?"""
    with tenant_transaction(p.clinic.ctx):
        return policy_decide(actor_public_id=p.patient_actor, operation=operation,
                             purpose_code="SELF_SERVICE", subject_ref=child).allowed


# ------------------------------------------------------------------ choosing the scopes
def test_approving_needs_at_least_one_known_scope(p):
    made = requested(p)
    for bad in ([], ["records"], ["appointments", "everything"], "appointments"):
        r = decide(p, made["id"], "approve", verified=True, scopes=bad)
        assert r.status_code == 422, bad
    assert decide(p, made["id"], "approve", verified=True).status_code == 200   # all four (helper)


def test_the_chosen_scopes_are_shown_to_staff_and_to_the_guardian(p):
    made = approve_with(p, ["appointments", "messages"])
    mine = json.loads(p.get("family", subject="pat").content)["family"]
    assert [sorted(g["scopes"]) for g in mine] == [["appointments", "messages"]]
    assert made["id"]


# ------------------------------------------------------------------ applying them
def test_only_the_chosen_parts_are_open_to_the_guardian(p):
    made = approve_with(p, ["appointments"])
    child = made["person"]
    assert may(p, "read:person", child)                 # the dependent's name: always
    assert may(p, "read:booking", child) and may(p, "write:booking", child)
    assert not may(p, "read:comms", child) and not may(p, "write:comms", child)
    assert not may(p, "read:referral", child)
    assert not may(p, "read:coverage", child)


def test_a_guardian_with_only_messages_can_write_but_not_book(p):
    child = approve_with(p, ["messages"])["person"]
    assert may(p, "write:comms", child) and may(p, "read:comms", child)
    assert not may(p, "write:booking", child) and not may(p, "read:booking", child)
    assert booking_for(p, child).status_code == 403


def test_a_guardian_with_appointments_can_really_book(p):
    child = approve_with(p, ["appointments"])["person"]
    assert booking_for(p, child).status_code == 201


def test_clinical_records_are_never_open_to_a_guardian_whatever_was_ticked(p):
    child = approve_with(p, ["appointments", "messages", "referrals", "insurance"])["person"]
    for op in ("read:diagnostic", "read:medication", "read:billing"):
        assert not may(p, op, child), op


def test_the_guardians_own_record_is_not_limited_by_the_scopes(p):
    approve_with(p, ["appointments"])
    for op in ("read:person", "read:booking", "write:booking", "read:comms", "read:coverage"):
        assert may(p, op, p.person), op              # their own record: the patient rules apply
