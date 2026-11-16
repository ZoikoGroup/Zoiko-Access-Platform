"""A data request opens work in the clinic's queue, due when the country pack says (DEV-140): it is
opened by an event, closed when the request ends, counted as late once its date passes,
and picked up by the sync job if the event was lost."""
import datetime as dt
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from foundation.outbox import relay_once
from modules.trust import api as trust
from modules.workflow import api as workflow
from modules.workflow import jobs
from modules.workflow.models import Kind, Status, WorkItem
from tests.test_api import Web
from tests.test_clinician import Practice
from tests.test_country_deadline import file_request, set_country, world  # noqa: F401
from tests.test_packs import full, signed

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    workflow.register_subscribers()


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def deliver():
    for _ in range(4):
        relay_once(dispatch.dispatch)


def queue(p, subject="ops"):
    r = p.get("staff/work", subject=subject)
    assert r.status_code == 200, r.content
    return [i for i in json.loads(r.content)["items"] if i["kind"] == "answer_data_request"]


def rows(p):
    with tenant_transaction(p.clinic.ctx):
        return list(WorkItem.objects.filter(kind=Kind.ANSWER_DATA_REQUEST))


def step(p, case, name, who="ops", note=""):
    return p.post(f"staff/rights/{case}/{name}", {"note": note}, subject=who)


def hours_until(iso):
    return (dt.datetime.fromisoformat(iso) - dt.datetime.now(dt.UTC)).total_seconds() / 3600


# ------------------------------------------------------------------ opening
def test_a_data_request_opens_one_item_in_the_clinics_queue(p):
    file_request(p)
    deliver()
    deliver()
    [item] = queue(p)
    assert item["label"] == "Answer a data request" and item["overdue"] is False
    assert len(rows(p)) == 1


def test_without_a_pack_the_item_is_due_on_the_clinics_own_response_target(p, settings):
    file_request(p)
    deliver()
    [item] = queue(p)
    target = settings.RESPONSE_TARGET_HOURS        # the clinic's own default, in hours
    assert target - 1 < hours_until(item["due_at"]) < target + 1


def test_with_a_signed_pack_the_item_is_due_on_the_date_the_request_was_given(world, p):  # noqa: F811
    signed(world, ref="gb-care-navigation", manifest=full(days=30))
    set_country(world, p, "GB")
    made = file_request(p)
    deliver()
    [item] = queue(p)
    assert abs(dt.datetime.fromisoformat(item["due_at"])
               - dt.datetime.fromisoformat(made["due_at"])) < dt.timedelta(seconds=2)
    assert 29 * 24 < hours_until(item["due_at"]) < 30 * 24 + 1


def test_the_event_carries_a_reference_only_and_the_item_none_of_the_patients_words(p):
    p.post("rights", {"type": "access", "note": "SECRET WORDS ABOUT ME"}, subject="pat")
    with tenant_transaction(p.clinic.ctx):
        events = list(OutboxEvent.objects.filter(event_type="rights.requested.v1"))
    assert len(events) == 1 and list(events[0].payload) == ["case_ref"]
    deliver()
    assert "SECRET" not in json.dumps(queue(p))


# ------------------------------------------------------------------ closing
def test_withdrawing_the_request_cancels_the_item(p):
    made = file_request(p)
    deliver()
    assert p.post(f"rights/{made['id']}/withdraw", subject="pat").status_code == 200
    deliver()
    assert queue(p) == []
    [row] = rows(p)
    assert row.status == Status.CANCELLED


def test_completing_the_request_finishes_the_item(p):
    made = file_request(p)
    deliver()
    step(p, made["id"], "verify")
    assert step(p, made["id"], "complete", "boss", "Done").status_code == 200
    deliver()
    assert queue(p) == []
    assert rows(p)[0].status == Status.DONE


def test_declining_the_request_also_finishes_the_item(p):
    made = file_request(p, "erasure")
    deliver()
    assert step(p, made["id"], "decline", "boss", "We must keep that record").status_code == 200
    deliver()
    assert rows(p)[0].status == Status.DONE


