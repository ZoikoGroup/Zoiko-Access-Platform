import itertools
import uuid

import pytest
from django.db import DatabaseError, connection, transaction

from foundation.cell_context import build_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import verify_chain
from modules.evidence.models import AuditEvent
from modules.policy.api import NoRelationships, Reason, decide
from modules.policy.models import AccessDecision
from modules.policy.rules import (
    HARD_PROHIBITIONS,
    PURPOSES,
    RESOURCE_CLASSES,
    ROLE_PURPOSES,
    RULES,
    VERBS,
)
from modules.trust import api as trust
from modules.trust.roles_seed import DEFAULT_ROLES
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


class Verified:
    """A stand-in for the future relationship tables that vouches for everything."""

    def __init__(self):
        self.asked = []

    def verified(self, *, actor_public_id, condition, subject_ref):
        self.asked.append((actor_public_id, condition, subject_ref))
        return True


class World:
    """One tenant with two clinics and a way to create people holding roles."""

    def __init__(self, tenant_type=trust.TenantType.PROVIDER_ORG):
        self.tenant_name = f"Alpha {uuid.uuid4().hex[:8]}"
        tenant = trust.create_tenant(name=self.tenant_name, type=tenant_type)
        self.ctx = build_context(tenant.id)
        self.tenant_public_id = tenant.public_id
        with tenant_transaction(self.ctx):
            self.org = trust.create_organization(name="North").public_id
            self.other_org = trust.create_organization(name="South").public_id
        self.n = 0

    def person(self, *roles, org=None):
        self.n += 1
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(
                actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject=f"u{self.n}")
            for role in roles:
                trust.assign_role(actor_public_id=actor.public_id,
                                  organization_public_id=org or self.org,
                                  role_code=role, by="system")
        return actor.public_id

    def ask(self, actor, operation, purpose, **kwargs):
        with tenant_transaction(self.ctx):
            return decide(actor_public_id=actor, operation=operation, purpose_code=purpose,
                          **kwargs)


@pytest.fixture
def world():
    return World()


# ------------------------------------------------------------------ allowed
def test_clinic_ops_may_write_bookings_at_their_own_organization(world):
    ops = world.person("R04")
    d = world.ask(ops, "write:booking", "OPERATIONS", organization_public_id=world.org)
    assert (d.allowed, d.reason, d.role_code) == (True, Reason.ALLOWED, "R04")
    d.require()                                              # does not raise


def test_catalog_is_readable_without_an_organization(world):
    patient = world.person("R01")
    assert world.ask(patient, "read:catalog", "SELF_SERVICE").allowed


def test_the_employer_may_read_aggregate_billing_at_its_own_organization(world):
    employer = world.person("R08")
    assert world.ask(employer, "read:billing", "PROGRAM_ADMIN",
                     organization_public_id=world.org).allowed


# ------------------------------------------------------------------ denied: the main fences
def test_default_is_deny_for_anything_unknown(world):
    ops = world.person("R04")
    for operation in ("read:unicorn", "delete:booking", "booking", "READ:booking", "", "read:"):
        d = world.ask(ops, operation, "OPERATIONS", organization_public_id=world.org)
        assert (d.allowed, d.reason) == (False, Reason.UNKNOWN_OPERATION)


def test_an_organization_is_required_and_must_match_the_role_held(world):
    ops = world.person("R04")
    base = ("write:booking", "OPERATIONS")
    assert world.ask(ops, *base).reason == Reason.ORG_REQUIRED
    d = world.ask(ops, *base, organization_public_id=world.other_org)
    assert (d.allowed, d.reason) == (False, Reason.ORG_MISMATCH)
    assert world.ask(ops, *base, organization_public_id="not an id!").reason == Reason.ORG_REQUIRED


def test_the_purpose_must_fit_the_role(world):
    ops = world.person("R04")
    d = world.ask(ops, "write:booking", "PAYMENT_COVERAGE", organization_public_id=world.org)
    assert d.reason == Reason.PURPOSE_NOT_ALLOWED
    assert world.ask(ops, "write:booking", "WHATEVER",
                     organization_public_id=world.org).reason == Reason.PURPOSE_NOT_ALLOWED


def test_a_role_cannot_use_a_verb_it_was_not_given(world):
    ops = world.person("R04")
    assert world.ask(ops, "attest:referral", "OPERATIONS",
                     organization_public_id=world.org).reason == Reason.ROLE_NOT_PERMITTED
    assert world.ask(ops, "read:medication", "OPERATIONS",
                     organization_public_id=world.org).reason == Reason.ROLE_NOT_PERMITTED


