import datetime as dt
import threading

import pytest
from django.db import connection

from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import OutboxEvent
from foundation.timeutil import UTC, FixedClock
from modules.catalog import api as catalog
from modules.evidence.api import verify_chain
from modules.evidence.models import AuditEvent
from modules.policy.api import authorize, decide
from modules.policy.models import AccessDecision
from modules.scheduling import api as scheduling
from modules.scheduling.models import Booking
from modules.trust import api as trust
from tests.conftest import as_app_role
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

LONG_AGO = FixedClock(dt.datetime(2026, 1, 1, tzinfo=UTC))   # makes 2027 dates "the future"


def code_of(excinfo):
    return excinfo.value.code


class Clinic(World):
    """A tenant with one clinic offering one 30-minute service in Asia/Kolkata."""

    def __init__(self):
        super().__init__()
        self.staff = self.person("R04")          # a clinic-operations login
        self.cat = self.rights()                 # its receipt for changing this clinic's catalog
        with tenant_transaction(self.ctx):
            self.location = catalog.create_location(
                organization_ref=self.org, name="Main", time_zone="Asia/Kolkata",
                **self.cat)["public_id"]
            self.service = catalog.create_service(
                organization_ref=self.org, name="General check-up", category="general_physician",
                duration_minutes=30, **self.cat)["public_id"]

    def rights(self, org=None, actor=None):
        """Authorize a staff login for write:catalog (outside a transaction, so it is recorded)
        and return the keyword arguments the catalog functions need."""
        actor = actor or self.staff
        with bind_context(self.ctx):
            decision = authorize(
                actor_public_id=actor, operation="write:catalog", purpose_code="OPERATIONS",
                organization_public_id=org or self.org)
        return {"actor_public_id": actor, "decision": decision}

    def slots(self, count=3, local=dt.datetime(2027, 6, 15, 10, 0), **kw):
        with tenant_transaction(self.ctx):
            return scheduling.create_slots(
                service_ref=self.service, location_ref=self.location, first_local=local,
                count=count, clock=LONG_AGO, **self.cat, **kw)

    def patient(self, subject="pat"):
        """A patient login that owns a person record."""
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(
                actor_type=trust.ActorType.PATIENT, issuer="idp", subject=subject)
            trust.assign_role(actor_public_id=actor.public_id, organization_public_id=self.org,
                              role_code="R01", by="system")
            person = trust.create_person()
            trust.link_person_to_actor(person.public_id, actor.public_id, by="system")
        return actor.public_id, person.public_id

    def allow(self, actor, purpose, slot_org=None, person=None, op="write:booking"):
        with bind_context(self.ctx):
            return authorize(actor_public_id=actor, operation=op, purpose_code=purpose,
                             organization_public_id=slot_org or self.org, subject_ref=person)

    def book(self, actor, person, slot, purpose="SELF_SERVICE"):
        decision = self.allow(actor, purpose, person=person)
        with tenant_transaction(self.ctx):
            return scheduling.book(slot_ref=slot, person_ref=person, actor_public_id=actor,
                                   decision=decision)


@pytest.fixture
def clinic():
    return Clinic()


# ------------------------------------------------------------------ catalog
def test_catalog_validates_and_lists(clinic):
    with tenant_transaction(clinic.ctx):
        for bad in ({"time_zone": "Mars/Base"}, {"name": " "}):
            args = {"organization_ref": clinic.org, "name": "X", "time_zone": "UTC", **bad,
                    **clinic.cat}
            with pytest.raises(ZoikoError) as e:
                catalog.create_location(**args)
            assert code_of(e) == ErrorCode.VALIDATION_FAILED
        with pytest.raises(ZoikoError) as dup:
            catalog.create_location(
                organization_ref=clinic.org, name="Main", time_zone="UTC", **clinic.cat)
        assert code_of(dup) == ErrorCode.CONFLICT_VERSION
        for bad in ({"duration_minutes": 2}, {"duration_minutes": 999}, {"category": "Bad Cat"},
                    {"mode": "teleport"}, {"name": ""}):
            args = {"organization_ref": clinic.org, "name": "Y", "category": "dermatology",
                    "duration_minutes": 20, **bad, **clinic.cat}
            with pytest.raises(ZoikoError):
                catalog.create_service(**args)
        catalog.create_service(organization_ref=clinic.org, name="Skin review",
                               category="dermatology", duration_minutes=20, mode="video",
                               **clinic.cat)
        assert [s["name"] for s in catalog.list_services()] == ["General check-up", "Skin review"]
        assert [s["name"] for s in catalog.list_services(query="skin")] == ["Skin review"]
        assert [s["name"] for s in catalog.list_services(query="general physician")] == [
            "General check-up"]
        catalog.set_service_status(clinic.service, "inactive", **clinic.cat)
        assert [s["name"] for s in catalog.list_services()] == ["Skin review"]
        assert len(catalog.list_services(active_only=False)) == 2


