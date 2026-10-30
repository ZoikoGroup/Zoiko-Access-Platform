import datetime as dt
import json

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from modules.trust.models import CareRelationship
from tests.test_api import API, Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Practice(Web):
    """A clinic with a receptionist, two doctors and a patient."""

    def __init__(self):
        super().__init__()
        self.desk = self.staff("ops")
        self.doc = self.login("doc", trust.ActorType.CLINICIAN, "R03")
        self.doc2 = self.login("doc2", trust.ActorType.CLINICIAN, "R03")
        self.patient_actor, self.person = self.patient("pat")

    def add_times(self, provider, first="2027-09-01T09:00:00", count=2, subject="ops"):
        return self.post(
            f"services/{self.clinic.service}/slots",
            {"location": self.clinic.location, "first_local": first, "count": count,
             "provider": provider}, subject=subject)

    def open_slots(self):
        r = self.get(f"services/{self.clinic.service}/slots", subject="pat")
        return json.loads(r.content)["slots"]

    def slot_of(self, provider_name, index=0):
        return [s for s in self.open_slots()
                if s["provider"] and s["provider"]["name"] == provider_name][index]["id"]

    def schedule(self, subject="doc"):
        r = self.get("clinician/schedule", subject=subject)
        assert r.status_code == 200
        return json.loads(r.content)["appointments"]

    def my_patients(self, subject="doc"):
        r = self.get("clinician/patients", subject=subject)
        assert r.status_code == 200
        return json.loads(r.content)["patients"]


@pytest.fixture
def practice():
    return Practice()


# ------------------------------------------------------------------ assigning a doctor to times
def test_staff_can_assign_a_real_doctor_to_times_and_patients_see_who(practice):
    assert practice.add_times(practice.doc).status_code == 201
    slots = [s for s in practice.open_slots() if s["provider"]]
    assert len(slots) == 2 and slots[0]["provider"] == {"id": practice.doc, "name": "doc"}
    assert [s for s in practice.open_slots() if not s["provider"]]   # the earlier times: none


def test_only_active_doctors_of_this_organization_can_be_assigned(practice):
    other = World_other_doctor(practice)
    for bad in (practice.patient_actor, practice.desk, "nobody", other):
        r = practice.add_times(bad)
        assert r.status_code == 422, bad
    assert [s for s in practice.open_slots() if s["provider"]] == []


def World_other_doctor(practice):
    """A doctor who belongs to a DIFFERENT customer."""
    from tests.test_policy import World
    other = World()
    return other.person("R03")


def test_two_doctors_can_share_a_time_but_one_doctor_cannot_be_in_two_places(practice):
    assert practice.add_times(practice.doc).status_code == 201
    assert practice.add_times(practice.doc2).status_code == 201         # same service, place, time
    clash = practice.add_times(practice.doc, first="2027-09-01T09:00:00", count=1)
    assert clash.status_code == 409
    other_place = practice.post(
        f"services/{practice.clinic.service}/slots",
        {"location": practice.clinic.location, "first_local": "2027-09-01T09:00:00", "count": 1,
         "provider": practice.doc}, subject="ops")
    assert other_place.status_code == 409


# ------------------------------------------------------------------ the doctor's view
def test_a_doctor_sees_a_patient_only_after_the_booking_is_confirmed(practice):
    practice.add_times(practice.doc)
    slot = practice.slot_of("doc")
    booking = book(practice, slot, subject="pat").json()["booking"]
    assert booking["provider"] == {"id": practice.doc, "name": "doc"}
    assert practice.schedule() == [] and practice.my_patients() == []     # not confirmed yet
    done = practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    assert done.status_code == 200
    appointments = practice.schedule()
    assert [a["id"] for a in appointments] == [booking["id"]]
    assert appointments[0]["person"] == practice.person
    assert appointments[0]["service"]["name"] == "General check-up"
    patients = practice.my_patients()
    assert [p["id"] for p in patients] == [practice.person]
    assert patients[0]["basis"] == "appointment" and patients[0]["appointments"] == 1


