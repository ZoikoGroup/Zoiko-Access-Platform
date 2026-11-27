"""The provider directory: the doctors a patient can book with, and what is known about each, with
where each claim comes from (GHE-14: a listing is not proof of a licence or of quality)."""
import json

import pytest

from modules.trust import api as trust
from tests.test_api import API, book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

NOTE = "the reviewer wrote this private note"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.name_referral_owner()                  # gives the clinic its administrator, `boss`
    practice.add_times(practice.doc, count=3)
    return practice


def directory(p, query="", subject="pat"):
    r = p.get("directory/doctors" + (f"?{query}" if query else ""), subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def verify(p, provider=None):
    basis = next(iter(trust.NAME_CHECK_BASES))
    who = provider or p.doc
    subject = "doc" if who == p.doc else "doc2"
    named = p.post("auth/display-name", {"name": f"Dr {subject.title()} Rao"}, subject=subject)
    assert named.status_code == 200, named.content      # a name must exist before it is checked
    r = p.post(f"staff/providers/{provider or p.doc}/verify", {"basis": basis, "note": NOTE},
               subject="boss")
    assert r.status_code == 200, r.content
    return basis


def doctor(found, ref):
    return next(d for d in found["doctors"] if d["id"] == ref)


# ------------------------------------------------------------------ what a listing holds
def test_a_doctor_with_free_times_is_listed_with_services_places_and_the_next_time(p):
    found = directory(p)
    row = doctor(found, p.doc)
    assert row["name"] == "doc" and row["free_times"] == 3
    assert [s["name"] for s in row["services"]] == ["General check-up"]
    assert row["places"][0]["id"] == p.clinic.location
    assert row["next_free"]["local_start"].startswith("2027-09-01T09:00")
    assert row["next_free"]["service"] == "General check-up"
    assert row["organization"]["id"] == p.clinic.org and row["organization"]["name"]


def test_every_claim_says_where_it_comes_from_and_when(p):
    found = directory(p)
    assert found["availability"]["source"] == "the clinic's own calendar"
    assert found["availability"]["as_of"]
    assert "not proof of a current licence" in found["disclaimer"]
    row = doctor(found, p.doc)
    assert row["booking_method"] == "clinic"                 # the clinic's staff confirm


def test_a_doctor_with_no_free_time_is_not_listed(p):
    only = Practice()                                        # a clinic whose doctor has no times
    assert directory(only)["doctors"] == []


def test_a_time_that_is_taken_stops_counting(p):
    before = doctor(directory(p), p.doc)["free_times"]
    made = book(p, p.slot_of("doc"), subject="pat")
    assert made.status_code == 201, made.content
    assert doctor(directory(p), p.doc)["free_times"] == before - 1


def test_a_switched_off_service_is_not_listed(p):
    p.post(f"services/{p.clinic.service}/status", {"status": "inactive"}, subject="ops")
    assert directory(p)["doctors"] == []


# ------------------------------------------------------------------ the name check
def test_an_unchecked_name_shows_no_mark(p):
    row = doctor(directory(p), p.doc)
    assert row["verified"] is False and row["name_check"] == {"state": "none"}


def test_a_checked_name_shows_who_and_when_but_never_the_reviewers_note(p):
    basis = verify(p)
    found = directory(p)
    row = doctor(found, p.doc)
    assert row["verified"] is True
    assert row["name_check"]["state"] == "verified" and row["name_check"]["checked_on"]
    assert row["name_check"]["basis"] == trust.NAME_CHECK_BASES[basis]
    assert NOTE not in json.dumps(found)


def test_a_withdrawn_check_removes_the_mark(p):
    verify(p)
    r = p.post(f"staff/providers/{p.doc}/unverify", {"reason": "left the clinic"}, subject="boss")
    assert r.status_code == 200, r.content
    row = doctor(directory(p), p.doc)
    assert row["verified"] is False and row["name_check"] == {"state": "none"}
    assert "left the clinic" not in json.dumps(directory(p))


def test_checked_doctors_come_first_whatever_their_time(p):
    p.add_times(p.doc2, first="2027-08-01T09:00:00", count=1)      # sooner than doc's times
    found = directory(p)
    assert [d["id"] for d in found["doctors"]][:2] == [p.doc2, p.doc]      # soonest first
    verify(p)
    assert [d["id"] for d in directory(p)["doctors"]][:2] == [p.doc, p.doc2]


# ------------------------------------------------------------------ searching
def test_the_search_matches_a_doctor_a_service_or_a_place_ignoring_case(p):
    assert [d["id"] for d in directory(p, "q=DOC")["doctors"]] == [p.doc]
    assert directory(p, "q=general")["doctors"][0]["id"] == p.doc
    place = directory(p)["doctors"][0]["places"][0]["name"]
    assert directory(p, f"q={place.upper()}")["doctors"][0]["id"] == p.doc
    assert directory(p, "q=nobody-called-this")["doctors"] == []


def test_the_list_can_be_narrowed_by_service_or_clinic(p):
    assert len(directory(p, f"service={p.clinic.service}")["doctors"]) >= 1
    assert directory(p, "service=nosuchservice0000000000")["doctors"] == []
    assert len(directory(p, f"organization={p.clinic.org}")["doctors"]) >= 1
    assert directory(p, "organization=nosuchclinic00000000000")["doctors"] == []


def test_a_search_that_is_too_long_is_refused(p):
    r = p.get("directory/doctors?q=" + "x" * 101, subject="pat")
    assert r.status_code == 422


# ------------------------------------------------------------------ who sees what
def test_staff_see_the_same_directory_as_patients(p):
    assert [d["id"] for d in directory(p, subject="ops")["doctors"]] == [
        d["id"] for d in directory(p)["doctors"]]


def test_another_clinics_doctors_are_never_listed(p):
    other = Practice()
    other.add_times(other.doc, count=2)
    mine = {d["id"] for d in directory(p)["doctors"]}
    theirs = {d["id"] for d in directory(other)["doctors"]}
    assert mine and theirs and not mine & theirs


def test_it_needs_a_login_and_only_reads(p):
    assert p.client.get(API + "directory/doctors").status_code == 401
    assert p.post("directory/doctors", {}, subject="pat").status_code == 405
