import pytest

from tests.test_api import Web
from tests.test_attention import booked
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def test_the_patient_gets_a_calendar_file_for_their_appointment(p):
    booking = booked(p, confirm=True)
    r = p.get(f"bookings/{booking}/calendar", subject="pat")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/calendar")
    assert "attachment" in r["Content-Disposition"]
    body = r.content.decode()
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert f"UID:{booking}@zoikoaccess" in body
    assert "STATUS:CONFIRMED" in body
    start = next(x for x in body.split("\r\n") if x.startswith("DTSTART:")).split(":")[1]
    end = next(x for x in body.split("\r\n") if x.startswith("DTEND:")).split(":")[1]
    assert start.endswith("Z") and end > start


def test_a_waiting_booking_is_marked_tentative(p):
    booking = booked(p, confirm=False)
    body = p.get(f"bookings/{booking}/calendar", subject="pat").content.decode()
    assert "STATUS:TENTATIVE" in body


def test_the_file_holds_no_personal_data(p):
    booking = booked(p, confirm=True)
    body = p.get(f"bookings/{booking}/calendar", subject="pat").content.decode()
    assert p.person not in body
    assert not any(k in body for k in ("ATTENDEE", "ORGANIZER", "DESCRIPTION"))


def test_a_cancelled_appointment_has_no_calendar_file(p):
    booking = booked(p, confirm=True)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    p.post(f"bookings/{booking}/cancel", subject="ops")          # the clinic answers
    assert p.get(f"bookings/{booking}/calendar", subject="pat").status_code == 404


def test_another_patient_of_the_same_clinic_cannot_download_it(p):
    booking = booked(p, confirm=True)
    p.patient("stranger")                                        # same customer, different person
    assert p.get(f"bookings/{booking}/calendar", subject="stranger").status_code == 403


def test_another_customer_cannot_download_it(p):
    booking = booked(p, confirm=True)
    other = Web()
    other.patient("stranger")
    assert other.get(f"bookings/{booking}/calendar", subject="stranger").status_code == 404


def test_staff_of_the_clinic_can_download_it_too(p):
    booking = booked(p, confirm=True)
    assert p.get(f"bookings/{booking}/calendar", subject="ops").status_code == 200
