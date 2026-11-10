import json

import pytest

from tests.conftest import publish_service
from tests.test_api import Web, book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.name_referral_owner()
    return practice


def attention(p, subject="ops"):
    r = p.get("staff/dashboard", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["attention"]


def booked(p, confirm):
    if not [s for s in p.open_slots() if s["provider"]]:
        p.add_times(p.doc, count=3)
    slot = p.slot_of("doc")                                  # a time WITH the doctor
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    if confirm:
        p.post(f"bookings/{booking}/confirm", subject="ops")
    return booking


def empty_service(p, name="Cardiology"):
    made = p.post("services", {"organization": p.clinic.org, "name": name,
                               "category": "cardiology", "duration_minutes": 30}, subject="ops")
    service = json.loads(made.content)["service"]["id"]
    publish_service(p.clinic.ctx, service)
    return service


def test_a_quiet_clinic_has_nothing_waiting(p):
    assert attention(p) == {"bookings_to_confirm": 0, "family_requests": 0, "waiting_list": 0,
                            "referrals_to_decide": 0, "messages": 0}


def test_each_kind_of_waiting_work_is_counted_and_clears_when_handled(p):
    booked(p, confirm=True)                                      # gives the doctor a link
    waiting_booking = booked(p, confirm=False)
    cardio = empty_service(p)
    assert p.post("waitlist", {"service": cardio}, subject="pat").status_code == 201
    p.post("clinician/referrals", {"patient": p.person, "service": cardio,
                                   "reason": "Chest pain", "urgency": "routine"}, subject="doc")
    family = json.loads(p.post("family", {"relationship": "parent", "name": "Ravi Rao",
                                          "birth_date": "2018-05-02"}, subject="pat").content)
    assert attention(p) == {"bookings_to_confirm": 1, "family_requests": 1, "waiting_list": 1,
                            "referrals_to_decide": 1, "messages": 0}
    p.post(f"bookings/{waiting_booking}/confirm", subject="ops")
    p.post(f"staff/family-requests/{family['request']['id']}/reject", {}, subject="ops")
    referral = json.loads(p.get("referrals/incoming", subject="ops").content)["referrals"][0]
    p.post(f"referrals/{referral['id']}/decide", {"decision": "accepted"}, subject="doc2")
    entry = json.loads(p.get("staff/waitlist", subject="ops").content)["entries"][0]
    p.post(f"waitlist/{entry['id']}/leave", subject="ops")
    assert attention(p) == {"bookings_to_confirm": 0, "family_requests": 0, "waiting_list": 0,
                            "referrals_to_decide": 0, "messages": 0}


def test_a_referral_waiting_for_more_information_is_not_counted_as_needing_a_decision(p):
    booked(p, confirm=True)
    cardio = empty_service(p)
    p.post("clinician/referrals", {"patient": p.person, "service": cardio, "reason": "Chest pain",
                                   "urgency": "routine"}, subject="doc")
    referral = json.loads(p.get("referrals/incoming", subject="ops").content)["referrals"][0]
    p.post(f"referrals/{referral['id']}/decide",
           {"decision": "needs_info", "reason": "Send the ECG"}, subject="doc2")
    assert attention(p)["referrals_to_decide"] == 0


def test_the_counts_are_only_for_this_customer(p):
    booked(p, confirm=False)
    p.post("family", {"relationship": "parent", "name": "Ravi", "birth_date": "2018-05-02"},
           subject="pat")
    other = Web()
    other.staff("desk")
    r = other.get("staff/dashboard", subject="desk")
    assert json.loads(r.content)["attention"] == {
        "bookings_to_confirm": 0, "family_requests": 0, "waiting_list": 0,
        "referrals_to_decide": 0, "messages": 0}


def test_only_staff_see_the_dashboard(p):
    for who in ("pat", "doc"):
        assert p.get("staff/dashboard", subject=who).status_code == 403
