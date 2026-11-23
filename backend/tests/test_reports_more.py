"""More report measures (how far ahead people book, how full the times were, cancelled and missed
shares, how long the waiting list took) and the weekly summary email a clinic can ask for."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedEmail
from foundation.timeutil import FixedClock, utc_now
from modules.comms import report_email
from modules.scheduling.models import Slot
from modules.trust.models import Actor, Credential
from modules.waitlist import services as waitlist_services
from modules.waitlist.models import WaitEntry
from tests.test_clinician import Practice
from tests.test_report_extras import visit
from tests.test_reports import make_booking, report
from tests.test_waitlist import Waiting

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STAFF_APP_URL = "http://staff.test"


@pytest.fixture
def p():
    practice = Practice()
    practice.name_referral_owner()
    return practice


# ------------------------------------------------------------------ the new measures
def test_a_quiet_clinic_has_empty_measures_not_made_up_zeroes(p):
    bookings = report(p)["bookings"]
    assert bookings["lead_time_days"] == {"count": 0, "median": None, "average": None}
    assert bookings["utilisation"] == {"offered": 0, "taken": 0, "rate": None}
    assert bookings["cancel_rate"] is None and bookings["no_show_rate"] is None
    assert report(p)["waiting_list"]["hours_to_book"]["count"] == 0


def test_how_far_ahead_people_book_counts_only_bookings_still_wanted(p):
    make_booking(p, confirm=False)
    kept, _ = make_booking(p, confirm=False)
    gone, _ = make_booking(p, confirm=False)
    p.post(f"bookings/{gone}/cancel", subject="pat")
    lead = report(p)["bookings"]["lead_time_days"]
    assert lead["count"] == 2 and lead["median"] > 0           # the practice's times are in 2027
    assert kept != gone


def test_the_cancelled_share_is_of_all_bookings_made(p):
    make_booking(p, confirm=True)
    make_booking(p, confirm=False)
    cancelled, _ = make_booking(p, confirm=False)
    p.post(f"bookings/{cancelled}/cancel", subject="pat")
    assert report(p)["bookings"]["cancel_rate"] == pytest.approx(1 / 3, abs=0.001)


def test_the_overall_missed_share_counts_only_recorded_visits(p):
    visit(p, "attended")
    visit(p, "attended")
    visit(p, "no_show")
    visit(p)                                                    # not recorded yet
    assert report(p)["bookings"]["no_show_rate"] == pytest.approx(1 / 3, abs=0.001)


def test_how_full_the_times_were_counts_open_times_in_the_period_that_someone_holds(p):
    make_booking(p, confirm=True)
    make_booking(p, confirm=False)
    cancelled, _ = make_booking(p, confirm=False)
    p.post(f"bookings/{cancelled}/cancel", subject="pat")      # its time is free again
    start = utc_now() - dt.timedelta(days=2)
    with tenant_transaction(p.clinic.ctx):
        for i, slot in enumerate(Slot.objects.order_by("starts_at")):
            moved = start + dt.timedelta(hours=i)          # every one of the clinic's times
            Slot.objects.filter(pk=slot.pk).update(
                starts_at=moved, ends_at=moved + dt.timedelta(minutes=30))
        total = Slot.objects.count()
    full = report(p)["bookings"]["utilisation"]
    assert full["offered"] == total and full["taken"] == 2
    assert full["rate"] == pytest.approx(2 / total, abs=0.001)


def test_the_measures_hold_no_patient_or_name(p):
    make_booking(p, confirm=True)
    blob = json.dumps(report(p)["bookings"])
    assert "person_ref" not in blob and "Ravi" not in blob


def test_waiting_list_wait_is_measured_from_joining_to_booking():
    w = Waiting()
    assert w.join("pat").status_code == 201
    entry = w.entries("pat")[0]
    with tenant_transaction(w.clinic.ctx):
        WaitEntry.objects.filter(public_id=entry["id"]).update(
            created_at=utc_now() - dt.timedelta(hours=10))
        assert waitlist_services.close_for_booking(entry["patient"], w.cardio) == 1
    r = w.get("staff/reports", subject="desk")
    assert r.status_code == 200, r.content
    waiting = json.loads(r.content)["waiting_list"]
    assert waiting["booked"] == 1
    assert waiting["hours_to_book"]["count"] == 1
    assert waiting["hours_to_book"]["median"] == pytest.approx(10, abs=0.5)


# ------------------------------------------------------------------ the weekly summary email
def give_email(p, actor_ref, address, verified=True):
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.create(
            actor=Actor.objects.get(public_id=actor_ref), login=address, password_hash="x",
            email=address, email_verified_at=utc_now() if verified else None)


def admin(p, address="boss@clinic.test", verified=True):
    actor = p.boss                      # the clinic administrator the fixture already made
    give_email(p, actor, address, verified)
    return actor


def run(p, clock=None):
    with tenant_transaction(p.clinic.ctx):
        return report_email.send_weekly_report(clock)


def mails():
    return list(QueuedEmail.objects.filter(kind=report_email.KIND))


def turn(p, on, subject="boss"):
    return p.post("staff/settings", {"weekly_report": on}, subject=subject)


def test_it_is_off_until_the_administrator_turns_it_on(p):
    admin(p)
    assert run(p) == 0 and mails() == []
    shown = json.loads(p.get("staff/settings", subject="boss").content)
    assert shown["weekly_report"] is False


def test_once_on_the_administrator_gets_one_summary_with_counts_only(p):
    admin(p)
    make_booking(p, confirm=True)
    assert turn(p, True).status_code == 200
    assert json.loads(p.get("staff/settings", subject="boss").content)["weekly_report"] is True
    assert run(p) == 1
    (mail,) = mails()
    assert mail.to_address == "boss@clinic.test"
    assert mail.subject.startswith("Weekly summary for ") and "-W" in mail.subject
    for line in ("Bookings made:", "Visits:", "Cancellation rate:", "Times taken:",
                 "Waiting list:"):
        assert line in mail.body
    assert "http://staff.test" in mail.body
    for private in ("Ravi", "@", "General check-up"):
        assert private not in mail.body


def test_a_second_run_in_the_same_week_sends_nothing_but_the_next_week_does(p):
    admin(p)
    turn(p, True)
    assert run(p) == 1
    assert run(p) == 0
    next_week = FixedClock(utc_now() + dt.timedelta(days=7))
    assert run(p, next_week) == 1
    assert len(mails()) == 2


def test_turning_it_off_again_stops_it(p):
    admin(p)
    turn(p, True)
    turn(p, False)
    assert run(p) == 0 and mails() == []


def test_an_administrator_without_a_confirmed_address_gets_nothing(p):
    admin(p, verified=False)
    turn(p, True)
    assert run(p) == 0 and mails() == []


def test_only_the_administrator_may_change_it_and_only_on_or_off(p):
    admin(p)
    assert turn(p, True, subject="ops").status_code == 403
    for bad in ("yes", 1, None, [True]):
        assert p.post("staff/settings", {"weekly_report": bad}, subject="boss").status_code == 422
    assert json.loads(p.get("staff/settings", subject="boss").content)["weekly_report"] is False


def test_turning_the_summary_on_leaves_the_response_target_alone(p):
    admin(p)
    p.post("staff/settings", {"hours": 30}, subject="boss")
    turn(p, True)
    assert json.loads(p.get("staff/settings", subject="boss").content)[
        "response_target_hours"] == 30


def test_staff_who_are_not_administrators_do_not_get_the_summary(p):
    admin(p)
    give_email(p, p.desk, "desk@clinic.test")
    turn(p, True)
    assert run(p) == 1
    assert [m.to_address for m in mails()] == ["boss@clinic.test"]
