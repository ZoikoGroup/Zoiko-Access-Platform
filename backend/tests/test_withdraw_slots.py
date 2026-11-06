import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MONDAY = "2027-09-06T09:00:00"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.before = {s["id"] for s in practice.open_slots()}    # the clinic's own starting times
    return practice


def mine(p):
    return [s for s in p.open_slots() if s["id"] not in p.before]


def make(p, first=MONDAY, count=2, days=3, provider=None, subject="ops"):
    body = {"location": p.clinic.location, "first_local": first, "count": count, "days": days,
            "provider": provider or p.doc}
    return p.post(f"services/{p.clinic.service}/slots", body, subject=subject)


def withdraw(p, first="2027-09-06", last="2027-09-06", subject="ops", **extra):
    return p.post(f"services/{p.clinic.service}/slots/withdraw",
                  {"first_day": first, "last_day": last, **extra}, subject=subject)


def count_of(r):
    return json.loads(r.content)["count"]


def dates(p):
    return sorted({s["local_start"][:10] for s in mine(p)})


def test_the_chosen_days_are_taken_off_the_calendar_and_the_others_stay(p):
    make(p, count=2, days=3)                                       # the 6th, 7th and 8th
    r = withdraw(p, "2027-09-07", "2027-09-07")
    assert r.status_code == 200 and count_of(r) == 2
    assert dates(p) == ["2027-09-06", "2027-09-08"]


def test_both_ends_of_the_range_are_included(p):
    make(p, count=1, days=4)                                       # the 6th to the 9th
    assert count_of(withdraw(p, "2027-09-06", "2027-09-08")) == 3
    assert dates(p) == ["2027-09-09"]


def test_a_dry_run_counts_but_changes_nothing(p):
    make(p, count=2, days=2)
    r = withdraw(p, "2027-09-06", "2027-09-07", dry_run=True)
    assert count_of(r) == 4 and json.loads(r.content)["dry_run"] is True
    assert len(mine(p)) == 4


def test_a_time_with_a_booking_is_never_touched(p):
    make(p, count=2, days=1)
    slot = min(mine(p), key=lambda s: s["local_start"])["id"]
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    p.post(f"bookings/{booking}/confirm", subject="ops")
    assert count_of(withdraw(p)) == 1                              # only the other, free time
    rows = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    assert [b["status"] for b in rows if b["id"] == booking] == ["confirmed"]


def test_a_time_whose_booking_was_cancelled_is_free_again_and_can_be_withdrawn(p):
    make(p, count=1, days=1)
    slot = mine(p)[0]["id"]
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    assert count_of(withdraw(p, dry_run=True)) == 0                # taken by the booking
    p.post(f"bookings/{booking}/cancel", subject="pat")
    assert count_of(withdraw(p)) == 1


def test_only_the_chosen_doctors_times_go(p):
    make(p, count=1, days=1, provider=p.doc)
    make(p, count=1, days=1, provider=p.doc2)
    assert count_of(withdraw(p, provider=p.doc2)) == 1
    assert [s["provider"]["id"] for s in mine(p)] == [p.doc]


def test_times_in_the_past_are_left_alone(p):
    import datetime as dt

    from foundation.timeutil import utc_now
    from modules.scheduling.models import Slot, SlotStatus
    start = utc_now() - dt.timedelta(days=2)
    with tenant_transaction(p.clinic.ctx):
        old = Slot.objects.create(
            service_ref=p.clinic.service, location_ref=p.clinic.location,
            organization_ref=p.clinic.org, starts_at=start,
            ends_at=start + dt.timedelta(minutes=30), local_zone="Asia/Kolkata",
            provider_ref=p.doc)
    day = start.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    assert count_of(withdraw(p, day, day)) == 0
    with tenant_transaction(p.clinic.ctx):
        assert Slot.objects.get(pk=old.pk).status == SlotStatus.OPEN


def test_a_withdrawn_time_comes_back_when_the_same_time_is_created_again(p):
    make(p, count=2, days=1)
    withdraw(p)
    assert mine(p) == []
    r = make(p, count=2, days=1)                        # no clash with the withdrawn ones
    assert r.status_code == 201 and json.loads(r.content) == {"created": 2}
    assert len(mine(p)) == 2


def test_patients_no_longer_see_a_withdrawn_time_or_can_book_it(p):
    make(p, count=1, days=1)
    slot = mine(p)[0]["id"]
    withdraw(p)
    assert slot not in {s["id"] for s in p.open_slots()}
    assert book(p, slot, subject="pat").status_code in (404, 409, 422)


@pytest.mark.parametrize("first,last", [
    ("2027-09-08", "2027-09-06"),                                  # backwards
    ("2027-09-01", "2027-12-01"),                                  # longer than 62 days
    ("not-a-date", "2027-09-06"), ("2027-09-06", "2027-13-40"), ("", ""),
])
def test_a_bad_range_is_refused_and_nothing_changes(p, first, last):
    make(p, count=1, days=2)
    assert withdraw(p, first, last).status_code == 422
    assert len(mine(p)) == 2


def test_dry_run_must_be_a_real_true_or_false(p):
    assert withdraw(p, dry_run="yes").status_code == 422


def test_only_clinic_staff_may_do_it(p):
    make(p, count=1, days=1)
    for who in ("pat", "doc"):
        assert withdraw(p, subject=who).status_code == 403
    assert len(mine(p)) == 1


def test_another_customer_cannot_touch_these_times(p):
    make(p, count=1, days=1)
    other = Practice()
    r = other.post(f"services/{p.clinic.service}/slots/withdraw",
                   {"first_day": "2027-09-06", "last_day": "2027-09-06"}, subject="ops")
    assert r.status_code in (403, 404)
    assert len(mine(p)) == 1


def test_it_is_written_to_the_audit_trail_once_per_run(p):
    make(p, count=1, days=1)
    withdraw(p, dry_run=True)
    withdraw(p)
    with tenant_transaction(p.clinic.ctx):
        n = AuditEvent.objects.filter(action="slot.withdraw").count()
    assert n == 1                                                  # a dry run changes nothing
