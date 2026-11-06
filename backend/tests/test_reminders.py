import datetime as dt
import json

import pytest

from foundation import dispatch, periodic
from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.comms import api as comms
from modules.scheduling import services as scheduling_services
from modules.scheduling.jobs import remind_all_tenants
from modules.scheduling.models import Booking, Slot
from tests.test_api import Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.REMINDER_LEAD_HOURS = 24
    comms.register_subscribers()


@pytest.fixture
def web():
    w = Web()
    w.patient("pat")
    w.staff("desk")
    return w


def booked(web, index=0, confirm=True, starts_in_hours=5):
    """A booking whose slot starts `starts_in_hours` from now (the slots are made far ahead)."""
    r = book(web, web.slots[index]["public_id"], subject="pat")
    assert r.status_code == 201
    booking = json.loads(r.content)["booking"]["id"]
    if confirm:
        assert web.post(f"bookings/{booking}/confirm", subject="desk").status_code == 200
    start = utc_now() + dt.timedelta(hours=starts_in_hours, minutes=index)
    with tenant_transaction(web.clinic.ctx):
        Slot.objects.filter(public_id=web.slots[index]["public_id"]).update(
            starts_at=start, ends_at=start + dt.timedelta(minutes=30))
    return booking


def run(web):
    with tenant_transaction(web.clinic.ctx):
        return scheduling_services.send_due_reminders()


def reminders_in_inbox(web):
    for _ in range(3):
        relay_once(dispatch.dispatch)
    notices = json.loads(web.get("notifications", subject="pat").content)["notifications"]
    return [n for n in notices if n["kind"] == "booking_reminder"]


# ------------------------------------------------------------------ who gets one, and when
def test_a_confirmed_appointment_starting_soon_is_reminded_once(web):
    booked(web)
    assert run(web) == 1
    assert run(web) == 0                                    # never a second time
    inbox = reminders_in_inbox(web)
    assert len(inbox) == 1 and inbox[0]["title"] == "Appointment reminder"
    assert "Reminder: your General check-up at North" in inbox[0]["body"]


def test_the_patient_gets_one_notification_even_if_the_worker_runs_again(web):
    booked(web)
    run(web)
    run(web)
    reminders_in_inbox(web)
    assert len(reminders_in_inbox(web)) == 1


def test_an_appointment_further_away_than_the_lead_time_is_not_reminded_yet(web):
    booked(web, starts_in_hours=30)
    assert run(web) == 0
    assert reminders_in_inbox(web) == []


def test_the_lead_time_is_a_setting(web, settings):
    booked(web, starts_in_hours=30)
    settings.REMINDER_LEAD_HOURS = 48
    assert run(web) == 1


def test_waiting_cancelled_and_past_bookings_are_not_reminded(web):
    booked(web, 0, confirm=False)                           # not confirmed
    cancelled = booked(web, 1)
    web.post(f"bookings/{cancelled}/cancel", subject="pat")
    booked(web, 2, starts_in_hours=-2)                      # already started
    assert run(web) == 0
    assert reminders_in_inbox(web) == []


def test_a_booking_cancelled_after_the_reminder_was_queued_is_not_announced(web):
    booking = booked(web)
    run(web)                                                # reminder event queued, not delivered
    web.post(f"bookings/{booking}/cancel", subject="pat")
    notices = reminders_in_inbox(web)
    assert notices == []


def test_a_moved_appointment_gets_its_own_reminder(web):
    first = booked(web, 0)
    run(web)
    moved = json.loads(web.post(f"bookings/{first}/reschedule", {"slot": web.slots[1]["public_id"]},
                                subject="desk").content)["booking"]
    assert moved["status"] == "confirmed"
    start = utc_now() + dt.timedelta(hours=6)
    with tenant_transaction(web.clinic.ctx):
        Slot.objects.filter(public_id=web.slots[1]["public_id"]).update(
            starts_at=start, ends_at=start + dt.timedelta(minutes=30))
    assert run(web) == 1


def test_the_reminder_event_carries_no_personal_text(web):
    booked(web)
    run(web)
    with tenant_transaction(web.clinic.ctx):
        payloads = [json.dumps(e.payload) for e in OutboxEvent.objects.filter(
            event_type="booking.reminder.v1")]
    assert payloads and not any("General" in p or "North" in p for p in payloads)


# ------------------------------------------------------------------ every customer, not just one
def test_the_worker_job_reminds_every_customer(web):
    booked(web)
    other = Web()
    other.patient("pat")
    other.staff("desk")
    booked(other)
    assert remind_all_tenants() >= 2
    for site in (web, other):
        with tenant_transaction(site.clinic.ctx):
            mine = Booking.objects.filter(
                reminder_sent_at__isnull=False, tenant_id=site.clinic.ctx.tenant_id)
            assert mine.count() == 1


# ------------------------------------------------------------------ the recurring-job hook
def test_the_scheduling_job_is_registered_with_the_worker():
    assert "scheduling.reminders" in periodic.registered()


def test_a_job_runs_only_when_its_interval_has_passed():
    calls = []
    def count():
        calls.append(1)
        return 1

    periodic.register("test.counter", 60.0, count)
    now = [100.0]
    first = periodic.run_due(lambda: now[0])
    now[0] = 130.0                                          # 30 s later: too soon
    second = periodic.run_due(lambda: now[0])
    now[0] = 161.0                                          # 61 s after the first run
    third = periodic.run_due(lambda: now[0])
    assert first["test.counter"] == 1 and "test.counter" not in second
    assert third["test.counter"] == 1 and len(calls) == 2
    periodic._tasks.pop("test.counter")


def test_a_failing_job_does_not_stop_the_others_and_reports_only_its_class():
    def broken() -> int:
        raise ValueError("patient Asha Rao, secret detail")

    seen = []
    periodic.register("test.broken", 0.0, broken)
    def fine():
        seen.append(1)
        return 7

    periodic.register("test.fine", 0.0, fine)
    result = periodic.run_due()
    assert result["test.broken"] == "failed:ValueError" and result["test.fine"] == 7
    assert "Asha" not in json.dumps(result)
    periodic._tasks.pop("test.broken")
    periodic._tasks.pop("test.fine")