def test_catalog_does_not_cross_tenants(clinic):
    other = Clinic()
    with tenant_transaction(other.ctx):
        assert [s["public_id"] for s in catalog.list_services()] == [other.service]
        with pytest.raises(ZoikoError) as e:
            catalog.get_service(clinic.service)
        assert code_of(e) == ErrorCode.NOT_FOUND
        with pytest.raises(ZoikoError):                      # an organization of another tenant
            catalog.create_location(
                organization_ref=clinic.org, name="Z", time_zone="UTC", **other.cat)


# ------------------------------------------------------------------ slots and time
def test_slots_are_built_from_the_clinics_wall_clock_and_stored_in_utc(clinic):
    slots = clinic.slots(count=3)
    assert [s["starts_at"] for s in slots] == [
        "2027-06-15T04:30:00+00:00", "2027-06-15T05:00:00+00:00", "2027-06-15T05:30:00+00:00"]
    assert slots[0]["ends_at"] == "2027-06-15T05:00:00+00:00"          # 30-minute service
    assert slots[0]["local_start"] == "2027-06-15T10:00:00"
    assert slots[0]["local_zone"] == "Asia/Kolkata"


def test_impossible_and_repeated_clinic_times_are_refused_not_guessed():
    clinic = Clinic()
    with tenant_transaction(clinic.ctx):
        london = catalog.create_location(
            organization_ref=clinic.org, name="London", time_zone="Europe/London",
            **clinic.cat)["public_id"]
        for local in (dt.datetime(2027, 3, 28, 1, 30), dt.datetime(2027, 10, 31, 1, 30)):
            with pytest.raises(ZoikoError) as e:               # gap, then repeated hour
                scheduling.create_slots(service_ref=clinic.service, location_ref=london,
                                        first_local=local, count=1, clock=LONG_AGO,
                                        **clinic.cat)
            assert code_of(e) == ErrorCode.VALIDATION_FAILED


def test_slot_rules_all_or_nothing_no_past_no_duplicates_no_mismatch(clinic):
    with tenant_transaction(clinic.ctx):
        other_org = trust.create_organization(name="Elsewhere").public_id
    far_rights = clinic.rights(org=other_org, actor=clinic.person("R04", org=other_org))
    with tenant_transaction(clinic.ctx):
        with pytest.raises(ZoikoError):                           # in the past
            scheduling.create_slots(service_ref=clinic.service, location_ref=clinic.location,
                                    first_local=dt.datetime(2020, 1, 1, 9, 0), count=1,
                                    **clinic.cat)
        for count in (0, 201):
            with pytest.raises(ZoikoError):
                scheduling.create_slots(service_ref=clinic.service, location_ref=clinic.location,
                                        first_local=dt.datetime(2027, 6, 15, 9, 0), count=count,
                                        clock=LONG_AGO, **clinic.cat)
        far = catalog.create_location(
            organization_ref=other_org, name="Far", time_zone="UTC", **far_rights)["public_id"]
        with pytest.raises(ZoikoError) as e:                       # location of another org
            scheduling.create_slots(service_ref=clinic.service, location_ref=far,
                                    first_local=dt.datetime(2027, 6, 15, 9, 0), count=1,
                                    clock=LONG_AGO, **clinic.cat)
        assert code_of(e) == ErrorCode.VALIDATION_FAILED
    clinic.slots(count=1, local=dt.datetime(2027, 6, 15, 10, 30))
    with tenant_transaction(clinic.ctx), pytest.raises(ZoikoError) as dup:
        scheduling.create_slots(service_ref=clinic.service, location_ref=clinic.location,
                                first_local=dt.datetime(2027, 6, 15, 10, 0), count=3,
                                clock=LONG_AGO, **clinic.cat)  # 11:00 fine, 10:30 clashes
    assert code_of(dup) == ErrorCode.CONFLICT_VERSION
    with tenant_transaction(clinic.ctx):
        assert len(scheduling.list_open_slots(clinic.service, clock=LONG_AGO)) == 1  # none added


