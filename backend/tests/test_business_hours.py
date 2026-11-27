"""A clinic's opening hours (DEV-367, from the maruthi branch): set by its administrator, and used
by EVERY late flag, work-queue deadline and late count on this branch, so they all agree."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock, utc_now
from modules.catalog import api as catalog
from modules.referral.models import Referral
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from tests.test_api import book
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

OFFICE = {"days": [0, 1, 2, 3, 4], "opens": "09:00", "closes": "17:00", "holidays": []}
ONE_HOUR_A_DAY = {"days": [0, 1, 2, 3, 4, 5, 6], "opens": "09:00", "closes": "10:00"}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def c():
    return Clinic2()


def set_hours(c, spec):
    with tenant_transaction(c.clinic.ctx):
        trust.set_business_hours(c.clinic.org, spec, by="system")


def hours_of(c, subject="boss"):
    r = c.get("staff/business-hours", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


# ------------------------------------------------------------------ the screen (from maruthi)
def test_the_administrator_sets_and_clears_the_clinics_opening_hours(c):
    assert hours_of(c)["business_hours"] is None
    r = c.post("staff/business-hours", {"business_hours": {
        "days": [4, 0, 1], "opens": "08:30", "closes": "16:45:59", "holidays": ["2026-12-25"]}},
        subject="boss")
    assert r.status_code == 200
    now = hours_of(c)
    assert now["business_hours"] == {
        "days": [0, 1, 4], "opens": "08:30", "closes": "16:45", "holidays": ["2026-12-25"]}
    assert now["can_set"] is True and now["zone"] == "Asia/Kolkata"     # the clinic's own clock
    c.post("staff/business-hours", {"business_hours": None}, subject="boss")
    assert hours_of(c)["business_hours"] is None


def test_other_staff_can_read_the_hours_but_not_change_them(c):
    c.post("staff/business-hours", {"business_hours": OFFICE}, subject="boss")
    seen = hours_of(c, "ops")
    assert seen["business_hours"]["days"] == [0, 1, 2, 3, 4] and seen["can_set"] is False
    assert c.post("staff/business-hours", {"business_hours": None},
                  subject="ops").status_code == 403
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


def test_setting_hours_is_audited(c):
    from modules.evidence.models import AuditEvent
    set_hours(c, OFFICE)
    with tenant_transaction(c.clinic.ctx):
        assert AuditEvent.objects.filter(action="organization.set_business_hours").exists()


# ------------------------------------------------------------------ what the hours change
def test_a_referral_is_flagged_late_by_open_hours_when_the_clinic_has_set_them(c):
    made = issued(c)
    with tenant_transaction(c.clinic.ctx):
        Referral.objects.filter(public_id=made["id"]).update(
            created_at=utc_now() - dt.timedelta(hours=25))
    assert c.incoming()[0]["escalated"] is True            # every hour counts: 25 > 24
    set_hours(c, ONE_HOUR_A_DAY)
    assert c.incoming()[0]["escalated"] is False           # 24 OPEN hours is about three weeks
    set_hours(c, None)
    assert c.incoming()[0]["escalated"] is True


def test_due_and_late_line_agree_and_count_only_open_hours(c):
    """`late_cutoff` (the line for counting late work) agrees with `is_late` (built on
    `response_due`): waiting since just before the line is late now, since just after it is not.
    (When the clinic is closed right now, the deadline of the line itself is its last closing
    time, not this instant: closed minutes never count.)"""
    set_hours(c, ONE_HOUR_A_DAY)
    now = FixedClock(utc_now())
    minute = dt.timedelta(minutes=1)
    with tenant_transaction(c.clinic.ctx):
        org = c.clinic.org
        line = catalog.late_cutoff(org, now)
        assert catalog.is_late(org, line - minute, now) is True
        assert catalog.is_late(org, line + minute, now) is False
        assert catalog.response_due(org, line) <= now.now()
        every_hour = now.now() - dt.timedelta(hours=trust.response_target_hours(org))
    assert line < every_hour - dt.timedelta(days=7)         # open one hour a day: weeks, not a day


def test_without_hours_nothing_changes(c):
    since = utc_now() - dt.timedelta(hours=3)
    with tenant_transaction(c.clinic.ctx):
        target = dt.timedelta(hours=trust.response_target_hours(c.clinic.org))
        assert catalog.response_due(c.clinic.org, since) == since + target
        assert not catalog.is_late(c.clinic.org, since)


def test_the_late_count_uses_the_clinics_own_line(c):
    """A booking request 25 hours old is late when every hour counts, and not late when the clinic
    is open one hour a day; the dashboard and the daily email share this count."""
    from modules.scheduling.models import Booking

    assert book(c, c.slots[0]["public_id"]).status_code == 201
    org = c.clinic.org
    with tenant_transaction(c.clinic.ctx):
        assert Booking.objects.filter(status="requested").count() == 1
        Booking.objects.update(created_at=utc_now() - dt.timedelta(hours=25))
        hours = trust.response_target_hours(org)
        assert scheduling.overdue_requests(hours, organization_ref=org,
                                           cutoff=catalog.late_cutoff(org)) == 1
    set_hours(c, ONE_HOUR_A_DAY)
    with tenant_transaction(c.clinic.ctx):
        assert scheduling.overdue_requests(hours, organization_ref=org,
                                           cutoff=catalog.late_cutoff(org)) == 0


def test_unusable_stored_hours_fall_back_to_every_hour(c):
    """Hours stored before a rule tightened (or edited by hand) never stop deadlines working."""
    from modules.trust.models import Organization
    with tenant_transaction(c.clinic.ctx):
        Organization.objects.filter(public_id=c.clinic.org).update(
            business_hours={"days": [], "opens": "09:00", "closes": "17:00"})
        since = utc_now() - dt.timedelta(hours=1)
        target = dt.timedelta(hours=trust.response_target_hours(c.clinic.org))
        assert catalog.response_due(c.clinic.org, since) == since + target