def test_the_care_link_lasts_until_thirty_days_after_the_visit(practice):
    practice.add_times(practice.doc)
    booking = book(practice, practice.slot_of("doc"), subject="pat").json()["booking"]
    practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    visit_ends = dt.datetime.fromisoformat(booking["ends_at"])
    with tenant_transaction(practice.clinic.ctx):
        link = CareRelationship.objects.get(actor__public_id=practice.doc)
        assert link.valid_until == visit_ends + dt.timedelta(days=30)
        assert link.status == "active" and link.contested is False


def test_other_doctors_and_other_patients_stay_invisible(practice):
    practice.add_times(practice.doc)
    booking = book(practice, practice.slot_of("doc"), subject="pat").json()["booking"]
    practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    assert practice.schedule("doc2") == [] and practice.my_patients("doc2") == []
    practice.patient("other.patient")                                     # never booked with doc
    assert [p["id"] for p in practice.my_patients()] == [practice.person]


def test_a_second_visit_reuses_the_care_link_and_extends_it(practice):
    practice.add_times(practice.doc, count=3)
    first = book(practice, practice.slot_of("doc", 0), subject="pat").json()["booking"]
    second = book(practice, practice.slot_of("doc", 1), subject="pat").json()["booking"]
    practice.post(f"bookings/{first['id']}/confirm", subject="ops")
    practice.post(f"bookings/{second['id']}/confirm", subject="ops")
    with tenant_transaction(practice.clinic.ctx):
        links = list(CareRelationship.objects.filter(actor__public_id=practice.doc))
    assert len(links) == 1
    later_visit = dt.datetime.fromisoformat(second["ends_at"])
    assert links[0].valid_until == later_visit + dt.timedelta(days=30)
    assert [p["appointments"] for p in practice.my_patients()] == [2]


def test_a_cancelled_appointment_leaves_the_schedule(practice):
    practice.add_times(practice.doc)
    booking = book(practice, practice.slot_of("doc"), subject="pat").json()["booking"]
    practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    assert len(practice.schedule()) == 1
    practice.post(f"bookings/{booking['id']}/cancel", subject="pat")
    assert practice.schedule() == []


def test_confirming_still_works_without_a_doctor_or_if_the_doctor_was_switched_off(practice):
    plain = book(practice, practice.open_slots()[0]["id"], subject="pat").json()["booking"]
    assert practice.post(f"bookings/{plain['id']}/confirm", subject="ops").status_code == 200
    with tenant_transaction(practice.clinic.ctx):
        assert CareRelationship.objects.count() == 0                      # no doctor, no link
    practice.add_times(practice.doc)
    booking = book(practice, practice.slot_of("doc"), subject="pat").json()["booking"]
    with tenant_transaction(practice.clinic.ctx):
        trust.set_actor_status(practice.doc, trust.Status.SUSPENDED, by="system")
    done = practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    assert done.status_code == 200 and done.json()["booking"]["status"] == "confirmed"
    with tenant_transaction(practice.clinic.ctx):
        assert CareRelationship.objects.count() == 0


# ------------------------------------------------------------------ who may use the doctor screens
def test_only_doctors_may_use_the_doctor_endpoints(practice):
    practice.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    for path in ("clinician/schedule", "clinician/patients"):
        assert Client().get(API + path).status_code == 401
        for who in ("pat", "ops", "hr"):
            r = practice.get(path, subject=who)
            assert r.status_code == 403 and json.loads(r.content)["code"] == "POLICY_DENIED"
        assert practice.get(path, subject="doc").status_code == 200


def test_a_doctor_who_loses_the_role_loses_the_patient_view(practice):
    practice.add_times(practice.doc)
    booking = book(practice, practice.slot_of("doc"), subject="pat").json()["booking"]
    practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    assert len(practice.my_patients()) == 1
    with tenant_transaction(practice.clinic.ctx):
        trust.set_actor_status(practice.doc, trust.Status.SUSPENDED, by="system")
    assert practice.get("clinician/patients", subject="doc").status_code == 401