# ------------------------------------------------------------------ booking
def test_a_patient_books_an_open_slot_and_it_leaves_the_open_list(clinic):
    slots = clinic.slots(count=2)
    actor, person = clinic.patient()
    booking = clinic.book(actor, person, slots[0]["public_id"])
    assert (booking["status"], booking["person_ref"], booking["booked_by_ref"]) == (
        "requested", person, actor)
    with tenant_transaction(clinic.ctx):
        open_now = scheduling.list_open_slots(clinic.service, clock=LONG_AGO)
    assert [s["public_id"] for s in open_now] == [slots[1]["public_id"]]


def test_the_same_slot_cannot_be_booked_twice(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    a1, p1 = clinic.patient("one")
    a2, p2 = clinic.patient("two")
    clinic.book(a1, p1, slot)
    with pytest.raises(ZoikoError) as e:
        clinic.book(a2, p2, slot)
    assert code_of(e) == ErrorCode.CONFLICT_VERSION


def test_two_people_booking_at_the_same_moment_exactly_one_wins(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    people = [clinic.patient(f"p{i}") for i in range(4)]
    outcomes, barrier = [], threading.Barrier(len(people))

    def attempt(actor, person):
        try:
            barrier.wait(10)
            clinic.book(actor, person, slot)
            outcomes.append("won")
        except ZoikoError as exc:
            outcomes.append(str(exc.code))
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=p) for p in people]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert outcomes.count("won") == 1 and len(outcomes) == 4
    with tenant_transaction(clinic.ctx):
        assert Booking.objects.filter(status="requested").count() == 1


