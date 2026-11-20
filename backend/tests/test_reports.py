import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.scheduling.models import Slot
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


def report(p, subject="ops", days=None):
    r = p.get("staff/reports" + (f"?days={days}" if days else ""), subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def counts(block):
    """Just the plain counts of a report block (not the response-time summary)."""
    return {k: v for k, v in block.items() if isinstance(v, int)}


def make_booking(p, confirm=True):
    if len([s for s in p.open_slots() if s["provider"]]) == 0:
        p.add_times(p.doc, count=6)
    slot = p.slot_of("doc")
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    if confirm:
        p.post(f"bookings/{booking}/confirm", subject="ops")
    return booking, slot


def in_the_past(p, slot_id, days_ago):
    start = utc_now() - dt.timedelta(days=days_ago)
    with tenant_transaction(p.clinic.ctx):
        Slot.objects.filter(public_id=slot_id).update(
            starts_at=start, ends_at=start + dt.timedelta(minutes=30))


# ------------------------------------------------------------------ the numbers
def test_a_quiet_clinic_reports_zeroes_and_one_entry_per_day(p):
    r = report(p)
    assert r["days"] == 30 and len(r["bookings"]["per_day"]) == 30
    assert r["bookings"]["total"] == 0 and sum(d["count"] for d in r["bookings"]["per_day"]) == 0
    assert r["bookings"]["status"] == {"requested": 0, "confirmed": 0, "cancelled": 0}
    assert r["referrals"]["received"] == 0 and r["waiting_list"]["joined"] == 0
    assert r["messages"]["started"] == 0 and r["bookings"]["busiest_services"] == []


def test_bookings_are_counted_per_day_and_by_status(p):
    make_booking(p, confirm=True)
    make_booking(p, confirm=False)
    cancelled, _ = make_booking(p, confirm=False)
    p.post(f"bookings/{cancelled}/cancel", subject="pat")
    r = report(p)["bookings"]
    assert r["total"] == 3 and r["status"] == {"requested": 1, "confirmed": 1, "cancelled": 1}
    assert r["per_day"][-1]["count"] == 3                              # all made today
    assert sum(d["count"] for d in r["per_day"]) == 3
    assert r["busiest_services"] == [{"name": "General check-up", "count": 3}]


def test_attendance_counts_only_past_confirmed_appointments(p):
    attended, a_slot = make_booking(p)
    missed, m_slot = make_booking(p)
    unrecorded, u_slot = make_booking(p)
    future, _ = make_booking(p)                                         # not held yet: not counted
    waiting, w_slot = make_booking(p, confirm=False)                    # never confirmed
    gone, g_slot = make_booking(p, confirm=False)                       # cancelled
    p.post(f"bookings/{gone}/cancel", subject="pat")
    for slot in (a_slot, m_slot, u_slot, w_slot, g_slot):
        in_the_past(p, slot, days_ago=2)
    p.post(f"bookings/{attended}/attendance", {"outcome": "attended"}, subject="ops")
    p.post(f"bookings/{missed}/attendance", {"outcome": "no_show"}, subject="ops")
    assert future and unrecorded and waiting
    assert report(p)["bookings"]["attendance"] == {"attended": 1, "no_show": 1, "not_recorded": 1}


def test_the_period_can_be_changed_and_old_bookings_fall_out(p):
    booking, slot = make_booking(p)
    from modules.scheduling.models import Booking
    with tenant_transaction(p.clinic.ctx):
        old = utc_now() - dt.timedelta(days=20)
        Booking.objects.filter(public_id=booking).update(created_at=old)
    assert report(p, days=30)["bookings"]["total"] == 1
    assert report(p, days=7)["bookings"]["total"] == 0
    assert len(report(p, days=7)["bookings"]["per_day"]) == 7
    assert slot


def test_referrals_waiting_list_and_messages_are_counted(p):
    make_booking(p)                                                     # gives the doctor a link
    made = p.post("services", {"organization": p.clinic.org, "name": "Cardiology",
                               "category": "cardiology", "duration_minutes": 30}, subject="ops")
    cardio = json.loads(made.content)["service"]["id"]
    publish_service(p.clinic.ctx, cardio)
    sent = json.loads(p.post("clinician/referrals", {
        "patient": p.person, "service": cardio, "reason": "Chest pain", "urgency": "routine"},
        subject="doc").content)["referral"]["id"]
    p.post(f"referrals/{sent}/decide", {"decision": "accepted"}, subject="doc2")
    p.post("waitlist", {"service": cardio}, subject="pat")
    thread = json.loads(p.post("messages/threads", {"subject": "Hello", "body": "A question"},
                               subject="pat").content)["thread"]["id"]
    r = report(p)
    assert r["referrals"] == {"received": 1, "issued": 0, "accepted": 1, "needs_info": 0,
                              "declined": 0}
    assert r["waiting_list"] == {"joined": 1, "booked": 0, "left": 0, "waiting_now": 1}
    assert counts(r["messages"]) == {
        "started": 1, "closed": 0, "waiting_for_clinic": 1}
    p.post(f"messages/threads/{thread}/close", subject="ops")
    assert counts(report(p)["messages"]) == {
        "started": 1, "closed": 1, "waiting_for_clinic": 0}


# ------------------------------------------------------------------ privacy and access
def test_the_report_identifies_no_patient(p):
    p.post(f"people/{p.person}/profile", {"name": "Asha Rao"}, subject="pat")
    make_booking(p)
    p.post("messages/threads", {"subject": "Private subject", "body": "Private text"},
           subject="pat")
    text = json.dumps(report(p))
    assert p.person not in text and "Asha" not in text
    assert "Private" not in text


def test_only_clinic_staff_can_see_reports(p):
    for who in ("pat", "doc"):
        assert p.get("staff/reports", subject=who).status_code == 403


def test_another_customers_numbers_are_separate(p):
    make_booking(p)
    outsider = Web()
    outsider.staff("desk")
    r = outsider.get("staff/reports", subject="desk")
    assert json.loads(r.content)["bookings"]["total"] == 0


def test_another_site_of_the_same_customer_does_not_see_these_numbers(p):
    make_booking(p)
    from modules.trust import api as trust
    with tenant_transaction(p.clinic.ctx):
        site = trust.create_organization(name="Other Site").public_id
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="desk2")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=site,
                          role_code="R04", by="system")
    assert report(p, "desk2")["bookings"]["total"] == 0
    assert report(p)["bookings"]["total"] == 1


