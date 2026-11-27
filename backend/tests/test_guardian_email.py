"""Emailing a parent or guardian when a young patient's appointments change: only if the PATIENT
agreed, only a guardian who may see appointments, has a confirmed address and asked for emails, and
never a word about the care (modules/comms, modules/consent, trust.guardian_email_targets)."""
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.models import QueuedEmail
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.comms import api as comms
from modules.trust.models import Actor, Credential
from tests.test_api import book
from tests.test_clinician import Practice
from tests.test_family_access import act, child, mine, years_ago

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

GUARDIAN_ADDRESS = "parent@example.org"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.PATIENT_APP_URL = "http://patient.test"
    settings.AGE_OF_MAJORITY = ""
    settings.AGE_OF_MAJORITY_DEFAULT = 18
    comms.register_subscribers()


@pytest.fixture
def p():
    return Practice()


def give_email(p, actor_ref, address=GUARDIAN_ADDRESS, verified=True):
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.create(
            actor=Actor.objects.get(public_id=actor_ref), login=address, password_hash="x",
            email=address, email_verified_at=utc_now() if verified else None)


def settle():
    for _ in range(4):
        relay_once(dispatch.dispatch)


def guardian_mail():
    return list(QueuedEmail.objects.filter(kind="guardian_update"))


def setup(p, *, relationship="parent", verified=True, notices=True, agreed=True, scopes=None):
    """A 15-year-old with their own login whose parent (`pat`) has an approved grant."""
    kid_actor, kid, grant = child(
        p, years_ago(15), relationship=relationship, scopes=scopes)
    give_email(p, p.patient_actor, verified=verified)
    if notices:
        assert p.post("my/email-notices", {"enabled": True}, subject="pat").status_code == 200
    if agreed:
        say(p, True)
    return kid, grant


def say(p, grant, subject="kid"):
    r = p.post("access/consent", {"purpose": "guardian_email", "grant": grant}, subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["consent"]


def confirm_a_visit_for_the_child(p):
    if not [s for s in p.open_slots() if s["provider"]]:
        p.add_times(p.doc, count=3)
    made = book(p, p.slot_of("doc"), subject="kid")
    assert made.status_code == 201, made.content
    booking = json.loads(made.content)["booking"]["id"]
    assert p.post(f"bookings/{booking}/confirm", subject="ops").status_code == 200
    settle()
    return booking


# ------------------------------------------------------------------ the patient's own choice
def test_nobody_is_emailed_until_the_patient_agrees(p):
    setup(p, agreed=False)
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []


def test_with_the_patients_yes_the_guardian_gets_one_email_with_no_care_details(p):
    setup(p)
    confirm_a_visit_for_the_child(p)
    (mail,) = guardian_mail()
    assert mail.to_address == GUARDIAN_ADDRESS and mail.subject == "An update on Zoiko Access"
    assert "There is an update about an appointment for someone you act for." in mail.body
    assert "http://patient.test/?page=notifications" in mail.body
    for detail in ("Cardiology", "General", "Ravi", "confirmed", "2027", "North"):
        assert detail.lower() not in mail.body.lower(), detail


def test_the_choice_appears_on_the_patients_own_page_with_its_own_wording(p):
    setup(p, agreed=False)
    shown = {c["purpose"]: c for c in mine(p)["consents"]}
    assert set(shown) == {"insurance_details", "guardian_email", "doctor_messages",
                          "appointment_reminders",               # DEV-358 added two
                          "cross_matching"}                      # DEV-363
    mail = shown["guardian_email"]
    assert mail["shared"] is False and mail["state"] == "withdrawn"
    assert (mail["yes_label"], mail["no_label"]) == ("Allow emails", "Stop the emails")
    assert say(p, True)["shared"] is True
    assert {c["purpose"]: c["shared"] for c in mine(p)["consents"]}["guardian_email"] is True


def test_withdrawing_the_yes_stops_the_emails(p):
    setup(p)
    booking = confirm_a_visit_for_the_child(p)
    assert len(guardian_mail()) == 1
    say(p, False)
    assert p.post(f"bookings/{booking}/cancel", subject="kid").status_code == 200
    settle()
    assert len(guardian_mail()) == 1                      # the cancellation was not mailed


def test_a_guardian_cannot_agree_to_the_emails_for_the_patient(p):
    setup(p, agreed=False)
    r = p.post("access/consent", {"purpose": "guardian_email", "grant": True}, subject="pat")
    assert r.status_code == 200                         # it is the guardian's OWN record that moved
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []                        # the child's choice is still "no"


def test_a_choice_nobody_listed_is_refused(p):
    setup(p, agreed=False)
    r = p.post("access/consent", {"purpose": "everything", "grant": True}, subject="kid")
    assert r.status_code == 422


# ------------------------------------------------------------------ who may be emailed
def test_a_guardian_without_a_confirmed_address_is_not_emailed(p):
    setup(p, verified=False)
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []


def test_a_guardian_who_has_not_turned_emails_on_is_not_emailed(p):
    setup(p, notices=False)
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []


def test_a_guardian_whose_access_was_narrowed_to_leave_out_appointments_is_not_emailed(p):
    _, grant = setup(p)
    assert act(p, grant, "narrow", "kid", scopes=["messages"]).status_code == 200
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []


def test_a_guardian_whose_access_was_ended_is_not_emailed(p):
    _, grant = setup(p)
    assert act(p, grant, "end", "kid").status_code == 200
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []


def test_only_a_parent_or_guardian_is_emailed_not_any_other_carer(p):
    setup(p, relationship="caregiver")
    confirm_a_visit_for_the_child(p)
    assert guardian_mail() == []


def test_the_patients_own_emails_are_not_mixed_up_with_the_guardians(p):
    setup(p)
    confirm_a_visit_for_the_child(p)
    wrong = QueuedEmail.objects.filter(kind="patient_update", to_address=GUARDIAN_ADDRESS)
    assert wrong.count() == 0


# ------------------------------------------------------------------ the register keeps the history
def test_each_yes_and_no_is_kept_in_the_registers_own_history(p):
    setup(p, agreed=False)
    say(p, True)
    say(p, False)
    r = p.get("access", subject="kid")
    assert r.status_code == 200
    from modules.consent import api as consent

    with tenant_transaction(p.clinic.ctx):
        person = json.loads(r.content)["person"]
        done = consent.history(person, "guardian_email")
    assert [e["kind"] for e in done["events"]] == ["granted", "withdrawn"] and done["chain_ok"]
