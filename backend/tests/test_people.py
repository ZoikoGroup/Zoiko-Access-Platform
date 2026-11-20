import datetime as dt

import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import utc_now
from modules.evidence.api import verify_chain
from modules.evidence.models import AuditEvent
from modules.policy.api import Reason, decide
from modules.trust import api as trust
from modules.trust.models import CareRelationship, Person, ProxyGrant
from tests.conftest import as_app_role
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture
def world():
    return World()


def patient_actor(world, subject="pat"):
    with tenant_transaction(world.ctx):
        actor = trust.register_actor(
            actor_type=trust.ActorType.PATIENT, issuer="idp", subject=subject)
        trust.assign_role(actor_public_id=actor.public_id,
                          organization_public_id=world.org, role_code="R01", by="system")
    return actor.public_id


def new_person(world):
    with tenant_transaction(world.ctx):
        return trust.create_person().public_id


def verified(world, actor, condition, person, **kw):
    with tenant_transaction(world.ctx):
        return trust.relationship_verified(
            actor_public_id=actor, condition=condition, subject_ref=person, **kw)


def code_of(excinfo):
    return excinfo.value.code


# ------------------------------------------------------------------ own record
def test_a_patient_is_linked_to_their_own_login_and_only_that_one(world):
    me, person, other = patient_actor(world), new_person(world), new_person(world)
    with tenant_transaction(world.ctx):
        trust.link_person_to_actor(person, me, by="system")
    assert verified(world, me, "OWN", person) is True
    assert verified(world, me, "OWN", other) is False


def test_linking_rules(world):
    me, person, other = patient_actor(world), new_person(world), new_person(world)
    clinician = world.person("R03")
    with tenant_transaction(world.ctx):
        with pytest.raises(ZoikoError) as e1:
            trust.link_person_to_actor(person, clinician, by="system")   # not a patient login
        assert code_of(e1) == ErrorCode.VALIDATION_FAILED
        trust.link_person_to_actor(person, me, by="system")
        with pytest.raises(ZoikoError) as e2:
            trust.link_person_to_actor(person, me, by="system")          # person already linked
        assert code_of(e2) == ErrorCode.CONFLICT_VERSION
        with pytest.raises(ZoikoError) as e3:
            trust.link_person_to_actor(other, me, by="system")           # login already used
        assert code_of(e3) == ErrorCode.CONFLICT_VERSION


def test_a_patient_can_read_their_own_record_through_the_real_policy_engine(world):
    me, person, other = patient_actor(world), new_person(world), new_person(world)
    with tenant_transaction(world.ctx):
        trust.link_person_to_actor(person, me, by="system")
    mine = world.ask(me, "read:person", "SELF_SERVICE", subject_ref=person)
    assert (mine.allowed, mine.role_code) == (True, "R01")
    theirs = world.ask(me, "read:person", "SELF_SERVICE", subject_ref=other)
    assert (theirs.allowed, theirs.reason) == (False, Reason.RELATIONSHIP_UNVERIFIED)


# ------------------------------------------------------------------ care relationships
def test_a_clinician_with_a_care_relationship_may_read_that_patient_only(world):
    doctor, person, other = world.person("R03"), new_person(world), new_person(world)
    before = world.ask(doctor, "read:person", "DIRECT_CARE", subject_ref=person)
    assert before.reason == Reason.RELATIONSHIP_UNVERIFIED
    with tenant_transaction(world.ctx):
        trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.REFERRAL, by="system")
    assert world.ask(doctor, "read:person", "DIRECT_CARE", subject_ref=person).allowed
    assert not world.ask(doctor, "read:person", "DIRECT_CARE", subject_ref=other).allowed


def test_only_people_with_a_care_role_can_be_given_a_care_relationship(world):
    clerk, person = world.person("R04"), new_person(world)
    with tenant_transaction(world.ctx):
        with pytest.raises(ZoikoError) as excinfo:
            trust.start_care_relationship(
                actor_public_id=clerk, person_public_id=person,
                basis=trust.CareBasis.APPOINTMENT, by="system")
    assert code_of(excinfo) == ErrorCode.POLICY_DENIED


def test_an_ended_care_relationship_stops_working_and_ending_twice_is_harmless(world):
    doctor, person = world.person("R03"), new_person(world)
    with tenant_transaction(world.ctx):
        link = trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.CARE_TEAM, by="system")
    assert verified(world, doctor, "REL", person) is True
    with tenant_transaction(world.ctx):
        trust.end_care_relationship(link.public_id, by="system")
        trust.end_care_relationship(link.public_id, by="system")
    assert verified(world, doctor, "REL", person) is False
    with tenant_transaction(world.ctx):
        assert CareRelationship.objects.get(pk=link.pk).entity_version == 2


def test_a_care_relationship_expires_on_its_end_date(world):
    doctor, person = world.person("R03"), new_person(world)
    until = utc_now() + dt.timedelta(days=7)
    with tenant_transaction(world.ctx):
        trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person, basis=trust.CareBasis.REFERRAL,
            valid_until=until, by="system")
        with pytest.raises(ZoikoError):                       # end date in the past
            trust.start_care_relationship(
                actor_public_id=doctor, person_public_id=person, basis=trust.CareBasis.REFERRAL,
                valid_until=utc_now() - dt.timedelta(days=1), by="system")
    assert verified(world, doctor, "REL", person) is True
    assert verified(world, doctor, "REL", person, at=until + dt.timedelta(seconds=1)) is False
    assert verified(world, doctor, "REL", person, at=utc_now() - dt.timedelta(days=1)) is False


