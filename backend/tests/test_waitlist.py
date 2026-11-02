import datetime as dt
import json

import pytest

from foundation import dispatch, periodic
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from modules.comms import api as comms
from modules.waitlist import services as waitlist_services
from modules.waitlist.models import WaitEntry
from modules.waitlist.subscribers import register_subscribers
from tests.test_api import Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()
    register_subscribers()


class Waiting(Web):
    """A clinic with two patients and a Cardiology service that has no free times."""

    def __init__(self):
        super().__init__()
        self.patient("pat")
        self.patient("pat2")
        self.staff("desk")
        made = self.post("services", {
            "organization": self.clinic.org, "name": "Cardiology", "category": "cardiology",
            "duration_minutes": 30}, subject="desk")
        assert made.status_code == 201
        self.cardio = json.loads(made.content)["service"]["id"]
        self.next_hour = 9

    def add_times(self, count=1):
        first = f"2027-10-01T{self.next_hour:02d}:00:00"
        r = self.post(f"services/{self.cardio}/slots", {
            "location": self.clinic.location, "first_local": first, "count": count},
            subject="desk")
        assert r.status_code == 201, r.content
        self.next_hour += count        # the next batch starts later, so times never overlap

    def join(self, subject="pat", service=None):
        return self.post("waitlist", {"service": service or self.cardio}, subject=subject)

    def entries(self, subject="pat"):
        r = self.get("waitlist", subject=subject)
        assert r.status_code == 200
        return json.loads(r.content)["entries"]

    def free_slots(self):
        r = self.get(f"services/{self.cardio}/slots", subject="pat")
        return json.loads(r.content)["slots"]


@pytest.fixture
def w():
    return Waiting()


def deliver(passes=6):
    for _ in range(passes):
        relay_once(dispatch.dispatch)


def offers_in_inbox(w, subject="pat"):
    return [n for n in json.loads(w.get("notifications", subject=subject).content)["notifications"]
            if n["kind"] == "waitlist_offer"]


# ------------------------------------------------------------------ joining
def test_a_patient_joins_a_service_with_no_free_time_and_sees_it(w):
    r = w.join()
    assert r.status_code == 201
    entry = json.loads(r.content)["entry"]
    assert entry["state"] == "waiting" and entry["service"]["name"] == "Cardiology"
    assert [e["id"] for e in w.entries()] == [entry["id"]]
    assert w.entries("pat2") == []                     # other patients do not see it


def test_a_service_with_a_free_time_cannot_be_waited_for_just_book_it(w):
    w.add_times()
    assert w.join().status_code == 422


def test_joining_twice_is_refused_and_so_is_an_unknown_or_switched_off_service(w):
    assert w.join().status_code == 201
    assert w.join().status_code == 409
    assert w.join(subject="pat2", service="nosuchservice0000000000").status_code == 404
    w.post(f"services/{w.cardio}/status", {"status": "inactive"}, subject="desk")
    assert w.join(subject="pat2").status_code == 422


def test_staff_see_how_many_are_waiting_for_each_service(w):
    w.join("pat")
    w.join("pat2")
    r = w.get("services?all=1", subject="desk")
    waiting = {s["name"]: s["waiting"] for s in json.loads(r.content)["services"]}
    assert waiting["Cardiology"] == 2 and waiting["General check-up"] == 0


# ------------------------------------------------------------------ being offered a time
def test_a_new_time_is_offered_to_the_person_waiting_longest_only(w):
    w.join("pat")
    w.join("pat2")
    w.add_times(1)
    deliver()
    assert w.entries("pat")[0]["state"] == "offered"
    assert w.entries("pat2")[0]["state"] == "waiting"           # one time, one offer
    assert [n["title"] for n in offers_in_inbox(w, "pat")] == ["A time opened up"]
    assert offers_in_inbox(w, "pat2") == []
    w.add_times(1)
    deliver()
    assert w.entries("pat2")[0]["state"] == "offered"           # a second time, the second person
    assert len(offers_in_inbox(w, "pat")) == 1                  # nobody is told twice


def test_a_cancelled_booking_frees_a_time_for_someone_waiting(w):
    w.add_times(1)
    slot = w.free_slots()[0]["id"]
    booking = json.loads(book(w, slot, subject="pat").content)["booking"]["id"]
    assert w.join("pat2").status_code == 201                    # nothing free any more
    w.post(f"bookings/{booking}/cancel", subject="pat")
    deliver()
    assert w.entries("pat2")[0]["state"] == "offered"
    assert len(offers_in_inbox(w, "pat2")) == 1


def test_a_moved_appointment_frees_its_old_time_for_someone_waiting(w):
    w.add_times(2)
    first, second = [s["id"] for s in w.free_slots()]
    booking = json.loads(book(w, first, subject="pat").content)["booking"]["id"]
    book(w, second, subject="pat2")                             # now every time is taken
    w.patient("pat3")
    assert w.join("pat3").status_code == 201
    move = w.post(f"bookings/{booking}/reschedule", {"slot": second}, subject="desk")
    assert move.status_code == 409                              # taken: nothing moved, no offer
    w.add_times(1)
    third = w.free_slots()[0]["id"]
    moved = w.post(f"bookings/{booking}/reschedule", {"slot": third}, subject="desk")
    assert moved.status_code == 200
    deliver()
    assert w.entries("pat3")[0]["state"] == "offered"           # the vacated time was offered


def test_booking_the_service_closes_the_entry_and_a_stale_offer_is_not_announced(w):
    w.join("pat")
    w.add_times(1)
    slot = w.free_slots()[0]["id"]
    assert book(w, slot, subject="pat").status_code == 201       # booked before the worker ran
    deliver()
    assert w.entries("pat") == []                                # closed: no longer waiting
    assert offers_in_inbox(w, "pat") == []                       # and never told about it