def test_a_denial_is_recorded_even_though_the_request_fails(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    actor, _ = clinic.patient("mine")
    _, someone_elses = clinic.patient("theirs")
    with pytest.raises(ZoikoError) as e:
        clinic.book(actor, someone_elses, slot)
    assert code_of(e) == ErrorCode.SUBJECT_LINK_UNVERIFIED
    with tenant_transaction(clinic.ctx):
        row = AccessDecision.objects.get(actor_ref=actor, allowed=False)
        assert row.reason_code == "RELATIONSHIP_UNVERIFIED"
        assert AuditEvent.objects.filter(action="access.decide", outcome="denied").exists()
        assert Booking.objects.count() == 0


def test_booking_without_a_valid_receipt_is_refused(clinic):
    slots = clinic.slots(count=2)
    actor, person = clinic.patient()
    _, other_person = clinic.patient("other")
    good = clinic.allow(actor, "SELF_SERVICE", person=person)
    with tenant_transaction(clinic.ctx):
        denied = decide(actor_public_id=actor, operation="write:booking",
                        purpose_code="SELF_SERVICE", organization_public_id=clinic.org,
                        subject_ref=other_person)                  # a denial, not an allow
        forged = type(good)(True, good.reason, "x" * 22, "R01")
        for bad in (denied, forged):
            with pytest.raises(ZoikoError) as e:
                scheduling.book(slot_ref=slots[0]["public_id"], person_ref=person,
                                actor_public_id=actor, decision=bad)
            assert code_of(e) == ErrorCode.POLICY_DENIED
        with pytest.raises(ZoikoError):                           # receipt is for another patient
            scheduling.book(slot_ref=slots[0]["public_id"], person_ref=other_person,
                            actor_public_id=actor, decision=good)
        with pytest.raises(ZoikoError):                           # receipt is for another actor
            scheduling.book(slot_ref=slots[0]["public_id"], person_ref=person,
                            actor_public_id="someone_else", decision=good)
        assert Booking.objects.count() == 0


def test_booking_a_slot_that_already_started_is_refused(clinic):
    with tenant_transaction(clinic.ctx):
        past = scheduling.create_slots(
            service_ref=clinic.service, location_ref=clinic.location,
            first_local=dt.datetime(2025, 5, 1, 10, 0), count=1,
            clock=FixedClock(dt.datetime(2020, 1, 1, tzinfo=UTC)), **clinic.cat)[0]["public_id"]
    actor, person = clinic.patient()
    with pytest.raises(ZoikoError) as e:
        clinic.book(actor, person, past)
    assert code_of(e) == ErrorCode.VALIDATION_FAILED


def test_the_employer_cannot_book_and_a_guardian_can_book_for_a_child(clinic):
    slots = clinic.slots(count=2)
    with tenant_transaction(clinic.ctx):
        child = trust.create_person().public_id
    employer = clinic.person("R08")
    with pytest.raises(ZoikoError) as e:
        clinic.allow(employer, "PROGRAM_ADMIN", person=child)
    assert code_of(e) == ErrorCode.POLICY_DENIED
    guardian, approver = clinic.person("R02"), clinic.person("R04")
    with pytest.raises(ZoikoError):                               # no proxy yet
        clinic.book(guardian, child, slots[0]["public_id"])
    with tenant_transaction(clinic.ctx):
        grant = trust.propose_proxy_grant(
            delegate_public_id=guardian, person_public_id=child,
            relationship_type="parent", by="system")
        trust.approve_proxy_grant(grant.public_id, approver)
    booking = clinic.book(guardian, child, slots[0]["public_id"])
    assert booking["person_ref"] == child and booking["booked_by_ref"] == guardian


# ------------------------------------------------------------------ confirm and cancel
def test_staff_confirm_patients_cannot_and_repeating_is_harmless(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    actor, person = clinic.patient()
    booking = clinic.book(actor, person, slot)["public_id"]
    staff = clinic.person("R04")

    own = clinic.allow(actor, "SELF_SERVICE", person=person)
    with tenant_transaction(clinic.ctx), pytest.raises(ZoikoError) as e:
        scheduling.confirm(booking_ref=booking, actor_public_id=actor, decision=own)
    assert code_of(e) == ErrorCode.POLICY_DENIED     # the right to book is not the right to confirm

    ok = clinic.allow(staff, "OPERATIONS", person=person)
    with tenant_transaction(clinic.ctx):
        assert scheduling.confirm(booking_ref=booking, actor_public_id=staff,
                                  decision=ok)["status"] == "confirmed"
        assert scheduling.confirm(booking_ref=booking, actor_public_id=staff,
                                  decision=ok)["status"] == "confirmed"


def test_cancelling_frees_the_slot_and_a_cancelled_booking_cannot_be_confirmed(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    actor, person = clinic.patient()
    booking = clinic.book(actor, person, slot)["public_id"]
    staff = clinic.person("R04")
    own = clinic.allow(actor, "SELF_SERVICE", person=person)
    with tenant_transaction(clinic.ctx):
        assert scheduling.cancel(booking_ref=booking, actor_public_id=actor,
                                 decision=own)["status"] == "cancelled"
        assert scheduling.cancel(booking_ref=booking, actor_public_id=actor,
                                 decision=own)["status"] == "cancelled"        # harmless
        assert len(scheduling.list_open_slots(clinic.service, clock=LONG_AGO)) == 1
    ok = clinic.allow(staff, "OPERATIONS", person=person)
    with tenant_transaction(clinic.ctx), pytest.raises(ZoikoError) as e:
        scheduling.confirm(booking_ref=booking, actor_public_id=staff, decision=ok)
    assert code_of(e) == ErrorCode.CONFLICT_VERSION
    again, p2 = clinic.patient("second")                             # the freed slot is bookable
    assert clinic.book(again, p2, slot)["status"] == "requested"


def test_a_patient_cannot_cancel_someone_elses_booking(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    a1, p1 = clinic.patient("one")
    a2, _ = clinic.patient("two")
    booking = clinic.book(a1, p1, slot)["public_id"]
    with pytest.raises(ZoikoError) as e:
        clinic.allow(a2, "SELF_SERVICE", person=p1)               # not their record
    assert code_of(e) == ErrorCode.SUBJECT_LINK_UNVERIFIED
    with tenant_transaction(clinic.ctx):
        assert scheduling.get_booking(booking)["status"] == "requested"


# ------------------------------------------------------------------ records and fences
def test_events_audit_and_listing(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    actor, person = clinic.patient()
    booking = clinic.book(actor, person, slot)["public_id"]
    staff = clinic.person("R04")
    ok = clinic.allow(staff, "OPERATIONS", person=person)
    with tenant_transaction(clinic.ctx):
        scheduling.confirm(booking_ref=booking, actor_public_id=staff, decision=ok)
        types = list(OutboxEvent.objects.order_by("seq").values_list("event_type", flat=True))
        assert types == ["booking.requested.v1", "booking.confirmed.v1"]
        payload = OutboxEvent.objects.order_by("seq").last().payload
        assert payload["booking_ref"] == booking and "person" not in str(payload.keys())
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        assert {"slot.create", "booking.request", "booking.confirm"} <= actions
        assert verify_chain().ok
        mine = scheduling.list_bookings(person_refs=[person])
        assert [b["public_id"] for b in mine] == [booking]
        assert len(scheduling.list_bookings(organization_ref=clinic.org)) == 1
        assert scheduling.list_bookings() == []
        assert scheduling.list_bookings(person_refs=["nobody"]) == []


def test_nothing_crosses_tenants_and_the_app_role_works(clinic):
    slot = clinic.slots(count=1)[0]["public_id"]
    actor, person = clinic.patient()
    clinic.book(actor, person, slot)
    other = Clinic()
    with tenant_transaction(other.ctx):
        assert scheduling.list_bookings(person_refs=[person]) == []
        assert scheduling.list_open_slots(clinic.service, clock=LONG_AGO) == []
    with tenant_transaction(clinic.ctx), as_app_role():
        created = scheduling.create_slots(
            service_ref=clinic.service, location_ref=clinic.location,
            first_local=dt.datetime(2027, 7, 1, 9, 0), count=2, clock=LONG_AGO, **clinic.cat)
        assert len(created) == 2
        assert len(scheduling.list_bookings(person_refs=[person])) == 1


# ------------------------------------------------------------------ catalog writes need a receipt
def test_catalog_writes_are_refused_without_a_valid_receipt(clinic):
    patient, person = clinic.patient()
    other_org_staff = None
    with tenant_transaction(clinic.ctx):
        other_org = trust.create_organization(name="Elsewhere").public_id
    other_org_staff = clinic.person("R04", org=other_org)
    wrong_org = clinic.rights(org=other_org, actor=other_org_staff)       # a real ALLOW, wrong org
    booking_receipt = clinic.allow(patient, "SELF_SERVICE", person=person)  # a real ALLOW, wrong op
    good = clinic.cat["decision"]
    with tenant_transaction(clinic.ctx):
        denied = decide(actor_public_id=patient, operation="write:catalog",
                        purpose_code="SELF_SERVICE", organization_public_id=clinic.org)
        forged = type(good)(True, good.reason, "x" * 22, "R04")
        bad_receipts = [
            {"actor_public_id": clinic.staff, "decision": denied},          # a refusal
            {"actor_public_id": clinic.staff, "decision": forged},          # made up
            {"actor_public_id": clinic.staff, "decision": wrong_org["decision"]},
            {"actor_public_id": clinic.staff, "decision": booking_receipt},
            {"actor_public_id": patient, "decision": good},                 # someone else's
        ]
        for bad in bad_receipts:
            with pytest.raises(ZoikoError) as e1:
                catalog.create_location(organization_ref=clinic.org, name="Nope",
                                        time_zone="UTC", **bad)
            with pytest.raises(ZoikoError) as e2:
                catalog.create_service(organization_ref=clinic.org, name="Nope",
                                       category="dental", duration_minutes=20, **bad)
            with pytest.raises(ZoikoError) as e3:
                catalog.set_service_status(clinic.service, "inactive", **bad)
            with pytest.raises(ZoikoError) as e4:
                scheduling.create_slots(service_ref=clinic.service, location_ref=clinic.location,
                                        first_local=dt.datetime(2027, 9, 1, 9, 0), count=1,
                                        clock=LONG_AGO, **bad)
            assert {code_of(e) for e in (e1, e2, e3, e4)} == {ErrorCode.POLICY_DENIED}
        names = [s["name"] for s in catalog.list_services(active_only=False)]
        assert names == ["General check-up"]                              # nothing slipped in
        assert [loc["name"] for loc in catalog.list_locations()] == ["Main"]
        assert scheduling.list_open_slots(clinic.service, clock=LONG_AGO) == []


def test_a_patient_and_the_employer_cannot_even_get_a_catalog_receipt(clinic):
    patient, _ = clinic.patient()
    employer = clinic.person("R08")
    for who, purpose in ((patient, "SELF_SERVICE"), (employer, "PROGRAM_ADMIN")):
        with pytest.raises(ZoikoError) as e:
            clinic.allow(who, purpose, op="write:catalog")
        assert code_of(e) == ErrorCode.POLICY_DENIED