def test_a_contested_care_relationship_grants_nothing_until_cleared(world):
    doctor, person = world.person("R03"), new_person(world)
    with tenant_transaction(world.ctx):
        link = trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.CARE_TEAM, by="system")
        trust.set_care_contested(link.public_id, True, by="system")
    assert not world.ask(doctor, "read:person", "DIRECT_CARE", subject_ref=person).allowed
    with tenant_transaction(world.ctx):
        trust.set_care_contested(link.public_id, False, by="system")
    assert world.ask(doctor, "read:person", "DIRECT_CARE", subject_ref=person).allowed


def test_losing_the_care_role_or_being_suspended_closes_the_door_even_with_a_link(world):
    doctor, person = world.person("R03"), new_person(world)
    with tenant_transaction(world.ctx):
        trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.CARE_TEAM, by="system")
        trust.set_actor_status(doctor, trust.Status.SUSPENDED, by="system")
    assert not world.ask(doctor, "read:person", "DIRECT_CARE", subject_ref=person).allowed
    assert verified(world, doctor, "REL", person) is False


# ------------------------------------------------------------------ proxy grants
def test_a_proxy_works_only_after_someone_else_approves_it_and_not_after_revoking(world):
    guardian, approver = world.person("R02"), world.person("R04")
    person = new_person(world)
    with tenant_transaction(world.ctx):
        grant = trust.propose_proxy_grant(
            delegate_public_id=guardian, person_public_id=person,
            relationship_type="parent", by="system")
    assert not world.ask(guardian, "read:person", "SELF_SERVICE", subject_ref=person).allowed
    with tenant_transaction(world.ctx):
        with pytest.raises(ZoikoError) as e1:                  # cannot approve your own grant
            trust.approve_proxy_grant(grant.public_id, guardian)
        assert code_of(e1) == ErrorCode.POLICY_DENIED
        trust.approve_proxy_grant(grant.public_id, approver)
        with pytest.raises(ZoikoError) as e2:                  # already approved
            trust.approve_proxy_grant(grant.public_id, approver)
        assert code_of(e2) == ErrorCode.CONFLICT_VERSION
    assert world.ask(guardian, "read:person", "SELF_SERVICE", subject_ref=person).allowed
    with tenant_transaction(world.ctx):
        trust.revoke_proxy_grant(grant.public_id, by="system")
        trust.revoke_proxy_grant(grant.public_id, by="system")  # harmless
    assert not world.ask(guardian, "read:person", "SELF_SERVICE", subject_ref=person).allowed
    with tenant_transaction(world.ctx):
        assert ProxyGrant.objects.get(pk=grant.pk).status == "revoked"


def test_a_proxy_for_yourself_and_blank_types_are_refused(world):
    me, person = patient_actor(world), new_person(world)
    with tenant_transaction(world.ctx):
        trust.link_person_to_actor(person, me, by="system")
        with pytest.raises(ZoikoError):
            trust.propose_proxy_grant(delegate_public_id=me, person_public_id=person,
                                      relationship_type="self", by="system")
        with pytest.raises(ZoikoError):
            trust.propose_proxy_grant(delegate_public_id=me, person_public_id=person,
                                      relationship_type="  ", by="system")


def test_a_proxy_grant_can_expire(world):
    guardian, approver = world.person("R02"), world.person("R04")
    person = new_person(world)
    until = utc_now() + dt.timedelta(days=1)
    with tenant_transaction(world.ctx):
        grant = trust.propose_proxy_grant(
            delegate_public_id=guardian, person_public_id=person,
            relationship_type="caregiver", by="system")
        trust.approve_proxy_grant(grant.public_id, approver, valid_until=until)
    assert verified(world, guardian, "PROXY", person) is True
    assert verified(world, guardian, "PROXY", person, at=until + dt.timedelta(seconds=1)) is False


# ------------------------------------------------------------------ fences and records
def test_nothing_crosses_tenants_and_unknown_inputs_are_simply_false(world):
    other = World()
    doctor, person = other.person("R03"), new_person(other)
    with tenant_transaction(other.ctx):
        trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.CARE_TEAM, by="system")
    assert verified(other, doctor, "REL", person) is True
    assert verified(world, doctor, "REL", person) is False        # asked from the wrong tenant
    assert verified(world, "nobody", "REL", person) is False
    assert verified(world, doctor, "WEIRD", person) is False
    with tenant_transaction(world.ctx):
        with pytest.raises(ZoikoError) as excinfo:
            trust.end_care_relationship("nope", by="system")
    assert code_of(excinfo) == ErrorCode.NOT_FOUND


def test_everything_is_audited_and_the_chain_stays_intact(world):
    doctor, person = world.person("R03"), new_person(world)
    with tenant_transaction(world.ctx):
        trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.CARE_TEAM, by="system")
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        assert {"person.create", "care.start"} <= actions
        assert verify_chain().ok


def test_the_restricted_app_role_works_and_sees_only_its_own_tenant(world):
    doctor = world.person("R03")
    with tenant_transaction(world.ctx), as_app_role():
        person = trust.create_person().public_id
        trust.start_care_relationship(
            actor_public_id=doctor, person_public_id=person,
            basis=trust.CareBasis.REFERRAL, by=doctor)
        assert decide(actor_public_id=doctor, operation="read:person",
                      purpose_code="DIRECT_CARE", subject_ref=person).allowed
        assert Person.objects.count() == 1
    other = World()
    with tenant_transaction(other.ctx), as_app_role():
        assert Person.objects.count() == 0 and CareRelationship.objects.count() == 0
