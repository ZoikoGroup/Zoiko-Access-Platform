import datetime as dt
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.trust import api as trust
from modules.workflow import api as workflow
from modules.workflow import jobs
from modules.workflow.models import WorkItem
from tests.test_attention import booked, empty_service
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
    practice.name_referral_owner()
    return practice


def deliver():
    for _ in range(4):
        relay_once(dispatch.dispatch)


def items(p, query="", subject="ops"):
    r = p.get(f"staff/work{query}", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["items"]


def kinds(p):
    return sorted(i["kind"] for i in items(p))


def refer(p):
    cardio = empty_service(p)
    p.post("clinician/referrals", {"patient": p.person, "service": cardio, "reason": "Chest pain",
                                   "urgency": "routine"}, subject="doc")
    return json.loads(p.get("referrals/incoming", subject="ops").content)["referrals"][0]["id"]


def test_an_empty_clinic_has_no_work(p):
    deliver()
    assert items(p) == []


def test_a_requested_booking_opens_work_and_confirming_it_closes_it(p):
    booking = booked(p, confirm=False)
    deliver()
    (item,) = items(p)
    assert item["kind"] == "confirm_booking" and item["subject"] == booking
    assert item["label"] == "Confirm an appointment" and item["overdue"] is False
    p.post(f"bookings/{booking}/confirm", subject="ops")
    deliver()
    assert items(p) == []


def test_cancelling_a_waiting_booking_closes_the_work(p):
    booking = booked(p, confirm=False)
    deliver()
    assert kinds(p) == ["confirm_booking"]
    p.post(f"bookings/{booking}/cancel", subject="pat")
    deliver()
    assert items(p) == []


def test_a_referral_opens_work_until_it_is_decided(p):
    booked(p, confirm=True)                                    # gives the doctor a care link
    deliver()
    assert items(p) == []                                      # a confirmed booking is no work
    referral = refer(p)
    deliver()
    assert kinds(p) == ["decide_referral"]
    p.post(f"referrals/{referral}/decide", {"decision": "accepted"}, subject="doc2")
    deliver()
    assert items(p) == []


def test_a_patient_message_opens_work_until_the_clinic_answers(p):
    thread = started(p)
    deliver()
    (item,) = items(p)
    assert item["kind"] == "answer_message" and item["subject"] == thread
    reply(p, thread)
    deliver()
    assert items(p) == []
    started_again = reply(p, thread, "thanks", subject="pat")      # the patient writes back
    assert started_again.status_code == 200
    deliver()
    assert kinds(p) == ["answer_message"]


def test_delivering_the_same_work_twice_does_not_duplicate_it(p):
    started(p)
    deliver()
    deliver()
    assert kinds(p) == ["answer_message"]
    reply(p, started(p, subject="Second"), "x", subject="pat")
    deliver()
    assert len(items(p)) == 2                                  # two different threads, one each


def test_work_is_due_after_the_response_target_and_flags_when_overdue(p, settings):
    settings.RESPONSE_TARGET_HOURS = 5
    booked(p, confirm=False)
    deliver()
    (item,) = items(p)
    due = dt.datetime.fromisoformat(item["due_at"])
    assert abs((due - (utc_now() + dt.timedelta(hours=5))).total_seconds()) < 120
    with tenant_transaction(p.clinic.ctx):
        WorkItem.objects.update(due_at=utc_now() - dt.timedelta(minutes=1))
    assert items(p)[0]["overdue"] is True


def test_the_one_due_first_is_listed_first(p):
    started(p, subject="First")
    booked(p, confirm=False)
    deliver()
    with tenant_transaction(p.clinic.ctx):
        WorkItem.objects.filter(kind="confirm_booking").update(
            due_at=utc_now() - dt.timedelta(hours=3))
    assert [i["kind"] for i in items(p)] == ["confirm_booking", "answer_message"]


def test_taking_work_assigns_it_and_the_filters_find_it(p):
    started(p)
    deliver()
    item = items(p)[0]["id"]
    assert items(p, "?unassigned=1")[0]["id"] == item and items(p, "?mine=1") == []
    taken = p.post(f"staff/work/{item}/claim", subject="ops")
    assert taken.status_code == 200
    got = json.loads(taken.content)["item"]
    assert got["assigned_to"] == p.desk and got["assigned_to_name"] == "ops"
    assert items(p, "?mine=1")[0]["id"] == item and items(p, "?unassigned=1") == []


def test_taking_the_same_item_again_is_harmless_but_a_second_person_is_refused(p):
    started(p)
    deliver()
    item = items(p)[0]["id"]
    assert p.post(f"staff/work/{item}/claim", subject="ops").status_code == 200
    assert p.post(f"staff/work/{item}/claim", subject="ops").status_code == 200
    p.staff("ops2")
    assert p.post(f"staff/work/{item}/claim", subject="ops2").status_code == 409
    assert items(p)[0]["assigned_to"] == p.desk


def test_only_the_holder_can_give_it_back(p):
    started(p)
    deliver()
    item = items(p)[0]["id"]
    p.post(f"staff/work/{item}/claim", subject="ops")
    p.staff("ops2")
    assert p.post(f"staff/work/{item}/release", subject="ops2").status_code == 409
    assert p.post(f"staff/work/{item}/release", subject="ops").status_code == 200
    assert items(p)[0]["assigned_to"] == ""
    assert p.post(f"staff/work/{item}/release", subject="ops").status_code == 409   # not held now


def test_work_that_was_closed_cannot_be_taken(p):
    thread = started(p)
    deliver()
    item = items(p)[0]["id"]
    reply(p, thread)
    deliver()
    assert p.post(f"staff/work/{item}/claim", subject="ops").status_code == 409


def test_taking_work_is_written_to_the_audit_trail(p):
    from modules.evidence.models import AuditEvent
    started(p)
    deliver()
    item = items(p)[0]["id"]
    p.post(f"staff/work/{item}/claim", subject="ops")
    p.post(f"staff/work/{item}/release", subject="ops")
    with tenant_transaction(p.clinic.ctx):
        actions = list(AuditEvent.objects.filter(object_ref=item).values_list("action", flat=True))
    assert sorted(actions) == ["workitem.claim", "workitem.release"]


def test_the_queue_holds_no_patient_data(p):
    booked(p, confirm=False)
    started(p)
    deliver()
    body = p.get("staff/work", subject="ops").content.decode()
    assert p.person not in body and p.patient_actor not in body
    assert "Question about my appointment" not in body and "dizzy" not in body


def test_only_clinic_staff_may_use_the_queue(p):
    started(p)
    deliver()
    item = items(p)[0]["id"]
    for who in ("pat", "doc"):
        assert p.get("staff/work", subject=who).status_code == 403
        assert p.post(f"staff/work/{item}/claim", subject=who).status_code == 403


def test_another_customers_staff_see_and_take_nothing(p):
    started(p)
    deliver()
    item = items(p)[0]["id"]
    other = Practice()
    deliver()
    assert items(other) == []
    assert other.post(f"staff/work/{item}/claim", subject="ops").status_code == 404


def test_another_site_of_the_same_customer_sees_and_takes_nothing(p):
    started(p)
    deliver()
    item = items(p)[0]["id"]
    with tenant_transaction(p.clinic.ctx):
        site = trust.create_organization(name="Other Site").public_id
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="desk2")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=site,
                          role_code="R04", by="system")
    assert items(p, subject="desk2") == []
    assert p.post(f"staff/work/{item}/claim", subject="desk2").status_code == 404
    assert len(items(p)) == 1