def test_the_employer_is_refused_every_clinical_class_whatever_else_is_true(world):
    employer = world.person("R08")
    yes = Verified()
    for verb, cls, purpose in itertools.product(
        VERBS, HARD_PROHIBITIONS["R08"], PURPOSES
    ):
        d = world.ask(employer, f"{verb}:{cls}", purpose, organization_public_id=world.org,
                      subject_ref="subj_1", relationships=yes)
        assert (d.allowed, d.reason) == (False, Reason.HARD_PROHIBITION), (verb, cls, purpose)
    assert yes.asked == []                                   # never even reached the relationship


def test_support_staff_have_no_standing_access_to_patient_data(world):
    support = world.person("R11")
    for cls in ("person", "diagnostic", "medication"):
        d = world.ask(support, f"read:{cls}", "SUPPORT", organization_public_id=world.org,
                      subject_ref="subj_1", relationships=Verified())
        assert d.reason == Reason.HARD_PROHIBITION


def test_holding_an_employer_role_never_unlocks_other_roles_clinical_limits(world):
    both = world.person("R08", "R03")
    # Clinical read for a clinician needs a verified relationship; without one it stays denied.
    d = world.ask(both, "read:diagnostic", "DIRECT_CARE", subject_ref="subj_1")
    assert (d.allowed, d.reason) == (False, Reason.RELATIONSHIP_UNVERIFIED)
    # The employer purpose cannot be used to open the clinician's door.
    d = world.ask(both, "read:diagnostic", "PROGRAM_ADMIN", subject_ref="subj_1",
                  relationships=Verified())
    assert d.allowed is False


# ------------------------------------------------------------------ relationships fail closed
def test_patient_data_is_denied_until_a_relationship_is_verified(world):
    clinician = world.person("R03")
    d = world.ask(clinician, "read:person", "DIRECT_CARE", subject_ref="subj_1")
    assert (d.allowed, d.reason) == (False, Reason.RELATIONSHIP_UNVERIFIED)
    with pytest.raises(ZoikoError) as excinfo:
        d.require()
    assert excinfo.value.code == ErrorCode.SUBJECT_LINK_UNVERIFIED
    assert world.ask(clinician, "read:person", "DIRECT_CARE").allowed is False   # no subject at all
    assert isinstance(NoRelationships().verified(
        actor_public_id="a", condition="REL", subject_ref="s"), bool)


def test_a_verified_relationship_opens_the_door_and_is_asked_the_right_question(world):
    clinician = world.person("R03")
    checker = Verified()
    d = world.ask(clinician, "read:person", "DIRECT_CARE", subject_ref="subj_1",
                  relationships=checker)
    assert (d.allowed, d.role_code) == (True, "R03")
    assert checker.asked == [(clinician, "REL", "subj_1")]
    patient = world.person("R01")
    assert world.ask(patient, "read:person", "SELF_SERVICE", subject_ref="subj_9",
                     relationships=checker).allowed
    assert checker.asked[-1] == (patient, "OWN", "subj_9")


def test_denial_raises_policy_denied(world):
    employer = world.person("R08")
    d = world.ask(employer, "read:person", "PROGRAM_ADMIN", organization_public_id=world.org)
    with pytest.raises(ZoikoError) as excinfo:
        d.require()
    assert excinfo.value.code == ErrorCode.POLICY_DENIED


# ------------------------------------------------------------------ who is asking
def test_unknown_suspended_ended_and_foreign_actors_are_denied(world):
    ops = world.person("R04")
    ask = ("write:booking", "OPERATIONS")
    kw = {"organization_public_id": world.org}
    assert world.ask("missing_actor", *ask, **kw).reason == Reason.ACTOR_UNKNOWN
    assert world.ask("bad id!!", *ask, **kw).reason == Reason.ACTOR_UNKNOWN
    assert world.ask(world.person(), *ask, **kw).reason == Reason.NO_ROLE      # no roles at all

    other = World()                                           # a different tenant entirely
    assert other.ask(ops, *ask, organization_public_id=other.org).reason == Reason.ACTOR_UNKNOWN

    with tenant_transaction(world.ctx):
        trust.set_actor_status(ops, trust.Status.SUSPENDED, by="system")
    assert world.ask(ops, *ask, **kw).reason == Reason.NO_ROLE
    with tenant_transaction(world.ctx):
        trust.set_actor_status(ops, trust.Status.ACTIVE, by="system")
    assert world.ask(ops, *ask, **kw).allowed


