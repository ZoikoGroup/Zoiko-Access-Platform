import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.models import QueuedEmail
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.comms import api as comms
from modules.evidence.models import AuditEvent
from modules.trust.models import Actor, Credential
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import reply, started

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

ADDRESS = "ravi@example.org"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.PATIENT_APP_URL = "http://patient.test"
    comms.register_subscribers()


@pytest.fixture
def p():
    return Practice()


def give_email(p, actor_ref=None, address=ADDRESS, verified=True):
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.create(
            actor=Actor.objects.get(public_id=actor_ref or p.patient_actor), login=address,
            password_hash="x", email=address, email_verified_at=utc_now() if verified else None)


def switch(p, enabled, subject="pat"):
    return p.post("my/email-notices", {"enabled": enabled}, subject=subject)


def settle():
    for _ in range(4):
        relay_once(dispatch.dispatch)


def updates():
    return list(QueuedEmail.objects.filter(kind="patient_update"))


def confirmed_booking(p):
    booking = booked(p, confirm=False)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    settle()
    return booking


def test_emails_are_off_until_the_patient_turns_them_on(p):
    give_email(p)
    confirmed_booking(p)
    assert updates() == []


def test_when_on_a_confirmed_appointment_sends_one_short_email_with_no_care_details(p):
    give_email(p)
    assert switch(p, True).status_code == 200
    confirmed_booking(p)
    (mail,) = updates()
    assert mail.to_address == ADDRESS and mail.subject == "An update on Zoiko Access"
    assert mail.body.count("An appointment was confirmed.") == 1
    assert "http://patient.test/?page=notifications" in mail.body
    for secret in ("Cardiology", "North", "doc", "2027", "09:00", p.person):
        assert secret not in mail.body, secret


def test_an_unconfirmed_address_is_never_emailed(p):
    give_email(p, verified=False)
    switch(p, True)
    confirmed_booking(p)
    assert updates() == []


def test_a_patient_with_no_address_gets_nothing(p):
    switch(p, True)
    confirmed_booking(p)
    assert updates() == []


def test_a_request_is_not_emailed_but_a_clinic_cancellation_is(p):
    give_email(p)
    switch(p, True)
    booking = booked(p, confirm=False)
    settle()
    assert updates() == []                                    # "requested" is the patient's own act
    p.post(f"bookings/{booking}/cancel", {"reason": "doctor_unavailable"}, subject="ops")
    settle()
    (mail,) = updates()
    assert "An appointment was cancelled." in mail.body
    assert "doctor is unavailable" not in mail.body           # the reason stays behind the sign-in


def test_a_message_from_the_clinic_is_emailed_but_the_patients_own_is_not(p):
    give_email(p)
    switch(p, True)
    thread = started(p)
    settle()
    assert updates() == []
    reply(p, thread, "Your results are in")
    settle()
    (mail,) = updates()
    assert "You have a new message from your clinic." in mail.body
    assert "results" not in mail.body


def test_turning_it_off_stops_the_emails(p):
    give_email(p)
    switch(p, True)
    switch(p, False)
    confirmed_booking(p)
    assert updates() == []


def test_another_patients_choice_never_sends_them_this_patients_news(p):
    give_email(p)
    switch(p, True)
    second, _ = p.patient("second")
    give_email(p, actor_ref=second, address="second@example.org")
    switch(p, True, subject="second")
    confirmed_booking(p)
    assert [m.to_address for m in updates()] == [ADDRESS]


def test_the_settings_say_whether_an_email_could_be_sent(p):
    r = json.loads(p.get("my/email-notices", subject="pat").content)
    assert r == {"enabled": False, "can_email": False}
    give_email(p, verified=False)
    assert json.loads(p.get("my/email-notices", subject="pat").content)["can_email"] is False
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.update(email_verified_at=utc_now())
    assert json.loads(p.get("my/email-notices", subject="pat").content)["can_email"] is True
    assert json.loads(switch(p, True).content) == {"enabled": True, "can_email": True}


@pytest.mark.parametrize("bad", ["yes", 1, None, [True]])
def test_the_switch_must_be_a_real_true_or_false(p, bad):
    assert switch(p, bad).status_code == 422


def test_staff_and_doctors_have_no_such_switch(p):
    for who in ("ops", "doc"):
        assert p.get("my/email-notices", subject=who).status_code == 403
        assert switch(p, True, subject=who).status_code == 403


def test_another_customers_choice_is_separate(p):
    give_email(p)
    other = Practice()
    switch(other, True)
    confirmed_booking(p)
    assert updates() == []                                    # this patient never turned it on


def test_turning_it_on_or_off_is_audited(p):
    switch(p, True)
    switch(p, False)
    with tenant_transaction(p.clinic.ctx):
        actions = sorted(AuditEvent.objects.filter(
            action__startswith="comms.email_notices").values_list("action", flat=True))
    assert actions == ["comms.email_notices_off", "comms.email_notices_on"]
