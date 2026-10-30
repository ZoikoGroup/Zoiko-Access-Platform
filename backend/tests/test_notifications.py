import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from foundation.outbox import _to_published, emit, relay_once
from modules.comms import api as comms
from modules.comms.models import Notification
from modules.trust import api as trust
from tests.conftest import as_app_role
from tests.test_api import Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()           # idempotent: makes sure the real notifier is subscribed


@pytest.fixture
def web():
    return Web()


def deliver():
    """One pass of the event-delivery worker."""
    return relay_once(dispatch.dispatch)


def inbox_of(web, subject="pat"):
    r = web.get("notifications", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)


def staff_act(web, booking_id, verb):
    r = web.post(f"bookings/{booking_id}/{verb}", subject="ops")
    assert r.status_code == 200
    return r


# ------------------------------------------------------------------ the happy path
def test_a_booking_produces_notifications_for_the_patient_as_it_changes(web):
    web.patient("pat")
    web.staff("ops")
    booking = book(web, web.slots[0]["public_id"], subject="pat").json()["booking"]["id"]
    assert inbox_of(web)["notifications"] == []              # nothing until the worker runs
    assert deliver().published >= 1
    first = inbox_of(web)
    assert first["unread"] == 1
    n = first["notifications"][0]
    assert n["title"] == "Appointment requested" and n["kind"] == "booking_requested"
    assert "General check-up" in n["body"] and "North" in n["body"]
    assert "Tue 15 Jun 2027, 10:00 (Asia/Kolkata)" in n["body"]
    assert n["booking"] == booking and n["read"] is False

    staff_act(web, booking, "confirm")
    deliver()
    staff_act(web, booking, "cancel")
    deliver()
    titles = [x["title"] for x in inbox_of(web)["notifications"]]
    assert titles == ["Appointment cancelled", "Appointment confirmed", "Appointment requested"]
    assert inbox_of(web)["unread"] == 3


def test_marking_read_lowers_the_unread_count_and_repeating_is_harmless(web):
    web.patient("pat")
    book(web, web.slots[0]["public_id"], subject="pat")
    deliver()
    note = inbox_of(web)["notifications"][0]["id"]
    first = web.post(f"notifications/{note}/read", subject="pat")
    assert first.status_code == 200 and json.loads(first.content)["notification"]["read"] is True
    assert inbox_of(web)["unread"] == 0
    assert web.post(f"notifications/{note}/read", subject="pat").status_code == 200
    with tenant_transaction(web.clinic.ctx):
        row = Notification.objects.get(public_id=note)
        assert row.read_at is not None and row.entity_version == 2     # changed once, not twice


# ------------------------------------------------------------------ who sees what
def test_only_the_patient_sees_their_notifications(web):
    web.patient("pat")
    web.patient("stranger")
    web.staff("ops")
    book(web, web.slots[0]["public_id"], subject="pat")
    deliver()
    note = inbox_of(web, "pat")["notifications"][0]["id"]
    assert inbox_of(web, "stranger") == {"notifications": [], "unread": 0}
    assert inbox_of(web, "ops") == {"notifications": [], "unread": 0}   # staff reach no patient
    refused = web.post(f"notifications/{note}/read", subject="stranger")
    assert refused.status_code == 403
    assert json.loads(refused.content)["code"] == "SUBJECT_LINK_UNVERIFIED"
    assert web.post("notifications/doesnotexist/read", subject="pat").status_code == 404
    assert inbox_of(web, "pat")["unread"] == 1                          # still unread


def test_a_guardian_with_an_approved_proxy_sees_the_childs_notifications(web):
    guardian = web.login("guardian@example.org", trust.ActorType.PROXY, "R02")
    approver = web.staff("ops")
    web.patient("pat")
    with tenant_transaction(web.clinic.ctx):
        child = trust.create_person().public_id
        grant = trust.propose_proxy_grant(
            delegate_public_id=guardian, person_public_id=child,
            relationship_type="parent", by="system")
        trust.approve_proxy_grant(grant.public_id, approver)
    r = book(web, web.slots[0]["public_id"], subject="guardian@example.org", person=child)
    assert r.status_code == 201
    deliver()
    assert len(inbox_of(web, "guardian@example.org")["notifications"]) == 1
    assert inbox_of(web, "pat")["notifications"] == []                  # not the stranger's


# ------------------------------------------------------------------ privacy and robustness
def test_events_carry_no_personal_text_only_the_notification_does(web):
    web.patient("pat")
    book(web, web.slots[0]["public_id"], subject="pat")
    with tenant_transaction(web.clinic.ctx):
        payload = json.dumps(list(OutboxEvent.objects.values_list("payload", flat=True)))
    for text in ("General check-up", "North", "2027", "Asia/Kolkata", "requested the clinic"):
        assert text not in payload


def test_delivering_twice_never_creates_a_second_notification(web):
    web.patient("pat")
    book(web, web.slots[0]["public_id"], subject="pat")
    deliver()
    deliver()
    with tenant_transaction(web.clinic.ctx):
        row = OutboxEvent.objects.filter(event_type="booking.requested.v1").get()
    dispatch.dispatch(_to_published(row))                               # a duplicate delivery
    assert len(inbox_of(web)["notifications"]) == 1


def test_an_event_for_a_booking_that_is_gone_or_an_unknown_event_is_just_delivered(web):
    web.patient("pat")
    with tenant_transaction(web.clinic.ctx):
        emit(event_type="booking.confirmed.v1", aggregate_type="booking",
             aggregate_ref="missing", payload={"booking_ref": "does-not-exist"})
        emit(event_type="booking.noted.v1", aggregate_type="booking", aggregate_ref="other",
             payload={"booking_ref": "x"})
    result = deliver()
    assert result.published == 2 and result.retrying == 0 and result.quarantined == 0
    assert inbox_of(web)["notifications"] == []


def test_the_notifier_is_registered_once_and_survives_registering_again(web):
    comms.register_subscribers()
    comms.register_subscribers()
    names = [s.consumer for s in dispatch.subscriptions()]
    assert names.count("comms.notifier") == 1


def test_it_works_as_the_restricted_app_role(web):
    _, person = web.patient("pat")
    book(web, web.slots[0]["public_id"], subject="pat")
    deliver()
    with tenant_transaction(web.clinic.ctx), as_app_role():
        assert len(comms.list_for_persons([person])) == 1
        assert comms.unread_count([person]) == 1
    other = Web()
    with tenant_transaction(other.clinic.ctx), as_app_role():
        assert comms.list_for_persons([person]) == []                   # another tenant sees none


def test_marking_read_needs_a_receipt_for_exactly_that_patient(web):
    from foundation.cell_context import bind_context
    from foundation.errors import ErrorCode, ZoikoError
    from modules.policy.api import authorize

    web.patient("pat")
    other_actor, other_person = web.patient("stranger")
    book(web, web.slots[0]["public_id"], subject="pat")
    deliver()
    note = inbox_of(web)["notifications"][0]["id"]
    with bind_context(web.clinic.ctx):               # a real ALLOW, but for the stranger's record
        wrong = authorize(actor_public_id=other_actor, operation="write:comms",
                          purpose_code="SELF_SERVICE", subject_ref=other_person)
    with tenant_transaction(web.clinic.ctx), pytest.raises(ZoikoError) as excinfo:
        comms.mark_read(notification_ref=note, actor_public_id=other_actor, decision=wrong)
    assert excinfo.value.code == ErrorCode.POLICY_DENIED
    assert inbox_of(web, "pat")["unread"] == 1