def test_an_ended_role_stops_working_immediately(world):
    ops = world.person("R04")
    ask = ("write:booking", "OPERATIONS")
    kw = {"organization_public_id": world.org}
    assert world.ask(ops, *ask, **kw).allowed
    with tenant_transaction(world.ctx):
        from modules.trust.models import RoleAssignment
        ended = RoleAssignment.objects.get(actor__public_id=ops)
        trust.end_assignment(ended.public_id, reason="departure", by="system")
    assert world.ask(ops, *ask, **kw).reason == Reason.NO_ROLE


def test_a_role_held_at_another_organization_does_not_count_here(world):
    south_ops = world.person("R04", org=world.other_org)
    d = world.ask(south_ops, "write:booking", "OPERATIONS", organization_public_id=world.org)
    assert (d.allowed, d.reason) == (False, Reason.ORG_MISMATCH)
    assert world.ask(south_ops, "write:booking", "OPERATIONS",
                     organization_public_id=world.other_org).allowed


# ------------------------------------------------------------------ recording
def test_every_decision_is_stored_and_audited_allowed_or_denied(world):
    ops = world.person("R04")
    world.ask(ops, "write:booking", "OPERATIONS", organization_public_id=world.org)
    world.ask(ops, "read:person", "SUPPORT", organization_public_id=world.org)
    with tenant_transaction(world.ctx):
        rows = list(AccessDecision.objects.order_by("created_at"))
        assert [(r.allowed, r.reason_code) for r in rows] == [
            (True, "ALLOWED"), (False, "PURPOSE_NOT_ALLOWED")]
        assert rows[0].role_code == "R04" and rows[0].policy_version
        decided = AuditEvent.objects.filter(action="access.decide").order_by("chain_seq")
        assert [(e.outcome, e.reason_code) for e in decided] == [
            ("allowed", "ALLOWED"), ("denied", "PURPOSE_NOT_ALLOWED")]
        assert decided[0].object_ref == rows[0].public_id
        assert verify_chain().ok


def test_stored_decisions_cannot_be_changed_or_removed(world):
    ops = world.person("R04")
    d = world.ask(ops, "write:booking", "OPERATIONS", organization_public_id=world.org)
    with tenant_transaction(world.ctx):
        row = AccessDecision.objects.get(public_id=d.decision_public_id)
        with pytest.raises(PermissionError):
            row.save()
        with pytest.raises(PermissionError):
            row.delete()
    for sql in ("UPDATE policy_accessdecision SET allowed = true",
                "DELETE FROM policy_accessdecision"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql)


def test_the_app_role_decides_and_sees_only_its_own_tenants_decisions(world):
    ops = world.person("R04")
    with tenant_transaction(world.ctx), as_app_role():
        assert decide(actor_public_id=ops, operation="write:booking", purpose_code="OPERATIONS",
                      organization_public_id=world.org).allowed
        assert AccessDecision.objects.count() == 1
    other = World()
    with tenant_transaction(other.ctx), as_app_role():
        assert AccessDecision.objects.count() == 0


# ------------------------------------------------------------------ the rule table itself
def test_the_rule_tables_are_consistent_with_each_other_and_the_role_catalog():
    seeded = {code for code, *_ in DEFAULT_ROLES}
    assert set(RULES) == set(ROLE_PURPOSES) == seeded
    for role, rules in RULES.items():
        for cls, (verbs, condition) in rules.items():
            assert cls in RESOURCE_CLASSES and verbs <= set(VERBS)
            assert condition in {"ANY", "ORG", "REL", "OWN", "PROXY"}
            assert cls not in HARD_PROHIBITIONS.get(role, ()), (role, cls)
            if cls != "catalog" and cls != "analytics":
                assert condition != "ANY", (role, cls)   # only public data skips the fences
    for purposes in ROLE_PURPOSES.values():
        assert purposes <= set(PURPOSES)


def test_the_employer_has_no_rule_for_any_clinical_class():
    clinical = {"person", "booking", "referral", "workflow", "diagnostic", "attestation",
                "medication", "coverage", "evidence"}
    assert clinical.isdisjoint(RULES["R08"]) and clinical <= HARD_PROHIBITIONS["R08"]
