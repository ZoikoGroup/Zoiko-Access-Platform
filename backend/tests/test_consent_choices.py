"""Two more consent choices (DEV-358): appointment reminders, and whether the doctors who treat a
patient read their messages. Both are on until the patient says no; something real obeys each."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.comms import api as comms
from modules.consent import api as consent
from modules.trust import api as trust
from tests.test_clinician import Practice
from tests.test_reminders import booked, reminders_in_inbox, run

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.REMINDER_LEAD_HOURS = 24
    comms.register_subscribers()


def choose(p, purpose, grant, who="pat"):
    return p.post("access/consent", {"purpose": purpose, "grant": grant}, subject=who)


def choices(p, who="pat"):
    r = p.get("access", subject=who)
    assert r.status_code == 200, r.content
    return {c["purpose"]: c for c in json.loads(r.content)["consents"]}


# ------------------------------------------------------------------ how they are shown
def test_the_new_choices_are_on_until_the_patient_chooses_and_the_old_ones_off():
    p = Practice()
    shown = choices(p)
    for purpose in ("doctor_messages", "appointment_reminders"):
        assert (shown[purpose]["shared"], shown[purpose]["default_on"],
                shown[purpose]["chosen"]) == (True, True, False), purpose
    assert shown["insurance_details"]["shared"] is False
    assert shown["guardian_email"]["shared"] is False


def test_saying_no_and_then_yes_is_recorded():
    p = Practice()
    assert choose(p, "appointment_reminders", False).status_code == 200
    assert (choices(p)["appointment_reminders"]["shared"],
            choices(p)["appointment_reminders"]["chosen"]) == (False, True)
    assert choose(p, "appointment_reminders", True).status_code == 200
    assert choices(p)["appointment_reminders"]["shared"] is True


def test_only_the_patient_changes_their_own_choice():
    p = Practice()
    for who in ("ops", "doc"):
        assert choose(p, "doctor_messages", False, who=who).status_code == 403, who
    assert choices(p)["doctor_messages"]["shared"] is True


def test_allowed_for_counts_the_default():
    p = Practice()
    choose(p, "doctor_messages", False)
    with tenant_transaction(p.clinic.ctx):
        assert consent.allowed_for([p.person], "doctor_messages") == set()
        assert consent.allowed_for([p.person], "appointment_reminders") == {p.person}
        assert consent.allowed_for([p.person], "insurance_details") == set()


# ------------------------------------------------------------------ reminders obey it
def test_a_patient_who_said_no_gets_no_reminder_and_one_who_said_nothing_does():
    from tests.test_api import Web

    web = Web()
    web.patient("pat")
    web.staff("desk")
    booked(web)
    assert run(web) == 1
    assert len(reminders_in_inbox(web)) == 1

    quiet = Web()
    quiet.patient("pat")
    quiet.staff("desk")
    assert choose(quiet, "appointment_reminders", False).status_code == 200
    booked(quiet)
    run(quiet)
    assert reminders_in_inbox(quiet) == []
    changes = json.loads(quiet.get("notifications", subject="pat").content)["notifications"]
    assert {n["kind"] for n in changes} >= {"booking_confirmed"}     # changes are still told


# ------------------------------------------------------------------ doctors' messages obey it
@pytest.fixture
def cared():
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=practice.doc, person_public_id=practice.person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=practice.clinic.org, by="system")
    return practice


def thread_of(p):
    r = p.post("messages/threads", {"subject": "A question", "body": "When can I come?"},
               subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["thread"]["id"]


def doctor_list(p):
    r = p.get("clinician/messages", subject="doc")
    assert r.status_code == 200, r.content
    return [t["id"] for t in json.loads(r.content)["threads"]]


def test_by_default_the_treating_doctor_reads_and_answers(cared):
    thread = thread_of(cared)
    assert doctor_list(cared) == [thread]
    assert cared.get(f"messages/threads/{thread}", subject="doc").status_code == 200


def test_after_a_no_the_doctor_sees_nothing_but_the_desk_still_answers(cared):
    thread = thread_of(cared)
    assert choose(cared, "doctor_messages", False).status_code == 200
    assert doctor_list(cared) == []
    assert cared.get(f"messages/threads/{thread}", subject="doc").status_code == 404
    r = cared.post(f"messages/threads/{thread}/reply", {"body": "Hello"}, subject="doc")
    assert r.status_code == 404
    assert cared.get(f"messages/threads/{thread}", subject="ops").status_code == 200
    assert cared.post(f"messages/threads/{thread}/reply", {"body": "Thursday at 10."},
                      subject="ops").status_code == 200


def test_saying_yes_again_gives_the_doctor_the_conversation_back(cared):
    thread = thread_of(cared)
    choose(cared, "doctor_messages", False)
    choose(cared, "doctor_messages", True)
    assert doctor_list(cared) == [thread]
