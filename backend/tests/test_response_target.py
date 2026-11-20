"""Each clinic sets its own response target (the specification has no universal timeout); the
service-wide value is only the default. DEV-120."""
import datetime as dt
import json

import pytest
from django.conf import settings

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.referral.models import Referral
from modules.scheduling.models import Booking
from modules.trust import api as trust
from modules.workflow.models import WorkItem
from tests.test_api import book
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def c():
    clinic = Clinic2()                      # has a receptionist (ops) and an administrator (boss)
    clinic.add_times(clinic.doc2, count=2)
    return clinic


def config(c, subject="boss"):
    r = c.get("staff/settings", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def set_hours(c, hours, subject="boss"):
    return c.post("staff/settings", {"hours": hours}, subject=subject)


def request_booking(c):
    made = book(c, c.slot_of("doc2"), subject="pat")
    assert made.status_code == 201, made.content
    return json.loads(made.content)["booking"]["id"]


def age(model, ref, hours):
    with_ = {"created_at": utc_now() - dt.timedelta(hours=hours)}
    model.objects.filter(public_id=ref).update(**with_)


# ------------------------------------------------------------------ the setting
def test_a_clinic_starts_on_the_service_default(c):
    got = config(c)
    assert got["response_target_hours"] == settings.RESPONSE_TARGET_HOURS
    assert got["response_target_default"] == settings.RESPONSE_TARGET_HOURS
    assert got["can_set_target"] is True and config(c, "ops")["can_set_target"] is False


def test_the_administrator_sets_it_and_can_go_back_to_the_default(c):
    assert set_hours(c, 4).status_code == 200
    assert config(c)["response_target_hours"] == 4
    assert set_hours(c, None).status_code == 200
    assert config(c)["response_target_hours"] == settings.RESPONSE_TARGET_HOURS


def test_only_the_clinic_administrator_can_change_it(c):
    for who in ("ops", "doc", "pat"):
        assert set_hours(c, 4, subject=who).status_code == 403
    assert config(c)["response_target_hours"] == settings.RESPONSE_TARGET_HOURS


@pytest.mark.parametrize("bad", [0, -1, 169, "5", 2.5, True, [4]])
def test_only_one_to_one_hundred_and_sixty_eight_hours_is_accepted(c, bad):
    assert set_hours(c, bad).status_code == 422
    assert config(c)["response_target_hours"] == settings.RESPONSE_TARGET_HOURS


# ------------------------------------------------------------------ what it changes
def test_a_booking_is_late_by_the_clinics_own_target(c):
    booking = request_booking(c)
    with tenant_transaction(c.clinic.ctx):
        age(Booking, booking, 5)
    rows = lambda: json.loads(c.get("bookings", subject="ops").content)["bookings"]   # noqa: E731
    assert [b["late"] for b in rows() if b["id"] == booking] == [False]    # default is longer
    set_hours(c, 4)
    assert [b["late"] for b in rows() if b["id"] == booking] == [True]     # now 5 hours is late


def test_a_referral_is_flagged_by_the_receiving_clinics_own_target():
    c = Clinic2()
    made = issued(c)
    with tenant_transaction(c.clinic.ctx):
        age(Referral, made["id"], 5)
    assert c.incoming()[0]["escalated"] is False
    assert set_hours(c, 4).status_code == 200
    assert c.incoming()[0]["escalated"] is True


def test_new_work_is_due_after_the_clinics_own_target(c):
    set_hours(c, 4)
    booking = request_booking(c)
    for _ in range(4):
        relay_once(dispatch.dispatch)
    with tenant_transaction(c.clinic.ctx):
        item = WorkItem.objects.get(subject_ref=booking, kind="confirm_booking")
        gap = item.due_at - utc_now()
    assert dt.timedelta(hours=3, minutes=50) < gap <= dt.timedelta(hours=4)


def test_the_change_is_recorded_in_the_audit_log(c):
    from modules.evidence.models import AuditEvent
    set_hours(c, 6)
    with tenant_transaction(c.clinic.ctx):
        found = AuditEvent.objects.filter(action="organization.set_response_target")
        assert found.count() == 1 and found.get().details == {"hours": 6}


def test_the_dashboard_reports_the_clinics_target(c):
    set_hours(c, 6)
    got = json.loads(c.get("staff/dashboard", subject="ops").content)
    assert got["late"]["hours"] == 6
    assert trust.MAX_RESPONSE_TARGET_HOURS == 168
