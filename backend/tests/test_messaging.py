import json

import pytest
from django.db import connection

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from foundation.outbox import relay_once
from modules.comms import api as comms
from modules.evidence.models import AuditEvent
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

SECRET = "I have been dizzy since Tuesday, my tablet number is XJ-4471"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


@pytest.fixture
def p():
    return Practice()


def write(p, who="pat", **over):
    body = {"subject": "Question about my appointment", "body": SECRET, **over}
    return p.post("messages/threads", body, subject=who)


def threads(p, subject="pat"):
    r = p.get("messages/threads", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["threads"]


def opened(p, thread, subject="pat"):
    r = p.get(f"messages/threads/{thread}", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["thread"]


def started(p, **over):
    r = write(p, **over)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["thread"]["id"]


def reply(p, thread, body="Please come in on Thursday.", subject="ops"):
    return p.post(f"messages/threads/{thread}/reply", {"body": body}, subject=subject)


# ------------------------------------------------------------------ the conversation
def test_a_patient_writes_to_the_clinic_and_the_clinic_sees_it_unread(p):
    thread = started(p)
    seen = threads(p, "ops")
    assert [t["id"] for t in seen] == [thread]
    assert seen[0]["subject"] == "Question about my appointment" and seen[0]["unread"] == 1
    assert seen[0]["status"] == "open"


def test_opening_a_thread_shows_the_messages_and_marks_it_read(p):
    thread = started(p)
    first = opened(p, thread, "ops")
    assert [m["body"] for m in first["messages"]] == [SECRET]
    assert first["messages"][0]["side"] == "patient" and first["messages"][0]["mine"] is False
    assert threads(p, "ops")[0]["unread"] == 0


def test_a_back_and_forth_keeps_order_and_unread_counts_per_side(p):
    thread = started(p)
    opened(p, thread, "ops")
    assert reply(p, thread).status_code == 200
    assert threads(p, "pat")[0]["unread"] == 1 and threads(p, "ops")[0]["unread"] == 0
    first = opened(p, thread, "pat")
    assert [m["side"] for m in first["messages"]] == ["patient", "staff"]
    assert first["messages"][1]["mine"] is False and first["messages"][0]["mine"] is True
    assert threads(p, "pat")[0]["unread"] == 0
    assert reply(p, thread, "Thank you", subject="pat").status_code == 200
    assert threads(p, "ops")[0]["unread"] == 1


def test_the_clinic_can_close_a_thread_and_then_nobody_can_reply(p):
    thread = started(p)
    assert p.post(f"messages/threads/{thread}/close", subject="ops").status_code == 200
    assert p.post(f"messages/threads/{thread}/close", subject="ops").status_code == 200
    assert threads(p)[0]["status"] == "closed"
    assert reply(p, thread).status_code == 409
    assert reply(p, thread, "More", subject="pat").status_code == 409


def test_the_dashboard_counts_threads_waiting_for_the_clinic(p):
    thread = started(p)
    first = json.loads(p.get("staff/dashboard", subject="ops").content)["attention"]
    assert first["messages"] == 1
    opened(p, thread, "ops")
    assert json.loads(p.get("staff/dashboard", subject="ops").content)["attention"]["messages"] == 0


# ------------------------------------------------------------------ privacy
def test_subject_and_text_are_stored_encrypted_and_never_in_events_or_audit(p):
    thread = started(p)
    reply(p, thread, "Your tablet XJ-4471 is fine")
    with connection.cursor() as cur:
        cur.execute("select subject_enc from messaging_thread")
        cells = [r[0] for r in cur.fetchall()]
        cur.execute("select body_enc from messaging_message")
        cells += [r[0] for r in cur.fetchall()]
    assert cells and all(c.startswith("zf1:") for c in cells)
    assert not any("dizzy" in c or "XJ-4471" in c or "appointment" in c for c in cells)
    with tenant_transaction(p.clinic.ctx):
        payloads = json.dumps([e.payload for e in OutboxEvent.objects.filter(
            event_type="message.sent.v1")])
        audit = json.dumps([str(e.details) for e in AuditEvent.objects.filter(
            action__startswith="message.")])
    assert "dizzy" not in payloads + audit and "XJ-4471" not in payloads + audit


def test_a_message_copied_to_another_row_reads_as_blank(p):
    from modules.messaging.models import Message
    first = started(p)
    second = started(p, subject="Another", body="Different text")
    opened(p, first, "ops")
    with tenant_transaction(p.clinic.ctx):
        a = Message.objects.filter(thread__public_id=first).get()
        b = Message.objects.filter(thread__public_id=second).get()
        b.body_enc = a.body_enc                          # a stolen ciphertext
        b.save(update_fields=["body_enc"])
    assert [m["body"] for m in opened(p, second, "ops")["messages"]] == [""]


def test_the_patient_notice_says_the_clinic_wrote_but_not_what(p):
    thread = started(p)
    reply(p, thread, "Your results are in")
    for _ in range(4):
        relay_once(dispatch.dispatch)
    rows = json.loads(p.get("notifications", subject="pat").content)["notifications"]
    notes = [n for n in rows if n["kind"] == "message_received"]
    assert len(notes) == 1 and notes[0]["title"] == "New message"
    assert "results" not in json.dumps(notes) and "dizzy" not in json.dumps(notes)


def test_a_patients_own_message_does_not_notify_the_patient(p):
    started(p)
    for _ in range(4):
        relay_once(dispatch.dispatch)
    rows = json.loads(p.get("notifications", subject="pat").content)["notifications"]
    assert [n for n in rows if n["kind"] == "message_received"] == []


# ------------------------------------------------------------------ who may do what
def test_other_patients_cannot_see_or_answer_a_thread(p):
    thread = started(p)
    p.patient("pat2")
    assert threads(p, "pat2") == []
    assert p.get(f"messages/threads/{thread}", subject="pat2").status_code == 403
    assert reply(p, thread, "hi", subject="pat2").status_code == 403


def test_doctors_cannot_read_or_write_messages_yet(p):
    thread = started(p)
    assert p.get(f"messages/threads/{thread}", subject="doc").status_code == 403
    assert reply(p, thread, "hi", subject="doc").status_code == 403
    assert write(p, "doc").status_code == 403


def test_the_clinic_cannot_start_a_thread_only_answer(p):
    assert write(p, "ops").status_code == 403


def test_another_customers_staff_see_nothing_and_cannot_answer(p):
    thread = started(p)
    outsider = Web()
    outsider.staff("desk")
    assert threads(outsider, "desk") == []
    assert outsider.get(f"messages/threads/{thread}", subject="desk").status_code == 404
    assert reply(outsider, thread, "hi", subject="desk").status_code == 404
    assert outsider.post(f"messages/threads/{thread}/close", subject="desk").status_code == 404


def test_a_patient_cannot_close_a_thread(p):
    thread = started(p)
    assert p.post(f"messages/threads/{thread}/close", subject="pat").status_code == 403


# ------------------------------------------------------------------ input and limits
@pytest.mark.parametrize("fields", [
    {"subject": ""}, {"subject": "x" * 121}, {"body": ""}, {"body": "   "}, {"body": "x" * 2001},
    {"subject": "Bad\x00subject"}, {"body": "bad\x00body"},
])
def test_bad_messages_are_refused_and_nothing_is_stored(p, fields):
    assert write(p, **fields).status_code == 422
    assert threads(p) == []


def test_a_reply_needs_text(p):
    thread = started(p)
    for body in ("", "   ", "x" * 2001):
        assert reply(p, thread, body).status_code == 422


def test_a_person_cannot_flood_the_clinic(p):
    thread = started(p)
    codes = [reply(p, thread, f"message {i}", subject="pat").status_code for i in range(32)]
    assert codes.count(200) == 29 and 429 in codes             # 30 an hour including the first


def other_site_staff(p, login="desk2"):
    """Staff of a SECOND organization (another site) inside the same customer."""
    from modules.trust import api as trust
    with tenant_transaction(p.clinic.ctx):
        site = trust.create_organization(name="Other Site").public_id
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject=login)
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=site,
                          role_code="R04", by="system")
    return login


