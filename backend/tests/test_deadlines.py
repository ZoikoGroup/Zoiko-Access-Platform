"""Journey deadlines that follow the clinic: its own response target, its own offer window, and
(when it has set them) its opening hours, so a deadline counts only the minutes it is open."""
import datetime as dt
import json

import pytest

from foundation import dispatch
from foundation.business_time import BusinessHours, add_business_minutes
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.catalog import api as catalog
from modules.referral.models import Referral
from modules.trust import api as trust
from modules.workflow import api as workflow
from modules.workflow.models import WorkflowInstance, WorkflowTimer
from tests.test_referral import Clinic2, issued
from tests.test_waitlist import Waiting, deliver

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

OFFICE = {"days": [0, 1, 2, 3, 4], "opens": "09:00", "closes": "17:00", "holidays": []}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    from modules.comms import api as comms
    from modules.waitlist.subscribers import register_subscribers

    comms.register_subscribers()
    register_subscribers()


@pytest.fixture
def c():
    return Clinic2()


def pump():
    for _ in range(6):
        relay_once(dispatch.dispatch)


def set_target(c, hours):
    with tenant_transaction(c.clinic.ctx):
        trust.set_response_target(c.clinic.org, hours, by="system")


def set_hours(c, spec):
    with tenant_transaction(c.clinic.ctx):
        trust.set_business_hours(c.clinic.org, spec, by="system")


def sent(c):
    made = issued(c)
    pump()
    return made


def timer(c, subject_ref, name):
    with tenant_transaction(c.clinic.ctx):
        found = WorkflowTimer.objects.filter(
            instance__subject_ref=subject_ref, name=name, status="pending").first()
        assert found is not None, name
        return found.deadline_at, found.created_at


def close(a, b, seconds=60):
    return abs((a - b).total_seconds()) < seconds


# ------------------------------------------------------------------ the clinic's response target
def test_a_referral_must_be_seen_within_the_receiving_clinics_own_target(c):
    set_target(c, 6)
    made = sent(c)
    due, born = timer(c, made["id"], "acknowledge_by")
    assert close(due, born + dt.timedelta(hours=6))


def test_the_decision_deadline_is_three_times_the_target(c):
    set_target(c, 6)
    made = sent(c)
    assert c.post(f"referrals/{made['id']}/acknowledge", {}, subject="ops").status_code == 200
    pump()
    due, born = timer(c, made["id"], "decide_by")
    assert close(due, born + dt.timedelta(hours=18))


def test_a_clinic_without_its_own_target_gets_the_service_default(c, settings):
    settings.RESPONSE_TARGET_HOURS = 24
    made = sent(c)
    due, born = timer(c, made["id"], "acknowledge_by")
    assert close(due, born + dt.timedelta(hours=24))


def test_changing_the_target_later_does_not_move_a_deadline_already_set(c):
    set_target(c, 6)
    made = sent(c)
    before = timer(c, made["id"], "acknowledge_by")[0]
    set_target(c, 48)
    assert timer(c, made["id"], "acknowledge_by")[0] == before


# ------------------------------------------------------------------ opening hours
def test_with_opening_hours_set_only_open_time_counts(c):
    set_target(c, 6)
    set_hours(c, OFFICE)
    made = sent(c)
    due, born = timer(c, made["id"], "acknowledge_by")
    with tenant_transaction(c.clinic.ctx):
        zone = catalog.organization_zone(c.clinic.org)["zone"]
    expected = add_business_minutes(born, 6 * 60, BusinessHours.from_spec(OFFICE), zone)
    assert close(due, expected)
    # never earlier than counting every hour (the timer row is stamped a moment after it was set)
    assert due >= born + dt.timedelta(hours=6) - dt.timedelta(seconds=30)


def test_hours_that_are_open_only_one_hour_a_day_stretch_the_deadline(c):
    set_target(c, 6)
    set_hours(c, {"days": [0, 1, 2, 3, 4, 5, 6], "opens": "09:00", "closes": "10:00"})
    made = sent(c)
    due, born = timer(c, made["id"], "acknowledge_by")
    assert due - born >= dt.timedelta(days=5)              # six open hours is at least five days


def test_hours_set_after_a_journey_started_do_not_move_its_deadline(c):
    set_target(c, 6)
    made = sent(c)
    before = timer(c, made["id"], "acknowledge_by")[0]
    set_hours(c, {"days": [0, 1, 2, 3, 4, 5, 6], "opens": "09:00", "closes": "10:00"})
    assert timer(c, made["id"], "acknowledge_by")[0] == before
    with tenant_transaction(c.clinic.ctx):
        inst = WorkflowInstance.objects.get(subject_ref=made["id"])
        assert inst.business_hours is None                 # this journey counts every hour


def test_a_referral_is_flagged_late_by_open_hours_when_the_clinic_has_set_them(c):
    made = issued(c)
    with tenant_transaction(c.clinic.ctx):
        Referral.objects.filter(public_id=made["id"]).update(
            created_at=utc_now() - dt.timedelta(hours=25))
    assert c.incoming()[0]["escalated"] is True            # every hour counts: 25 > 24
    set_hours(c, {"days": [0, 1, 2, 3, 4, 5, 6], "opens": "09:00", "closes": "10:00"})
    assert c.incoming()[0]["escalated"] is False           # 24 OPEN hours is about three weeks
    set_hours(c, None)
    assert c.incoming()[0]["escalated"] is True


