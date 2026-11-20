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
    # DEV-194/DEV-198 added the text-message fields alongside these.
    assert {k: r[k] for k in ("enabled", "can_email", "quiet")} == {
        "enabled": False, "can_email": False, "quiet": None}
    give_email(p, verified=False)
    assert json.loads(p.get("my/email-notices", subject="pat").content)["can_email"] is False
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.update(email_verified_at=utc_now())
    assert json.loads(p.get("my/email-notices", subject="pat").content)["can_email"] is True
    done = json.loads(switch(p, True).content)
    assert {k: done[k] for k in ("enabled", "can_email", "quiet")} == {
        "enabled": True, "can_email": True, "quiet": None}


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


# ------------------------------------------------------------------ TC30: quiet hours
def quiet(p, start="22:00", end="07:00", zone="Europe/London", subject="pat"):
    return p.post("my/email-notices", {"quiet": {"start": start, "end": end, "zone": zone}},
                  subject=subject)


def at(hour, minute=0, day=1, month=7):
    import datetime as dt
    return dt.datetime(2027, month, day, hour, minute, tzinfo=dt.timezone.utc)


def test_a_patient_sets_reads_and_clears_quiet_hours(p):
    give_email(p)
    assert json.loads(p.get("my/email-notices", subject="pat").content)["quiet"] is None
    r = quiet(p)
    assert r.status_code == 200
    assert json.loads(r.content)["quiet"] == {"start": "22:00", "end": "07:00",
                                              "zone": "Europe/London"}
    assert p.post("my/email-notices", {"quiet": None}, subject="pat").status_code == 200
    assert json.loads(p.get("my/email-notices", subject="pat").content)["quiet"] is None
    with tenant_transaction(p.clinic.ctx):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"comms.quiet_hours_set", "comms.quiet_hours_cleared"} <= actions


@pytest.mark.parametrize("window", [
    {"start": "25:00", "end": "07:00", "zone": "UTC"},
    {"start": "22:00", "end": "7am", "zone": "UTC"},
    {"start": "22:00", "end": "22:00", "zone": "UTC"},
    {"start": "22:00", "end": "07:00", "zone": "Mars/Base"},
    {"start": "22:00", "end": "07:00"}, "overnight", 5])
def test_a_bad_quiet_window_is_refused(p, window):
    give_email(p)
    r = p.post("my/email-notices", {"quiet": window}, subject="pat")
    assert r.status_code == 422


def test_an_empty_post_is_still_refused_and_only_patients_have_quiet_hours(p):
    assert p.post("my/email-notices", {}, subject="pat").status_code == 422
    assert quiet(p, subject="ops").status_code == 403


def test_a_routine_email_waits_for_the_end_of_the_window_and_a_clock_change_cannot_move_it(p):
    give_email(p)
    quiet(p, "22:00", "07:00", "Europe/London")
    actor = p.patient_actor
    from modules.comms.services import next_allowed_send as send
    with tenant_transaction(p.clinic.ctx):
        assert send(actor, at(12)) == at(12)                       # daytime: now
        assert send(actor, at(23)) == at(6, day=2)                 # 23:00 BST -> 07:00 BST next day
        assert send(actor, at(3)) == at(6)                         # small hours -> 07:00 BST
        # the night the clocks go back (31 Oct 2027): 07:00 is GMT, so 07:00 UTC
        assert send(actor, at(23, day=30, month=10)) == at(7, day=31, month=10)
        # the night they go forward (28 Mar 2027): 07:00 is BST, so 06:00 UTC
        assert send(actor, at(23, day=27, month=3)) == at(6, day=28, month=3)


def test_only_routine_reminders_wait_while_urgent_notices_go_at_once(p):
    from modules.comms.subscribers import _email_the_patient
    give_email(p)
    switch(p, True)
    import datetime as dt
    now = utc_now()

    def hour(delta):
        return (now + dt.timedelta(hours=delta)).strftime("%H:%M")

    assert quiet(p, hour(-1), hour(1), "UTC").status_code == 200   # right now is inside the window
    with tenant_transaction(p.clinic.ctx):
        _email_the_patient(p.person, "booking_reminder")
        _email_the_patient(p.person, "booking_cancelled")
    mails = {m.body: m for m in updates()}
    reminder = next(m for b, m in mails.items() if "coming up" in b)
    cancelled = next(m for b, m in mails.items() if "was cancelled" in b)
    assert reminder.next_attempt_at > now + dt.timedelta(minutes=30)
    assert cancelled.next_attempt_at <= utc_now()                   # not held back


# ------------------------------------------------- TC37: turning emails off cancels queued ones
def test_turning_emails_off_cancels_what_is_already_waiting_to_be_sent(p):
    give_email(p)
    switch(p, True)
    confirmed_booking(p)
    (waiting,) = updates()
    assert waiting.state == "queued" and ADDRESS in waiting.to_address
    assert switch(p, False).status_code == 200
    waiting.refresh_from_db()
    assert waiting.state == "cancelled" and waiting.body == ""
    assert ADDRESS not in waiting.to_address                       # masked, like a sent one
    with tenant_transaction(p.clinic.ctx):
        assert "comms.queued_emails_cancelled" in set(
            AuditEvent.objects.values_list("action", flat=True))


def test_cancelling_leaves_other_mail_and_other_people_alone(p):
    from foundation.mail import queue_email
    give_email(p)
    switch(p, True)
    confirmed_booking(p)
    with tenant_transaction(p.clinic.ctx):
        queue_email(kind="verify_email", to_address=ADDRESS, subject="Confirm", body="a link")
        queue_email(kind="patient_update", to_address="someone.else@example.org",
                    subject="An update on Zoiko Access", body="other person")
    switch(p, False)
    states = {(m.kind, m.state) for m in QueuedEmail.objects.all()}
    assert ("verify_email", "queued") in states                      # a different kind: kept
    others = QueuedEmail.objects.filter(subject="An update on Zoiko Access",
                                        to_address="someone.else@example.org")
    assert others.count() == 1 and others.first().state == "queued"  # someone else: kept


def test_turning_emails_off_with_nothing_waiting_is_harmless(p):
    give_email(p)
    switch(p, True)
    assert switch(p, False).status_code == 200
    assert updates() == []
