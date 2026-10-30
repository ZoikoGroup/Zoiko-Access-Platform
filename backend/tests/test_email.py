import datetime as dt
import io
import json
import re

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.test import Client

from foundation import mail as mailer
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import MailState, QueuedEmail
from foundation.timeutil import FixedClock, utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import AuthToken, Credential
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"
NEW_PASSWORD = "Brand-New-Passw0rd"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False
    settings.PATIENT_APP_URL = "http://app.test"


@pytest.fixture
def site():
    world = World()
    with tenant_transaction(world.ctx):
        trust.set_self_registration(True, by="system")
    return world


def post(path, **body):
    return Client().post(f"/api/v1/auth/{path}", json.dumps(body), content_type="application/json")


def sign_up(site, email="new.patient@example.org", password=PASSWORD):
    r = post("register", organization=site.tenant_name, login=email, password=password)
    assert r.status_code == 201
    return r.json()["token"]


def token_from(message):
    return re.search(r"token=([\w.\-]+)", message.body).group(1)


def deliver_mail():
    return mailer.send_due()


def me(token):
    r = Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 200
    return r.json()


def later(minutes):
    return FixedClock(utc_now() + dt.timedelta(minutes=minutes))


# ------------------------------------------------------------------ the mail queue
def test_email_is_queued_inside_a_transaction_and_refused_when_unsafe(site):
    with pytest.raises(mailer.MailError):
        mailer.queue_email(kind="test_mail", to_address="a@b.org", subject="Hi", body="x")
    with tenant_transaction(site.ctx):
        for bad in ({"to_address": "not-an-email"}, {"to_address": "a@b.org\nbcc:x@y.org"},
                    {"to_address": "a@b.org,c@d.org"}, {"subject": "Hi\nBcc: x@y.org"},
                    {"subject": "   "}, {"kind": "Bad Kind"}, {"body": "x" * 5001}):
            args = {"kind": "test_mail", "to_address": "a@b.org", "subject": "Hi", "body": "x",
                    **bad}
            with pytest.raises(ZoikoError) as e:
                mailer.queue_email(**args)
            assert e.value.code == ErrorCode.VALIDATION_FAILED
        row = mailer.queue_email(kind="test_mail", to_address=" A@B.org ", subject="Hi", body="x")
        assert row.to_address == "a@b.org" and row.state == MailState.QUEUED


def test_sending_wipes_the_body_and_masks_the_address(site):
    with tenant_transaction(site.ctx):
        mailer.queue_email(kind="test_mail", to_address="asha@example.org",
                           subject="Your link", body="secret link https://x/?token=SECRET123")
    assert mailer.send_due().sent == 1
    assert len(mail.outbox) == 1 and "SECRET123" in mail.outbox[0].body
    assert mail.outbox[0].to == ["asha@example.org"] and mail.outbox[0].subject == "Your link"
    row = QueuedEmail.objects.get()
    assert row.state == MailState.SENT and row.sent_at is not None
    assert row.body == "" and row.to_address == "a***@example.org"
    assert mailer.send_due().sent == 0 and len(mail.outbox) == 1       # never sent twice


def test_a_failing_mail_server_causes_retries_then_gives_up_with_no_message_kept(
        site, monkeypatch):
    class Boom(ConnectionRefusedError):
        pass

    def send(self, fail_silently=False):
        raise Boom("could not reach mail server for asha@example.org")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", send)
    with tenant_transaction(site.ctx):
        mailer.queue_email(kind="test_mail", to_address="asha@example.org", subject="S", body="b")
    clock = FixedClock(utc_now())
    for attempt in range(1, mailer.MAX_ATTEMPTS):
        result = mailer.send_due(clock=clock)
        assert (result.retrying, result.failed) == (1, 0)
        row = QueuedEmail.objects.get()
        assert row.attempts == attempt and row.state == MailState.QUEUED
        assert row.last_error == "Boom" and "asha" not in row.last_error     # class only
        clock = FixedClock(clock.now() + dt.timedelta(hours=2))
    assert mailer.send_due(clock=clock).failed == 1
    row = QueuedEmail.objects.get()
    assert row.state == MailState.FAILED and mailer.mail_stats() == {"queued": 0, "failed": 1}
    assert mailer.send_due(clock=FixedClock(clock.now() + dt.timedelta(days=1))).failed == 0


def test_a_claim_that_was_never_finished_is_picked_up_again(site):
    with tenant_transaction(site.ctx):
        row = mailer.queue_email(kind="test_mail", to_address="a@b.org", subject="S", body="b")
    QueuedEmail.objects.filter(pk=row.pk).update(
        state=MailState.SENDING, locked_until=utc_now() - dt.timedelta(minutes=1))   # crashed
    assert mailer.send_due().sent == 1
    assert mailer.mail_stats() == {"queued": 0, "failed": 0}