def test_staff_of_another_site_of_the_same_customer_cannot_read_or_answer(p):
    thread = started(p)
    other = other_site_staff(p)
    assert threads(p, other) == []
    assert p.get(f"messages/threads/{thread}", subject=other).status_code == 404
    assert reply(p, thread, "hi", subject=other).status_code == 404
    assert p.post(f"messages/threads/{thread}/close", subject=other).status_code == 404
    assert [m["body"] for m in opened(p, thread, "ops")["messages"]] == [SECRET]


def test_the_domain_itself_refuses_another_sites_staff(p):
    """Even if a caller skipped the HTTP check, the domain function re-checks the organization."""
    from foundation.errors import ZoikoError
    from modules.messaging import api as messaging
    from modules.trust import api as trust
    thread = started(p)
    other = other_site_staff(p)
    with tenant_transaction(p.clinic.ctx):
        actor = next(a for a, label in trust.actor_labels().items() if label == other)
        org = next(o for o, name in trust.organization_names().items() if name == "Other Site")
    decision = p.clinic.allow(actor, "OPERATIONS", slot_org=org, person=p.person, op="write:comms")
    with tenant_transaction(p.clinic.ctx), pytest.raises(ZoikoError) as err:
        messaging.reply(thread_ref=thread, body="x", actor_public_id=actor, decision=decision,
                        organization_public_id=org)
    assert err.value.code.name == "NOT_FOUND"