@pytest.mark.parametrize("days", ["0", "6", "91", "abc", "-5", "30.5"])
def test_a_bad_period_is_refused(p, days):
    assert p.get(f"staff/reports?days={days}", subject="ops").status_code == 422


# ------------------------------------------------------------------ response times
def hours_ago(hours):
    return utc_now() - dt.timedelta(hours=hours)


def test_no_confirmations_means_no_median_not_zero(p):
    make_booking(p, confirm=False)
    empty = {"count": 0, "median": None, "average": None}
    assert report(p)["bookings"]["hours_to_confirm"] == empty


def test_time_to_confirm_is_measured_from_when_the_booking_was_made(p):
    from modules.scheduling.models import Booking
    booking, _ = make_booking(p, confirm=False)
    with tenant_transaction(p.clinic.ctx):
        Booking.objects.filter(public_id=booking).update(created_at=hours_ago(3))
    p.post(f"bookings/{booking}/confirm", subject="ops")
    got = report(p)["bookings"]["hours_to_confirm"]
    assert got["count"] == 1 and 2.9 <= got["median"] <= 3.2


def test_unconfirmed_and_cancelled_bookings_and_repeat_confirms_do_not_change_it(p):
    from modules.scheduling.models import Booking
    first, _ = make_booking(p, confirm=False)
    with tenant_transaction(p.clinic.ctx):
        Booking.objects.filter(public_id=first).update(created_at=hours_ago(2))
    p.post(f"bookings/{first}/confirm", subject="ops")
    make_booking(p, confirm=False)                                      # still waiting
    gone, _ = make_booking(p, confirm=False)
    p.post(f"bookings/{gone}/cancel", subject="pat")                    # never confirmed
    p.post(f"bookings/{first}/confirm", subject="ops")                  # confirming again
    got = report(p)["bookings"]["hours_to_confirm"]
    assert got["count"] == 1 and 1.9 <= got["median"] <= 2.2


def test_a_move_made_already_confirmed_is_not_counted_as_a_quick_confirmation(p):
    booking, _ = make_booking(p, confirm=True)
    before = report(p)["bookings"]["hours_to_confirm"]["count"]
    target = p.slot_of("doc")
    moved = p.post(f"bookings/{booking}/reschedule", {"slot": target}, subject="ops")
    assert moved.status_code == 200
    assert json.loads(moved.content)["booking"]["status"] == "confirmed"
    assert report(p)["bookings"]["hours_to_confirm"]["count"] == before


def test_time_to_first_reply_uses_the_first_answer_only(p):
    from modules.messaging.models import Thread
    thread = json.loads(p.post("messages/threads", {"subject": "Hello", "body": "Question"},
                               subject="pat").content)["thread"]["id"]
    assert report(p)["messages"]["hours_to_first_reply"]["count"] == 0
    with tenant_transaction(p.clinic.ctx):
        Thread.objects.filter(public_id=thread).update(created_at=hours_ago(5))
    p.post(f"messages/threads/{thread}/reply", {"body": "Answer one"}, subject="ops")
    first = report(p)["messages"]["hours_to_first_reply"]
    assert first["count"] == 1 and 4.9 <= first["median"] <= 5.2
    with tenant_transaction(p.clinic.ctx):
        stored = Thread.objects.get(public_id=thread).first_reply_at
    p.post(f"messages/threads/{thread}/reply", {"body": "Answer two"}, subject="ops")
    with tenant_transaction(p.clinic.ctx):
        assert Thread.objects.get(public_id=thread).first_reply_at == stored    # exactly the same
    assert report(p)["messages"]["hours_to_first_reply"] == first