# ------------------------------------------------------------------ verification
def test_signing_up_queues_a_verification_email_and_the_worker_sends_it(site):
    token = sign_up(site)
    assert mail.outbox == []                                  # nothing is sent inside the request
    assert me(token)["email"] == {"email": "n***@example.org", "verified": False}
    assert deliver_mail().sent == 1
    message = mail.outbox[0]
    assert message.to == ["new.patient@example.org"]
    assert message.subject == "Verify your email for Zoiko Access"
    assert "http://app.test/?action=verify&token=" in message.body


def test_the_link_verifies_the_address_once_and_only_once(site):
    token = sign_up(site)
    deliver_mail()
    link_token = token_from(mail.outbox[0])
    assert post("verify-email", token=link_token).status_code == 200
    assert me(token)["email"]["verified"] is True
    again = post("verify-email", token=link_token)               # used up
    assert again.status_code == 422 and again.json()["fields"] == ["token"]


def test_bad_expired_and_foreign_links_all_get_the_same_generic_error(site):
    sign_up(site)
    deliver_mail()
    good = token_from(mail.outbox[0])
    tenant_id, secret = good.split(".")
    other = World()
    for bad in ("", "garbage", f"{tenant_id}.", f".{secret}", f"{tenant_id}.{secret}x",
                f"{other.tenant_public_id}.{secret}", f"nosuchtenant.{secret}", secret):
        r = post("verify-email", token=bad)
        assert r.status_code == 422 and r.json()["fields"] == ["token"], bad
    with tenant_transaction(site.ctx):                       # expired
        AuthToken.objects.update(expires_at=utc_now() - dt.timedelta(seconds=1))
    assert post("verify-email", token=good).status_code == 422


def test_resending_is_rate_limited_and_a_new_link_cancels_the_old_one(site):
    token = sign_up(site)
    deliver_mail()
    old = token_from(mail.outbox[0])
    r = Client().post("/api/v1/auth/resend-verification", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"    # too soon
    actor = me(token)["actor"]
    with tenant_transaction(site.ctx):
        assert trust.send_verification(actor, clock=later(2)) is True
    deliver_mail()
    new = token_from(mail.outbox[1])
    assert new != old
    assert post("verify-email", token=old).status_code == 422             # the old link is dead
    assert post("verify-email", token=new).status_code == 200


def test_at_most_five_verification_emails_a_day(site):
    token = sign_up(site)
    actor = me(token)["actor"]
    for i in range(2, 6):
        with tenant_transaction(site.ctx):
            assert trust.send_verification(actor, clock=later(i * 2)) is True
    with tenant_transaction(site.ctx), pytest.raises(ZoikoError) as e:
        trust.send_verification(actor, clock=later(20))
    assert e.value.code == ErrorCode.RATE_LIMITED


# ------------------------------------------------------------------ password reset
def verified_patient(site, email="reset.me@example.org"):
    token = sign_up(site, email=email)
    deliver_mail()
    post("verify-email", token=token_from(mail.outbox[-1]))
    mail.outbox.clear()
    return token


def forgot(site, login="reset.me@example.org", organization=None):
    return post("forgot-password", organization=organization or site.tenant_name, login=login)


def test_asking_for_a_reset_never_reveals_whether_the_account_exists(site):
    verified_patient(site)
    sign_up(site, email="not.verified@example.org")
    deliver_mail()
    mail.outbox.clear()
    answers = []
    for kwargs in ({}, {"login": "nobody@example.org"}, {"login": "not.verified@example.org"},
                   {"organization": "No Such Place"}):
        r = forgot(site, **kwargs)
        assert r.status_code == 202
        body = r.json()
        answers.append(body)
    assert all(a == {"sent": True} for a in answers)
    deliver_mail()
    assert len(mail.outbox) == 1                              # only the real, verified account
    assert mail.outbox[0].to == ["reset.me@example.org"]
    assert "action=reset&token=" in mail.outbox[0].body


def test_a_reset_changes_the_password_signs_everyone_out_and_works_once(site):
    old_session = verified_patient(site)
    forgot(site)
    deliver_mail()
    link = token_from(mail.outbox[0])
    r = post("reset-password", token=link, password=NEW_PASSWORD)
    assert r.status_code == 200
    assert Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {old_session}"
                        ).status_code == 401                  # signed out everywhere
    assert post("login", organization=site.tenant_name, login="reset.me@example.org",
                password=PASSWORD).status_code == 401         # the old password is dead
    assert post("login", organization=site.tenant_name, login="reset.me@example.org",
                password=NEW_PASSWORD).status_code == 200
    assert post("reset-password", token=link, password="Another-Passw0rd!"
                ).status_code == 422                          # a link works once