# ------------------------------------------------------------------ what the engine accepts
def start(c, **extra):
    with tenant_transaction(c.clinic.ctx):
        workflow.ensure_bundled_definitions()
        return workflow.engine.start(
            definition_key="J2.referral", subject_ref="subject0000000000000001",
            owner_ref=c.clinic.org, actor_ref="system", organization_ref=c.clinic.org, **extra)


@pytest.mark.parametrize("bad", [
    {"nosuchtimer": 60}, {"acknowledge_by": 0}, {"acknowledge_by": -5}, {"acknowledge_by": True},
    {"acknowledge_by": 1.5}, {"acknowledge_by": "60"}, {"acknowledge_by": 60 * 24 * 366},
    "acknowledge_by",
])
def test_the_engine_refuses_timer_lengths_it_cannot_use(c, bad):
    with pytest.raises(ZoikoError) as caught:
        start(c, timer_minutes=bad)
    assert caught.value.code == ErrorCode.VALIDATION_FAILED


@pytest.mark.parametrize("bad", [{"days": []}, {"opens": "18:00", "closes": "08:00"}, "always"])
def test_the_engine_refuses_unusable_opening_hours(c, bad):
    with pytest.raises(ZoikoError) as caught:
        start(c, business_hours=bad)
    assert caught.value.code == ErrorCode.VALIDATION_FAILED


def test_the_engine_uses_the_definitions_lengths_when_nothing_is_asked_for(c):
    started = start(c)
    with tenant_transaction(c.clinic.ctx):
        found = WorkflowTimer.objects.get(instance__public_id=started["public_id"])
        assert found.name == "acknowledge_by"
        assert close(found.deadline_at, found.created_at + dt.timedelta(minutes=1440))


# ------------------------------------------------------------------ the screen
def hours_of(c, subject="boss"):
    r = c.get("staff/business-hours", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def test_the_administrator_sets_and_clears_the_clinics_opening_hours(c):
    assert hours_of(c)["business_hours"] is None
    r = c.post("staff/business-hours", {"business_hours": {
        "days": [4, 0, 1], "opens": "08:30", "closes": "16:45:59", "holidays": ["2026-12-25"]}},
        subject="boss")
    assert r.status_code == 200
    now = hours_of(c)
    assert now["business_hours"] == {
        "days": [0, 1, 4], "opens": "08:30", "closes": "16:45", "holidays": ["2026-12-25"]}
    assert now["can_set"] is True
    c.post("staff/business-hours", {"business_hours": None}, subject="boss")
    assert hours_of(c)["business_hours"] is None


def test_other_staff_can_read_the_hours_but_not_change_them(c):
    c.post("staff/business-hours", {"business_hours": OFFICE}, subject="boss")
    seen = hours_of(c, "ops")
    assert seen["business_hours"]["days"] == [0, 1, 2, 3, 4] and seen["can_set"] is False
    denied = c.post("staff/business-hours", {"business_hours": None}, subject="ops")
    assert denied.status_code == 403
    assert hours_of(c)["business_hours"] is not None


def test_a_patient_cannot_read_or_change_the_clinics_hours(c):
    assert c.get("staff/business-hours", subject="pat").status_code == 403
    assert c.post("staff/business-hours", {"business_hours": OFFICE},
                  subject="pat").status_code == 403


@pytest.mark.parametrize("bad", [
    {"days": []}, {"days": [9]}, {"opens": "17:00", "closes": "09:00"}, {"opens": "late"},
    {"holidays": ["tomorrow"]}, "all day", 5,
])
def test_unusable_hours_are_refused_and_change_nothing(c, bad):
    c.post("staff/business-hours", {"business_hours": OFFICE}, subject="boss")
    r = c.post("staff/business-hours", {"business_hours": bad}, subject="boss")
    assert r.status_code == 422
    assert hours_of(c)["business_hours"]["opens"] == "09:00"


def test_a_request_without_the_hours_field_is_refused(c):
    assert c.post("staff/business-hours", {}, subject="boss").status_code == 422


# ------------------------------------------------------------------ the waiting list's offer window
def test_a_waiting_list_offer_deadline_follows_the_clinics_own_window():
    w = Waiting()
    w.login("boss", "facility_user", "R13")
    assert w.post("staff/settings", {"waitlist_offer_hours": 2}, subject="boss").status_code == 200
    made = json.loads(w.join("pat").content)["entry"]["id"]
    deliver()
    w.add_times(1)
    deliver()
    assert w.entries("pat")[0]["state"] == "offered"
    with tenant_transaction(w.clinic.ctx):
        found = WorkflowTimer.objects.filter(
            instance__subject_ref=made, name="offer_by", status="pending").first()
        assert found is not None
        assert close(found.deadline_at, found.created_at + dt.timedelta(hours=2))
