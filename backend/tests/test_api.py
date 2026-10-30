import json
import uuid

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.policy.models import AccessDecision
from modules.trust import api as trust
from tests.test_scheduling import Clinic

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True

API = "/api/v1/"


class Web:
    """A browser stand-in: one Clinic, and a way to call the API as any login."""

    def __init__(self):
        self.clinic = Clinic()
        self.client = Client()
        # Slots far in the future, created with an old clock so they count as upcoming.
        self.slots = self.clinic.slots(count=3)

    def header(self, subject, issuer="idp"):
        token = f"dev:{self.clinic.tenant_public_id}:{issuer}:{subject}"
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def get(self, path, subject="pat", **extra):
        return self.client.get(API + path, **self.header(subject), **extra)

    def post(self, path, body=None, subject="pat", key=None):
        extra = self.header(subject)
        if key is not None:
            extra["HTTP_IDEMPOTENCY_KEY"] = key
        return self.client.post(API + path, json.dumps(body or {}), content_type="application/json",
                                **extra)

    def patient(self, subject="pat"):
        return self.clinic.patient(subject)

    def login(self, subject, actor_type, role):
        """A login of the given kind holding one role at the clinic."""
        with tenant_transaction(self.clinic.ctx):
            actor = trust.register_actor(
                actor_type=actor_type, issuer="idp", subject=subject)
            trust.assign_role(actor_public_id=actor.public_id,
                              organization_public_id=self.clinic.org, role_code=role, by="system")
        return actor.public_id

    def staff(self, subject="staffer"):
        return self.login(subject, trust.ActorType.FACILITY_USER, "R04")


@pytest.fixture
def web():
    return Web()


def new_key():
    return uuid.uuid4().hex


def book(web, slot, subject="pat", key=None, person=None):
    body = {"slot": slot}
    if person:
        body["person"] = person
    return web.post("bookings", body, subject=subject, key=key or new_key())


# ------------------------------------------------------------------ login
def test_every_endpoint_needs_a_login_and_the_right_method(web):
    for path in ("me", "services", "bookings"):
        r = web.client.get(API + path)
        assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    bad = web.client.get(API + "me", HTTP_AUTHORIZATION="Bearer dev:nope:idp:pat")
    assert bad.status_code == 401
    web.patient()
    assert web.client.delete(API + "me", **web.header("pat")).status_code == 405


def test_me_describes_the_login(web):
    actor, person = web.patient()
    body = web.get("me").json()
    assert body["actor"] == actor and body["type"] == "patient" and body["persons"] == [person]
    assert body["purpose"] == "SELF_SERVICE" and body["cell"] == "TEST-01"
    assert body["roles"][0]["role"] == "R01" and body["roles"][0]["organization"]["name"] == "North"


# ------------------------------------------------------------------ find care
def test_services_show_the_next_free_time_and_slots_list_open_times(web):
    web.patient()
    services = web.get("services").json()["services"]
    assert len(services) == 1 and services[0]["name"] == "General check-up"
    assert services[0]["organization"]["name"] == "North"
    assert services[0]["next_slot_local"] == "2027-06-15T10:00:00"
    assert services[0]["next_slot_zone"] == "Asia/Kolkata"
    assert web.get("services?q=nothing-like-this").json()["services"] == []
    detail = web.get(f"services/{services[0]['id']}/slots").json()
    assert [s["local_start"] for s in detail["slots"]] == [
        "2027-06-15T10:00:00", "2027-06-15T10:30:00", "2027-06-15T11:00:00"]
    assert detail["slots"][0]["location"] == "Main"
    assert web.get("services/doesnotexist/slots").status_code == 404


def test_employer_and_staff_logins_can_browse_the_catalog(web):
    web.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    assert web.get("services", subject="hr").status_code == 200
    web.staff()
    assert web.get("services", subject="staffer").status_code == 200


# ------------------------------------------------------------------ booking
def test_a_patient_books_through_the_api_and_sees_it_in_their_list(web):
    actor, person = web.patient()
    r = book(web, web.slots[0]["public_id"])
    assert r.status_code == 201 and r["Idempotent-Replayed"] == "false"
    booking = r.json()["booking"]
    assert booking["status"] == "requested" and booking["person"] == person
    assert booking["service"]["name"] == "General check-up"
    assert booking["organization"]["name"] == "North"
    assert booking["local_start"] == "2027-06-15T10:00:00"
    mine = web.get("bookings").json()["bookings"]
    assert [b["id"] for b in mine] == [booking["id"]]


