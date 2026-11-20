"""Sending a message twice (a double tap, a retry) posts it once when an Idempotency-Key is given
(DEV-148); without a key nothing changes for older clients."""
import json

import pytest

from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
KEY = "message-key-000000001"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def start(p, key=KEY, subject="pat", **over):
    body = {"subject": "A question", "body": "When can I come in?", **over}
    return p.post("messages/threads", body, subject=subject, key=key)


def threads(p, subject="ops"):
    return json.loads(p.get("messages/threads", subject=subject).content)["threads"]


def thread_messages(p, thread, subject="pat"):
    return json.loads(p.get(f"messages/threads/{thread}", subject=subject).content
                      )["thread"]["messages"]


# ------------------------------------------------------------------ starting a conversation
def test_the_same_message_sent_twice_with_one_key_makes_one_thread_and_the_same_answer(p):
    first, again = start(p), start(p)
    assert first.status_code == 201 and again.status_code == 200
    a, b = json.loads(first.content)["thread"], json.loads(again.content)["thread"]
    assert a["id"] == b["id"]
    assert len(threads(p)) == 1


def test_the_same_key_with_different_words_is_refused_and_nothing_new_is_made(p):
    start(p)
    assert start(p, body="Something quite different").status_code == 409
    assert len(threads(p)) == 1


def test_without_a_key_two_sends_make_two_threads_as_before(p):
    assert start(p, key=None).status_code == 201
    assert start(p, key=None).status_code == 201
    assert len(threads(p)) == 2


@pytest.mark.parametrize("bad", ["short", "has spaces in the key!!", "x" * 200])
def test_a_malformed_key_is_refused_and_nothing_is_made(p, bad):
    assert start(p, key=bad).status_code == 422
    assert threads(p) == []


def test_two_patients_can_use_the_same_key_without_touching_each_other(p):
    p.patient("other")
    assert start(p).status_code == 201
    assert start(p, subject="other").status_code == 201
    assert len(threads(p)) == 2


# ------------------------------------------------------------------ replying
def test_a_reply_sent_twice_with_one_key_posts_once(p):
    thread = json.loads(start(p).content)["thread"]["id"]
    first = p.post(f"messages/threads/{thread}/reply", {"body": "Thank you"}, subject="pat",
                   key="reply-key-0000000001")
    again = p.post(f"messages/threads/{thread}/reply", {"body": "Thank you"}, subject="pat",
                   key="reply-key-0000000001")
    assert first.status_code == again.status_code == 200
    assert [m["body"] for m in thread_messages(p, thread)].count("Thank you") == 1


def test_a_reply_with_the_same_key_and_different_words_is_refused(p):
    thread = json.loads(start(p).content)["thread"]["id"]
    p.post(f"messages/threads/{thread}/reply", {"body": "One thing"}, subject="pat",
           key="reply-key-0000000002")
    r = p.post(f"messages/threads/{thread}/reply", {"body": "Another thing"}, subject="pat",
               key="reply-key-0000000002")
    assert r.status_code == 409
    assert len(thread_messages(p, thread)) == 2                  # the question and the one reply


def test_the_clinic_replying_twice_with_one_key_also_posts_once(p):
    thread = json.loads(start(p).content)["thread"]["id"]
    for _ in range(2):
        r = p.post(f"messages/threads/{thread}/reply", {"body": "Tuesday at ten"}, subject="ops",
                   key="clinic-key-000000001")
        assert r.status_code == 200
    assert [m["body"] for m in thread_messages(p, thread)].count("Tuesday at ten") == 1


def test_a_reply_without_a_key_still_works_and_a_reused_key_on_another_thread_is_separate(p):
    one = json.loads(start(p).content)["thread"]["id"]
    two = json.loads(start(p, key="message-key-000000002", subject="pat").content)["thread"]["id"]
    assert p.post(f"messages/threads/{one}/reply", {"body": "No key"}, subject="pat"
                  ).status_code == 200
    for thread in (one, two):
        assert p.post(f"messages/threads/{thread}/reply", {"body": "Same key"}, subject="pat",
                      key="shared-key-00000001").status_code == 200
    assert [m["body"] for m in thread_messages(p, one)].count("Same key") == 1
    assert [m["body"] for m in thread_messages(p, two)].count("Same key") == 1