def test_starting_or_checking_identity_leaves_the_item_open(p):
    made = file_request(p)
    deliver()
    step(p, made["id"], "start")
    step(p, made["id"], "verify")
    deliver()
    assert len(queue(p)) == 1


# ------------------------------------------------------------------ late
def test_the_item_is_late_once_its_date_has_passed(p):
    file_request(p)
    deliver()
    with tenant_transaction(p.clinic.ctx):
        WorkItem.objects.filter(kind=Kind.ANSWER_DATA_REQUEST).update(
            due_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    [item] = queue(p)
    assert item["overdue"] is True


# ------------------------------------------------------------------ the sync job
def test_the_sync_job_opens_an_item_for_a_request_whose_event_was_lost(p):
    made = file_request(p)                            # nothing delivered yet: the event is waiting
    assert queue(p) == []
    with tenant_transaction(p.clinic.ctx):
        assert jobs.sync_tenant() >= 1
        assert jobs.sync_tenant() >= 1                # a second pass changes nothing
    assert len(queue(p)) == 1
    deliver()                                         # the late event finds the item already open
    assert len(queue(p)) == 1 and made["id"]


def test_the_sync_job_ignores_requests_that_have_ended(p):
    made = file_request(p)
    p.post(f"rights/{made['id']}/withdraw", subject="pat")
    with tenant_transaction(p.clinic.ctx):
        jobs.sync_tenant()
    assert queue(p) == []


# ------------------------------------------------------------------ who sees it
def test_another_customers_staff_see_none_of_it_and_doctors_see_the_queue_rules_unchanged(p):
    file_request(p)
    deliver()
    outsider = Web()
    outsider.staff("desk")
    got = json.loads(outsider.get("staff/work", subject="desk").content)["items"]
    assert [i for i in got if i["kind"] == "answer_data_request"] == []
    assert p.get("staff/work", subject="pat").status_code == 403


# ------------------------------------------------------ the dashboard and the daily email
def go_past_date(p, hours=1):
    from modules.rights.models import RightsCase
    with tenant_transaction(p.clinic.ctx):
        RightsCase.objects.update(due_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours))


def late_numbers(p):
    r = p.get("staff/dashboard", subject="ops")
    assert r.status_code == 200, r.content
    return json.loads(r.content)["late"]


def test_the_dashboard_counts_data_requests_past_their_date(p):
    file_request(p)
    assert late_numbers(p)["data_requests"] == 0                # not due yet (own target applies)
    go_past_date(p)
    assert late_numbers(p)["data_requests"] == 1


def test_a_request_with_no_date_is_late_after_the_clinics_own_target(p):
    from modules.rights.models import RightsCase
    file_request(p)
    with tenant_transaction(p.clinic.ctx):
        RightsCase.objects.update(
            created_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=30))   # past 24 hours
    assert late_numbers(p)["data_requests"] == 1


def test_a_request_not_yet_past_its_date_or_already_ended_is_not_late(p):
    made = file_request(p)
    from modules.rights.models import RightsCase
    with tenant_transaction(p.clinic.ctx):
        RightsCase.objects.update(due_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=5))
    assert late_numbers(p)["data_requests"] == 0
    go_past_date(p)
    p.post(f"rights/{made['id']}/withdraw", subject="pat")
    assert late_numbers(p)["data_requests"] == 0


def test_the_daily_email_mentions_late_data_requests_with_a_count_only(p):
    from foundation.models import QueuedEmail
    from modules.comms import digest
    from tests.test_digest import give_email
    give_email(p, p.desk, "desk@clinic.test")
    p.post("rights", {"type": "access", "note": "SECRET WORDS ABOUT ME"}, subject="pat")
    with tenant_transaction(p.clinic.ctx):
        assert digest.send_late_digest() == 0                   # nothing late yet
    go_past_date(p)
    with tenant_transaction(p.clinic.ctx):
        assert digest.send_late_digest() == 1
        (mail,) = QueuedEmail.objects.filter(kind=digest.KIND)
    assert "1 data request past the date to answer" in mail.body
    assert "SECRET" not in mail.body and p.person not in mail.body