def test_retrying_with_the_same_key_does_not_book_twice(web):
    web.patient()
    key = new_key()
    first = book(web, web.slots[0]["public_id"], key=key)
    again = book(web, web.slots[0]["public_id"], key=key)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again["Idempotent-Replayed"] == "true"
    assert again.json()["booking"]["id"] == first.json()["booking"]["id"]
    assert len(web.get("bookings").json()["bookings"]) == 1
    other = web.post("bookings", {"slot": web.slots[1]["public_id"]}, key=key)   # key reused
    assert other.status_code == 409 and other.json()["code"] == "DUPLICATE_COMMAND"


def test_the_idempotency_key_is_required_and_must_be_long_enough(web):
    web.patient()
    for key in (None, "", "short"):
        r = web.post("bookings", {"slot": web.slots[0]["public_id"]}, key=key)
        assert r.status_code == 422 and r.json()["fields"] == ["idempotency_key"]
    assert web.post("bookings", {}, key=new_key()).status_code == 422          # no slot
    assert web.post("bookings", {"slot": "nope"}, key=new_key()).status_code == 404


def test_two_patients_cannot_take_the_same_slot_and_each_sees_only_their_own(web):
    web.patient("one")
    web.patient("two")
    slot = web.slots[0]["public_id"]
    assert book(web, slot, subject="one").status_code == 201
    taken = book(web, slot, subject="two")
    assert taken.status_code == 409
    assert web.get("bookings", subject="two").json()["bookings"] == []
    assert len(web.get("bookings", subject="one").json()["bookings"]) == 1


def test_booking_for_someone_elses_record_is_refused_and_leaves_a_trace(web):
    web.patient("mine")
    _, theirs = web.patient("theirs")
    r = book(web, web.slots[0]["public_id"], subject="mine", person=theirs)
    assert r.status_code == 403 and r.json()["code"] == "SUBJECT_LINK_UNVERIFIED"
    with tenant_transaction(web.clinic.ctx):
        assert AccessDecision.objects.filter(allowed=False, operation="write:booking").exists()
    assert web.get("bookings", subject="theirs").json()["bookings"] == []


def test_an_employer_login_cannot_book(web):
    web.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    _, person = web.patient()
    r = web.post("bookings", {"slot": web.slots[0]["public_id"], "person": person},
                 subject="hr", key=new_key())
    assert r.status_code == 403 and r.json()["code"] == "POLICY_DENIED"


# ------------------------------------------------------------------ staff and cancel
def test_staff_see_and_confirm_bookings_patients_cannot_confirm(web):
    web.patient()
    web.staff()
    booking = book(web, web.slots[0]["public_id"]).json()["booking"]["id"]
    staff_view = web.get("bookings", subject="staffer").json()["bookings"]
    assert [b["id"] for b in staff_view] == [booking]
    assert web.post(f"bookings/{booking}/confirm", subject="pat").status_code == 403
    done = web.post(f"bookings/{booking}/confirm", subject="staffer")
    assert done.status_code == 200 and done.json()["booking"]["status"] == "confirmed"
    assert web.get("bookings").json()["bookings"][0]["status"] == "confirmed"


def test_cancel_by_the_patient_frees_the_slot_and_strangers_are_refused(web):
    web.patient("owner")
    web.patient("stranger")
    booking = book(web, web.slots[0]["public_id"], subject="owner").json()["booking"]["id"]
    assert web.post(f"bookings/{booking}/cancel", subject="stranger").status_code == 403
    done = web.post(f"bookings/{booking}/cancel", subject="owner")
    assert done.status_code == 200 and done.json()["booking"]["status"] == "cancelled"
    assert web.post(f"bookings/{booking}/cancel", subject="owner").status_code == 200   # harmless
    assert book(web, web.slots[0]["public_id"], subject="stranger").status_code == 201
    assert web.post("bookings/none/cancel", subject="owner").status_code == 404


def test_slots_in_the_past_never_appear(web):
    web.patient()
    services = web.get("services").json()["services"]
    assert services[0]["next_slot"] > utc_now().isoformat()