# ------------------------------------------------------------------ leaving
def test_a_patient_can_leave_the_list_and_repeating_it_is_harmless(w):
    entry = json.loads(w.join().content)["entry"]["id"]
    assert w.post(f"waitlist/{entry}/leave", subject="pat").status_code == 200
    assert w.post(f"waitlist/{entry}/leave", subject="pat").status_code == 200
    assert w.entries() == []
    assert w.join().status_code == 201                           # and can join again


def test_nobody_else_can_remove_you_and_another_customer_cannot_see_the_entry(w):
    entry = json.loads(w.join().content)["entry"]["id"]
    assert w.post(f"waitlist/{entry}/leave", subject="pat2").status_code == 403
    outsider = Waiting()
    assert outsider.post(f"waitlist/{entry}/leave", subject="pat").status_code == 404
    assert w.entries()[0]["state"] == "waiting"


def test_someone_who_left_is_not_offered_a_time(w):
    entry = json.loads(w.join().content)["entry"]["id"]
    w.post(f"waitlist/{entry}/leave", subject="pat")
    w.add_times(1)
    deliver()
    assert offers_in_inbox(w) == []


# ------------------------------------------------------------------ an offer nobody uses
def test_an_unused_offer_goes_back_to_waiting_so_the_next_time_can_be_offered_again(w):
    w.join("pat")
    w.add_times(1)
    deliver()
    assert w.entries("pat")[0]["state"] == "offered"
    with tenant_transaction(w.clinic.ctx):
        assert waitlist_services.expire_old_offers(older_than=dt.timedelta(days=1)) == 0
        assert waitlist_services.expire_old_offers(older_than=dt.timedelta(seconds=0)) == 1
    assert w.entries("pat")[0]["state"] == "waiting"
    w.add_times(1)
    deliver()
    assert len(offers_in_inbox(w, "pat")) == 2                   # told again for the new time


def test_the_expiry_job_is_registered_and_the_event_carries_no_text(w):
    assert "waitlist.expire_offers" in periodic.registered()
    w.join()
    w.add_times(1)
    from foundation.models import OutboxEvent
    with tenant_transaction(w.clinic.ctx):
        payloads = [json.dumps(e.payload) for e in OutboxEvent.objects.filter(
            event_type__startswith=("slot.")) | OutboxEvent.objects.filter(
            event_type__startswith="waitlist.")]
    assert payloads and not any("Cardiology" in p for p in payloads)


def test_only_one_open_entry_per_patient_and_service_is_enforced_by_the_database(w):
    w.join()
    from django.db import IntegrityError, transaction
    with tenant_transaction(w.clinic.ctx):
        first = WaitEntry.objects.get(service_ref=w.cardio)
        with pytest.raises(IntegrityError), transaction.atomic():
            WaitEntry.objects.create(
                cell_id=first.cell_id, tenant_id=first.tenant_id, person_ref=first.person_ref,
                service_ref=first.service_ref, organization_ref=first.organization_ref,
                joined_by_ref="x")


# ------------------------------------------------------------------ the staff screen
def staff_list(w, subject="desk"):
    r = w.get("staff/waitlist", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["entries"]


def test_staff_see_who_is_waiting_in_queue_order_with_their_place(w):
    w.patient("pat3")
    first = json.loads(w.join("pat").content)["entry"]["id"]
    second = json.loads(w.join("pat2").content)["entry"]["id"]
    third = json.loads(w.join("pat3").content)["entry"]["id"]
    rows = staff_list(w)
    assert [r["id"] for r in rows] == [first, second, third]       # longest waiting first
    assert [r["place"] for r in rows] == [1, 2, 3]
    assert rows[0]["service"]["name"] == "Cardiology" and rows[0]["state"] == "waiting"


def test_the_place_counts_per_service_and_closed_entries_drop_out(w):
    other = json.loads(w.post("services", {
        "organization": w.clinic.org, "name": "Dermatology", "category": "dermatology",
        "duration_minutes": 20}, subject="desk").content)["service"]["id"]
    a = json.loads(w.join("pat").content)["entry"]["id"]
    w.join("pat", service=other)
    w.join("pat2")
    assert sorted((r["service"]["name"], r["place"]) for r in staff_list(w)) == [
        ("Cardiology", 1), ("Cardiology", 2), ("Dermatology", 1)]
    w.post(f"waitlist/{a}/leave", subject="pat")
    assert sorted((r["service"]["name"], r["place"]) for r in staff_list(w)) == [
        ("Cardiology", 1), ("Dermatology", 1)]                       # pat2 moved up


def test_only_clinic_staff_may_see_the_staff_list(w):
    w.join()
    doc = w.login("doc", "clinician", "R03")
    assert doc
    for who in ("pat", "doc"):
        assert w.get("staff/waitlist", subject=who).status_code == 403, who


def test_staff_can_remove_someone_from_the_list_and_it_is_recorded_as_theirs(w):
    entry = json.loads(w.join().content)["entry"]["id"]
    assert w.post(f"waitlist/{entry}/leave", subject="desk").status_code == 200
    assert staff_list(w) == [] and w.entries() == []
    from modules.evidence.models import AuditEvent
    with tenant_transaction(w.clinic.ctx):
        event = AuditEvent.objects.filter(action="waitlist.leave", object_ref=entry).get()
    assert event.actor_ref != ""                                  # the staff login, not "system"


def test_another_customers_staff_see_nothing_of_this_list(w):
    w.join()
    outsider = Waiting()
    assert staff_list(outsider) == []
