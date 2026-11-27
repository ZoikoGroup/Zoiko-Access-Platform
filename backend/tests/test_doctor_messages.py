"""A doctor reads and answers messages from patients they care for, and only those (DEV-162)."""
import json

import pytest

from foundation.cell_context import tenant_transaction
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
    with tenant_transaction(practice.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=practice.doc, person_public_id=practice.person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=practice.clinic.org, by="system")
    return practice


def body(r):
    return json.loads(r.content)


def write(p, subject="A question", text="When can I come in?"):
    r = p.post("messages/threads", {"subject": subject, "body": text}, subject="pat")
    assert r.status_code == 201, r.content
    return body(r)["thread"]["id"]


def inbox(p, who="doc"):
    r = p.get("clinician/messages", subject=who)
    assert r.status_code == 200, r.content
    return body(r)["threads"]


# ------------------------------------------------------------------ who sees which conversations
def test_a_doctor_sees_conversations_of_their_own_patients_only(p):
    mine = write(p)
    assert [t["id"] for t in inbox(p)] == [mine]
    assert inbox(p, "doc2") == []                       # a colleague with no relationship


def test_a_doctor_sees_nothing_without_a_relationship_even_in_the_same_clinic(p):
    write(p)
    assert inbox(p, "doc2") == []
    for who in ("pat", "ops"):
        assert p.get("clinician/messages", subject=who).status_code == 403


def test_a_doctor_cannot_open_or_reply_to_a_patients_thread_they_do_not_care_for(p):
    thread = write(p)
    assert p.get(f"messages/threads/{thread}", subject="doc2").status_code == 403
    assert p.post(f"messages/threads/{thread}/reply", {"body": "Hello"}, subject="doc2"
                  ).status_code == 403


# ------------------------------------------------------------------ reading and answering
def test_a_doctor_opens_a_thread_and_the_patient_sees_the_reply_as_the_clinics(p):
    thread = write(p)
    opened = body(p.get(f"messages/threads/{thread}", subject="doc"))["thread"]
    assert [m["body"] for m in opened["messages"]] == ["When can I come in?"]
    r = p.post(f"messages/threads/{thread}/reply", {"body": "Tuesday at ten"}, subject="doc")
    assert r.status_code == 200
    seen = body(p.get(f"messages/threads/{thread}", subject="pat"))["thread"]
    assert [m["body"] for m in seen["messages"]] == ["When can I come in?", "Tuesday at ten"]
    assert seen["messages"][1]["side"] == "staff"        # it reads as the clinic answering


def test_a_doctors_reply_is_on_the_clinic_side_and_clears_the_clinics_shared_unread(p):
    thread = write(p)
    assert body(p.get("messages/threads", subject="ops"))["threads"][0]["unread"] == 1
    p.post(f"messages/threads/{thread}/reply", {"body": "Tuesday at ten"}, subject="doc")
    assert body(p.get("messages/threads", subject="ops"))["threads"][0]["unread"] == 0
    assert inbox(p)[0]["unread_for_me"] == 1            # their own badge clears when they open it


def test_a_new_message_shows_as_unread_for_the_doctor_until_they_open_it(p):
    thread = write(p)
    assert inbox(p)[0]["unread_for_me"] == 1
    p.get(f"messages/threads/{thread}", subject="doc")
    assert inbox(p)[0]["unread_for_me"] == 0


def test_the_same_reply_sent_twice_with_one_key_posts_once(p):
    thread = write(p)
    for _ in range(2):
        r = p.post(f"messages/threads/{thread}/reply", {"body": "Tuesday at ten"},
                   subject="doc", key="doctor-key-00000001")
        assert r.status_code == 200
    seen = body(p.get(f"messages/threads/{thread}", subject="pat"))["thread"]["messages"]
    assert [m["body"] for m in seen].count("Tuesday at ten") == 1


def test_following_up_read_only_cannot_reply(p):
    thread = write(p)
    r = p.post(f"messages/threads/{thread}/reply", {"body": "Hello"}, subject="doc",
               HTTP_X_ZOIKO_PURPOSE="CONTINUITY_OF_CARE")
    assert r.status_code == 403
    read = p.get(f"messages/threads/{thread}", subject="doc",
                 HTTP_X_ZOIKO_PURPOSE="CONTINUITY_OF_CARE")
    assert read.status_code == 200                                    # reading is allowed


def test_only_clinic_staff_close_a_conversation(p):
    thread = write(p)
    assert p.post(f"messages/threads/{thread}/close", {}, subject="doc").status_code == 403
    assert p.post(f"messages/threads/{thread}/close", {}, subject="ops").status_code == 200
    assert p.post(f"messages/threads/{thread}/reply", {"body": "Hello"}, subject="doc"
                  ).status_code == 409


# ------------------------------------------------------------------ other customers
def test_another_customers_doctor_sees_nothing_of_this_clinic(p):
    write(p)
    other = Practice()
    assert inbox(other, "doc") == []