# ------------------------------------------------------------------ staff: catalog management
def test_staff_build_a_catalog_and_patients_then_see_it(web):
    web.staff()
    org = web.clinic.org
    loc = web.post("locations", {"organization": org, "name": "Annex", "time_zone": "Asia/Kolkata",
                                 "address": "5 Lake Road"}, subject="staffer")
    assert loc.status_code == 201
    location = loc.json()["location"]["id"]
    svc = web.post(
        "services",
        {"organization": org, "name": "Dental check", "category": "dental",
         "duration_minutes": 20, "description": "Routine"},
        subject="staffer")
    assert svc.status_code == 201 and svc.json()["service"]["status"] == "active"
    service = svc.json()["service"]["id"]
    made = web.post(f"services/{service}/slots",
                    {"location": location, "first_local": "2027-08-02T09:00:00", "count": 4},
                    subject="staffer")
    assert made.status_code == 201 and made.json()["created"] == 4

    web.patient()
    names = [s["name"] for s in web.get("services").json()["services"]]
    assert "Dental check" in names
    slots = web.get(f"services/{service}/slots").json()["slots"]
    assert [s["local_start"][11:16] for s in slots] == ["09:00", "09:20", "09:40", "10:00"]
    listed = web.get("locations", subject="staffer").json()["locations"]
    assert {"Main", "Annex"} <= {x["name"] for x in listed}


def test_staff_can_hide_a_service_and_see_hidden_ones(web):
    web.staff()
    web.patient()
    service = web.get("services").json()["services"][0]["id"]
    off = web.post(f"services/{service}/status", {"status": "inactive"}, subject="staffer")
    assert off.status_code == 200 and off.json()["service"]["status"] == "inactive"
    assert web.get("services").json()["services"] == []                  # patients no longer see it
    staff_list = web.get("services?all=1", subject="staffer").json()["services"]
    assert [(s["id"], s["status"]) for s in staff_list] == [(service, "inactive")]
    on = web.post(f"services/{service}/status", {"status": "active"}, subject="staffer")
    assert on.json()["service"]["status"] == "active"
    assert web.post(f"services/{service}/status", {"status": "bogus"},
                    subject="staffer").status_code == 422


def test_only_staff_of_that_organization_may_change_its_catalog(web):
    web.patient("pat")
    web.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    web.staff()
    with tenant_transaction(web.clinic.ctx):
        other = trust.create_organization(name="Elsewhere").public_id
    service = web.get("services", subject="pat").json()["services"][0]["id"]
    body = {"organization": web.clinic.org, "name": "Sneaky", "category": "dental",
            "duration_minutes": 20}
    for who in ("pat", "hr"):
        assert web.post("services", body, subject=who).status_code == 403
        assert web.post(f"services/{service}/status", {"status": "inactive"},
                        subject=who).status_code == 403
        assert web.get("services?all=1", subject=who).status_code == 403
    wrong_org = web.post("services", {**body, "organization": other}, subject="staffer")
    assert wrong_org.status_code == 403                                  # staff of another org
    assert [s["name"] for s in web.get("services").json()["services"]] == ["General check-up"]


def test_catalog_requests_with_bad_input_get_clean_errors(web):
    web.staff()
    org = web.clinic.org
    good = {"organization": org, "name": "X", "category": "dental", "duration_minutes": 20}
    assert web.post("services", {**good, "duration_minutes": "20"}, subject="staffer"
                    ).status_code == 422
    assert web.post("services", {**good, "duration_minutes": 2}, subject="staffer"
                    ).status_code == 422
    assert web.post("services", {**good, "category": "Bad Cat"}, subject="staffer"
                    ).status_code == 422
    assert web.post("locations", {"organization": org, "name": "Z", "time_zone": "Mars/Base"},
                    subject="staffer").status_code == 422
    service = web.get("services?all=1", subject="staffer").json()["services"][0]["id"]
    loc = web.get("locations", subject="staffer").json()["locations"][0]["id"]
    for body in ({"location": loc, "first_local": "not a time", "count": 2},
                 {"location": loc, "first_local": "2027-08-02T09:00:00", "count": "2"},
                 {"location": loc, "first_local": "2020-01-01T09:00:00", "count": 1},   # past
                 {"location": "nope", "first_local": "2027-08-02T09:00:00", "count": 1}):
        refused = web.post(f"services/{service}/slots", body, subject="staffer")
        assert refused.status_code in (404, 422)
    # a duplicate of an existing slot is a conflict
    again = web.post(f"services/{service}/slots",
                     {"location": loc, "first_local": "2027-06-15T10:00:00", "count": 1},
                     subject="staffer")
    assert again.status_code == 409
