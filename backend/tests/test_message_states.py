"""Message states (GHE-16): who opened a message is its own record, separate from delivery and from
clinical review; unread is per person; an email keeps a fingerprint of what was sent. DEV-113."""
import hashlib
import json

import pytest
from django.db import DatabaseError, transaction

from foundation import mail
from foundation.cell_context import tenant_transaction
from foundation.models import MailState, QueuedEmail
from modules.messaging.models import MessageView
from modules.trust import api as trust
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.staff("ops2")                     # a second receptionist
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "message")
    return practice


def start(p, body="Hello, can I move my visit?"):
    r = p.post("messages/threads", {"subject": "A question", "body": body}, subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["thread"]["id"]


def thread(p, thread_id, subject):
    r = p.get(f"messages/threads/{thread_id}", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["thread"]


def threads(p, subject):
    r = p.get("messages/threads", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["threads"]


def reply(p, thread_id, subject, body="Yes, of course."):
    r = p.post(f"messages/threads/{thread_id}/reply", {"body": body}, subject=subject)
    assert r.status_code == 200, r.content


# ------------------------------------------------------------------ who opened what
def test_a_message_is_not_seen_until_the_other_side_opens_it(p):
    t = start(p)
    assert thread(p, t, "pat")["messages"][0]["seen_at"] is None       # sent, not opened
    thread(p, t, "ops")                                                 # the clinic opens it
    assert thread(p, t, "pat")["messages"][0]["seen_at"]                # now the patient is told


def test_a_staff_reply_is_seen_only_when_the_patient_opens_it(p):
    t = start(p)
    reply(p, t, "ops")
    mine = thread(p, t, "ops")["messages"]
    assert mine[-1]["side"] == "staff" and mine[-1]["seen_at"] is None
    thread(p, t, "pat")
    assert thread(p, t, "ops")["messages"][-1]["seen_at"]


def test_opening_your_own_message_does_not_count_as_seen(p):
    t = start(p)
    thread(p, t, "pat")
    thread(p, t, "pat")
    assert thread(p, t, "pat")["messages"][0]["seen_at"] is None
    with tenant_transaction(p.clinic.ctx):
        assert MessageView.objects.count() == 0


def test_opening_twice_records_one_view_per_person_and_message(p):
    t = start(p)
    thread(p, t, "ops")
    thread(p, t, "ops")
    with tenant_transaction(p.clinic.ctx):
        assert MessageView.objects.count() == 1


def test_the_reader_never_learns_which_person_opened_it(p):
    t = start(p)
    thread(p, t, "ops")
    raw = p.get(f"messages/threads/{t}", subject="pat").content.decode()
    with tenant_transaction(p.clinic.ctx):
        who = MessageView.objects.get().viewer_ref
    assert who not in raw


def test_the_record_of_openings_cannot_be_changed_or_removed(p):
    t = start(p)
    thread(p, t, "ops")
    with tenant_transaction(p.clinic.ctx):
        view = MessageView.objects.get()
        with pytest.raises(DatabaseError), transaction.atomic():
            MessageView.objects.filter(pk=view.pk).update(viewer_ref="someone-else")
        with pytest.raises(DatabaseError), transaction.atomic():
            view.delete()


# ------------------------------------------------------------------ unread is per person
def test_one_staff_members_reply_does_not_clear_unread_for_another(p):
    t = start(p)
    assert [x["unread_for_me"] for x in threads(p, "ops")] == [1]
    assert [x["unread_for_me"] for x in threads(p, "ops2")] == [1]
    thread(p, t, "ops")                                   # ops opens it
    reply(p, t, "ops")                                    # and answers
    assert [x["unread_for_me"] for x in threads(p, "ops")] == [0]
    assert [x["unread_for_me"] for x in threads(p, "ops2")] == [1]   # ops2 never opened it
    thread(p, t, "ops2")
    assert [x["unread_for_me"] for x in threads(p, "ops2")] == [0]


def test_the_clinics_shared_waiting_count_still_clears_when_it_answers(p):
    t = start(p)
    reply(p, t, "ops")
    assert [x["unread"] for x in threads(p, "ops2")] == [0]          # the clinic has answered
    assert [x["unread_for_me"] for x in threads(p, "ops2")] == [1]   # but ops2 has not opened it


def test_a_patient_sees_what_they_have_not_opened_of_the_clinics_replies(p):
    t = start(p)
    reply(p, t, "ops")
    assert [x["unread_for_me"] for x in threads(p, "pat")] == [1]
    thread(p, t, "pat")
    assert [x["unread_for_me"] for x in threads(p, "pat")] == [0]


# ------------------------------------------------------------------ what an email proves
def test_a_sent_email_keeps_a_fingerprint_of_what_was_sent_but_not_the_text(p):
    with tenant_transaction(p.clinic.ctx):
        row = mail.queue_email(kind="verify_email", to_address="ann@example.org",
                               subject="Verify your email", body="Your one-time link: abc123")
    expected = hashlib.sha256(b"Verify your email\nYour one-time link: abc123").hexdigest()
    assert row.content_digest == expected
    assert mail.send_due().sent == 1
    kept = QueuedEmail.objects.get(pk=row.pk)
    assert kept.state == MailState.SENT and kept.body == ""            # the link is gone
    assert kept.content_digest == expected and kept.to_address == "a***@example.org"
