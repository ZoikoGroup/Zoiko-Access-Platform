import json

import pytest

from foundation import dispatch
from foundation.outbox import relay_once
from modules.comms import api as comms
from tests.test_attention import booked
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


@pytest.fixture
def p():
    return Practice()


def cancel(p, booking, reason=None, subject="pat"):
    body = {} if reason is None else {"reason": reason}
    return p.post(f"bookings/{booking}/cancel", body, subject=subject)


def row(p, booking, subject="ops"):
    rows = json.loads(p.get("bookings", subject=subject).content)["bookings"]
    return next(r for r in rows if r["id"] == booking)


def notices(p):
    for _ in range(4):
        relay_once(dispatch.dispatch)
    got = json.loads(p.get("notifications", subject="pat").content)["notifications"]
    return [n for n in got if n["kind"] == "booking_cancelled"]


def report(p):
    return json.loads(p.get("staff/reports?days=30", subject="ops").content)["bookings"]


def test_a_patient_gives_a_reason_and_the_clinic_sees_it(p):
    booking = booked(p, confirm=True)
    r = cancel(p, booking, "schedule_conflict")
    assert r.status_code == 200
    assert json.loads(r.content)["booking"]["cancel_reason"] == "schedule_conflict"
    assert row(p, booking)["cancel_reason"] == "schedule_conflict"


def test_a_reason_is_optional(p):
    booking = booked(p, confirm=False)
    assert cancel(p, booking).status_code == 200
    assert cancel(p, booked(p, confirm=False), "").status_code == 200
    assert row(p, booking)["cancel_reason"] is None


@pytest.mark.parametrize("reason", ["doctor_unavailable", "made-up", "SCHEDULE_CONFLICT", " x "])
def test_a_patient_cannot_use_a_clinic_reason_or_an_unknown_one(p, reason):
    booking = booked(p, confirm=False)
    assert cancel(p, booking, reason).status_code == 422
    assert row(p, booking)["status"] == "requested"               # nothing was cancelled


@pytest.mark.parametrize("reason", ["schedule_conflict", "booked_elsewhere", "made-up"])
def test_clinic_staff_cannot_use_a_patient_reason_or_an_unknown_one(p, reason):
    booking = booked(p, confirm=False)
    assert cancel(p, booking, reason, subject="ops").status_code == 422
    assert row(p, booking)["status"] == "requested"


def test_clinic_staff_give_a_clinic_reason(p):
    booking = booked(p, confirm=True)
    assert cancel(p, booking, "doctor_unavailable", subject="ops").status_code == 200
    assert row(p, booking)["cancel_reason"] == "doctor_unavailable"


def test_the_first_reason_is_kept_when_it_is_cancelled_again(p):
    booking = booked(p, confirm=False)
    cancel(p, booking, "no_longer_needed")
    again = cancel(p, booking, "booked_elsewhere")
    assert again.status_code == 200
    assert row(p, booking)["cancel_reason"] == "no_longer_needed"


def test_the_patient_is_told_the_clinics_reason_in_plain_words(p):
    booking = booked(p, confirm=True)
    cancel(p, booking, "doctor_unavailable", subject="ops")
    (notice,) = notices(p)
    assert "The clinic said: the doctor is unavailable then." in notice["body"]


def test_a_clinic_reason_of_other_adds_nothing_to_the_notice(p):
    booking = booked(p, confirm=True)
    cancel(p, booking, "other", subject="ops")
    (notice,) = notices(p)
    assert "The clinic said" not in notice["body"]


def test_the_patients_own_reason_is_not_repeated_back_to_them(p):
    booking = booked(p, confirm=True)
    cancel(p, booking, "schedule_conflict")
    (notice,) = notices(p)
    assert "The clinic said" not in notice["body"] and "cannot make" not in notice["body"]


def test_the_report_counts_cancellations_by_reason(p):
    p.add_times(p.doc, count=8)                       # enough free times for four bookings
    a = booked(p, confirm=False)
    b = booked(p, confirm=False)
    c = booked(p, confirm=False)
    d = booked(p, confirm=True)
    cancel(p, a, "schedule_conflict")
    cancel(p, b, "schedule_conflict")
    cancel(p, c)
    cancel(p, d, "doctor_unavailable", subject="ops")
    assert report(p)["cancel_reasons"] == {
        "schedule_conflict": 2, "none": 1, "doctor_unavailable": 1}


def test_no_cancellations_means_an_empty_breakdown(p):
    booked(p, confirm=False)
    assert report(p)["cancel_reasons"] == {}


def test_another_customers_cancellations_are_not_in_this_report(p):
    other = Practice()
    cancel(other, booked(other, confirm=False), "schedule_conflict")
    assert report(p)["cancel_reasons"] == {}
