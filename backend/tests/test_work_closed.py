import datetime as dt
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.workflow import api as workflow
from modules.workflow.models import WorkItem
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import reply, started

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    workflow.register_subscribers()


@pytest.fixture
def p():
    practice = Practice()
    practice.staff("ops2")
    return practice


def deliver():
    for _ in range(4):
        relay_once(dispatch.dispatch)


def open_items(p, subject="ops"):
    r = p.get("staff/work", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["items"]


def closed_items(p, days=14, subject="ops", extra=""):
    r = p.get(f"staff/work?closed={days}{extra}", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["items"]


def waiting(p):
    """A requested booking, and the id of the work item it opened."""
    booking = booked(p, confirm=False)
    deliver()
    (item,) = [i for i in open_items(p) if i["subject"] == booking]
    return booking, item["id"]


def finish(p, booking, how="confirm"):
    if how == "confirm":
        p.post(f"bookings/{booking}/confirm", subject="ops")
    else:
        p.post(f"bookings/{booking}/cancel", subject="pat")
    deliver()


def closed_ago(p, item, **delta):
    with tenant_transaction(p.clinic.ctx):
        WorkItem.objects.filter(public_id=item).update(
            closed_at=utc_now() - dt.timedelta(**delta))


# ------------------------------------------------------------------ what the list holds
def test_work_that_is_closed_moves_from_the_open_list_to_the_closed_list(p):
    booking, item = waiting(p)
    assert [i["id"] for i in open_items(p)] == [item] and closed_items(p) == []
    finish(p, booking)
    assert open_items(p) == []
    (row,) = closed_items(p)
    assert row["id"] == item and row["status"] == "done" and row["closed_at"]
    assert row["label"] == "Confirm an appointment" and row["kind"] == "confirm_booking"


def test_work_that_was_no_longer_needed_is_listed_with_that_result(p):
    booking, item = waiting(p)
    finish(p, booking, how="cancel")
    (row,) = closed_items(p)
    assert row["id"] == item and row["status"] == "cancelled"


def test_open_work_is_not_in_the_closed_list_and_says_it_is_open(p):
    booking, item = waiting(p)
    other, other_item = waiting(p)
    finish(p, other)
    assert [i["id"] for i in closed_items(p)] == [other_item]
    (still_open,) = open_items(p)
    assert still_open["id"] == item and still_open["status"] == "open"
    assert still_open["closed_at"] is None
    assert booking


def test_the_newest_closed_work_comes_first(p):
    first, first_item = waiting(p)
    second, second_item = waiting(p)
    third, third_item = waiting(p)
    finish(p, first)
    finish(p, second, how="cancel")
    finish(p, third)
    closed_ago(p, first_item, hours=5)
    closed_ago(p, second_item, hours=1)
    closed_ago(p, third_item, hours=3)
    assert [i["id"] for i in closed_items(p)] == [second_item, third_item, first_item]


def test_new_work_for_the_same_thing_is_open_while_the_old_one_stays_closed(p):
    thread = started(p)
    deliver()
    (first,) = [i for i in open_items(p) if i["kind"] == "answer_message"]
    reply(p, thread, subject="ops2")
    deliver()
    assert open_items(p) == []
    p.post(f"messages/threads/{thread}/reply", {"body": "One more question"}, subject="pat")
    deliver()
    (again,) = [i for i in open_items(p) if i["kind"] == "answer_message"]
    assert again["id"] != first["id"]
    assert [i["id"] for i in closed_items(p)] == [first["id"]]


# ------------------------------------------------------------------ how far back
@pytest.mark.parametrize("age,days,shown", [
    (dt.timedelta(days=13, hours=23), 14, True), (dt.timedelta(days=14, hours=1), 14, False),
    (dt.timedelta(days=10), 7, False), (dt.timedelta(days=10), 14, True),
    (dt.timedelta(days=29, hours=23), 30, True), (dt.timedelta(days=31), 30, False),
    (dt.timedelta(hours=23), 1, True), (dt.timedelta(hours=25), 1, False)])
def test_only_work_closed_inside_the_window_is_listed(p, age, days, shown):
    booking, item = waiting(p)
    finish(p, booking)
    closed_ago(p, item, seconds=age.total_seconds())
    assert (item in [i["id"] for i in closed_items(p, days)]) is shown


@pytest.mark.parametrize("value", ["0", "-1", "31", "99", "abc", "", "1.5", "7days"])
def test_a_bad_number_of_days_is_refused(p, value):
    assert p.get(f"staff/work?closed={value}", subject="ops").status_code == 422


@pytest.mark.parametrize("value", ["1", "7", "14", "30"])
def test_one_to_thirty_days_are_accepted(p, value):
    assert p.get(f"staff/work?closed={value}", subject="ops").status_code == 200


def test_the_service_keeps_days_and_limit_in_range(p):
    booking, item = waiting(p)
    finish(p, booking)
    closed_ago(p, item, days=20)
    with tenant_transaction(p.clinic.ctx):
        assert workflow.list_closed(p.clinic.org, 0) == []                     # 0 acts as 1 day
        assert [i["public_id"] for i in workflow.list_closed(p.clinic.org, 999)] == [item]   # to 30
        assert workflow.MAX_CLOSED_DAYS == 30
        assert len(workflow.list_closed(p.clinic.org, 30, limit=0)) == 1       # at least one


def test_the_list_is_cut_at_the_limit_keeping_the_newest(p):
    ids = []
    for hours in (6, 4, 2):
        booking, item = waiting(p)
        finish(p, booking)
        closed_ago(p, item, hours=hours)
        ids.append(item)
    with tenant_transaction(p.clinic.ctx):
        found = workflow.list_closed(p.clinic.org, 14, limit=2)
    assert [i["public_id"] for i in found] == [ids[2], ids[1]]


# ------------------------------------------------------------------ the history of closed work
def test_the_history_of_closed_work_can_be_reached_from_the_list(p):
    booking, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    finish(p, booking)
    (row,) = closed_items(p)
    r = p.get(f"staff/work/{row['id']}/history", subject="ops")
    assert r.status_code == 200
    view = json.loads(r.content)
    assert [e["event"] for e in view["events"]] == ["opened", "claimed", "done"]
    assert view["status"] == "done" and view["chain_ok"] is True


# ------------------------------------------------------------------ who may see it
def test_another_clinics_closed_work_is_never_listed(p):
    booking, item = waiting(p)
    finish(p, booking)
    other = Practice()
    other_booking = booked(other, confirm=False)
    deliver()
    other.post(f"bookings/{other_booking}/confirm", subject="ops")
    deliver()
    mine = [i["id"] for i in closed_items(p)]
    theirs = [i["id"] for i in closed_items(other)]
    assert mine == [item] and len(theirs) == 1 and theirs[0] != item


def test_patients_and_signed_out_visitors_cannot_list_closed_work(p):
    booking, _ = waiting(p)
    finish(p, booking)
    assert p.get("staff/work?closed=14", subject="pat").status_code == 403
    assert p.client.get("/api/v1/staff/work?closed=14").status_code == 401


def test_the_closed_list_holds_nothing_about_the_patient(p):
    booking, _ = waiting(p)
    finish(p, booking)
    rows = closed_items(p)
    assert p.person not in json.dumps(rows)
    # exactly these fields: the booking is only the pointer ("subject") that has always been there,
    # and a new field that could carry patient information would fail this test
    assert set(rows[0]) == {"id", "kind", "label", "subject", "created_at", "due_at", "status",
                            "closed_at", "overdue", "assigned_to", "assigned_to_name"}
    assert rows[0]["subject"] == booking


def test_asking_for_closed_work_ignores_the_mine_and_unassigned_filters(p):
    booking, item = waiting(p)
    finish(p, booking)
    assert [i["id"] for i in closed_items(p, extra="&mine=1")] == [item]
    assert [i["id"] for i in closed_items(p, extra="&unassigned=1")] == [item]


def test_asking_for_closed_work_leaves_a_decision_on_record(p):
    from modules.policy.models import AccessDecision
    with tenant_transaction(p.clinic.ctx):
        before = AccessDecision.objects.filter(
            operation="read:analytics", actor_ref=p.desk, allowed=True).count()
    closed_items(p)
    with tenant_transaction(p.clinic.ctx):
        after = AccessDecision.objects.filter(
            operation="read:analytics", actor_ref=p.desk, allowed=True).count()
    assert after == before + 1


def test_the_service_clamps_the_number_of_days_to_one_through_thirty(p):
    ids = {}
    ages = (("recent", {"hours": 2}), ("twenty", {"days": 20}), ("forty", {"days": 40}))
    for name, delta in ages:
        booking, item = waiting(p)
        finish(p, booking)
        closed_ago(p, item, **delta)
        ids[name] = item
    with tenant_transaction(p.clinic.ctx):
        def listed(days):
            return {i["public_id"] for i in workflow.list_closed(p.clinic.org, days)}

        # too small: zero and negative days act as one day, so something closed 2 hours ago shows
        assert listed(0) == {ids["recent"]} and listed(-5) == {ids["recent"]}
        # too large: capped at thirty days, so something closed 40 days ago never shows
        assert listed(999) == {ids["recent"], ids["twenty"]}
        assert listed(30) == {ids["recent"], ids["twenty"]}
        assert listed(1) == {ids["recent"]}