def test_a_weak_new_password_is_refused_without_using_up_the_link(site):
    verified_patient(site)
    forgot(site)
    deliver_mail()
    link = token_from(mail.outbox[0])
    for weak in ("short", "reset.me@example.org", "aaaaaaaaaaaa"):
        r = post("reset-password", token=link, password=weak)
        assert r.status_code == 422 and r.json()["fields"] == ["password"]
    assert post("reset-password", token=link, password=NEW_PASSWORD).status_code == 200


def test_reset_links_expire_and_a_verify_link_cannot_reset_a_password(site):
    sign_up(site, email="reset.me@example.org")
    deliver_mail()
    verify_link = token_from(mail.outbox[0])
    post("verify-email", token=verify_link)
    forgot(site)
    deliver_mail()
    reset_link = token_from(mail.outbox[-1])
    assert post("reset-password", token=verify_link, password=NEW_PASSWORD).status_code == 422
    with tenant_transaction(site.ctx):
        AuthToken.objects.filter(purpose="reset_password").update(
            expires_at=utc_now() - dt.timedelta(seconds=1))
    assert post("reset-password", token=reset_link, password=NEW_PASSWORD).status_code == 422


def test_at_most_three_reset_emails_an_hour_and_the_fourth_is_silently_skipped(site):
    verified_patient(site)
    for _ in range(4):
        assert forgot(site).status_code == 202
    deliver_mail()
    assert len(mail.outbox) == 3


# ------------------------------------------------------------------ secrets never stored
def test_tokens_and_passwords_never_sit_in_the_database_or_the_audit_log(site):
    verified_patient(site)
    forgot(site)
    deliver_mail()
    link = token_from(mail.outbox[0])
    secret = link.split(".")[1]
    post("reset-password", token=link, password=NEW_PASSWORD)
    with tenant_transaction(site.ctx):
        hashes = list(AuthToken.objects.values_list("token_hash", flat=True))
        audit = json.dumps(list(AuditEvent.objects.values_list(
            "details", "object_ref", "reason_code", "action")))
    assert secret not in hashes and all(len(h) == 64 for h in hashes)
    assert secret not in audit and NEW_PASSWORD not in audit and PASSWORD not in audit
    assert all(row.body == "" for row in QueuedEmail.objects.filter(state=MailState.SENT))
    assert not QueuedEmail.objects.filter(body__contains=secret).exists()
    with tenant_transaction(site.ctx):
        actions = set(AuditEvent.objects.filter(
            tenant_id=site.ctx.tenant_id).values_list("action", flat=True))
    assert {"auth.verification_sent", "auth.email_verified", "auth.reset_requested",
            "auth.password_reset"} <= actions


def test_changing_the_address_makes_it_unverified_again(site):
    token = sign_up(site)
    actor = me(token)["actor"]
    deliver_mail()
    post("verify-email", token=token_from(mail.outbox[0]))
    assert me(token)["email"]["verified"] is True
    with tenant_transaction(site.ctx):
        cred = Credential.objects.get(actor__public_id=actor)
        trust.set_password(actor, login=cred.login, password=NEW_PASSWORD, by="system",
                           email="other.address@example.org")
        cred.refresh_from_db()
        assert cred.email == "other.address@example.org" and cred.email_verified_at is None


# ------------------------------------------------------------------ the worker and the console
def test_the_worker_command_sends_email_too(site):
    sign_up(site)
    out = io.StringIO()
    call_command("relay_outbox", "--once", stdout=out)
    assert len(mail.outbox) == 1 and "mail sent=1" in out.getvalue()
    assert "token" not in out.getvalue() and "example.org" not in out.getvalue()


def test_registration_still_works_if_the_mail_server_is_down(site, monkeypatch):
    def down(self, fail_silently=False):
        raise ConnectionRefusedError

    monkeypatch.setattr("django.core.mail.EmailMessage.send", down)
    token = sign_up(site)                                    # signing up never touches SMTP
    assert me(token)["email"]["verified"] is False
    assert mailer.send_due().retrying == 1
    assert mailer.mail_stats()["queued"] == 1


def test_a_transaction_that_rolls_back_leaves_no_email(site):
    with pytest.raises(RuntimeError), tenant_transaction(site.ctx), transaction.atomic():
        mailer.queue_email(kind="test_mail", to_address="a@b.org", subject="S", body="b")
        raise RuntimeError("the change failed")
    assert QueuedEmail.objects.count() == 0