def sync(p):
    with tenant_transaction(p.clinic.ctx):
        return jobs.sync_tenant()


def test_work_that_existed_before_the_queue_is_found_by_the_sync(p):
    booked(p, confirm=False)
    started(p)
    booked(p, confirm=True)                                     # a handled booking is not work
    assert items(p) == []                                       # no events delivered yet
    assert sync(p) == 2
    assert kinds(p) == ["answer_message", "confirm_booking"]


def test_a_referral_waiting_for_a_decision_is_found_by_the_sync(p):
    booked(p, confirm=True)
    refer(p)
    sync(p)
    assert kinds(p) == ["decide_referral"]


def test_running_the_sync_again_or_after_the_events_changes_nothing(p):
    booked(p, confirm=False)
    sync(p)
    sync(p)
    deliver()                                                   # the real events arrive later
    sync(p)
    assert kinds(p) == ["confirm_booking"]


def test_the_sync_does_not_reopen_work_that_was_handled(p):
    booking = booked(p, confirm=False)
    sync(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    deliver()
    assert items(p) == []
    sync(p)
    assert items(p) == []


def test_a_synced_item_is_due_the_target_after_the_request_was_made(p, settings):
    settings.RESPONSE_TARGET_HOURS = 24
    booking = booked(p, confirm=False)
    from modules.scheduling.models import Booking
    with tenant_transaction(p.clinic.ctx):
        Booking.objects.filter(public_id=booking).update(
            created_at=utc_now() - dt.timedelta(hours=30))
    sync(p)
    assert items(p)[0]["overdue"] is True                       # waited 30 h against a 24 h target
