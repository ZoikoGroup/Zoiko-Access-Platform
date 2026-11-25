"""Where queued email goes: the provider-neutral sender and the delivery receipts (GHE-16)."""
import datetime as dt
import http.server
import json
import threading
from types import SimpleNamespace

import pytest
from django.core import mail
from django.test import Client

from foundation import mail as mailer
from foundation import mail_providers as providers
from foundation.cell_context import tenant_transaction
from foundation.mail_providers import (
    MailMessage,
    MailProvider,
    ProviderResult,
    receipt_signature,
    record_receipt,
    register_provider,
)
from foundation.models import MailState, QueuedEmail
from foundation.release_check import release_problems
from foundation.timeutil import FixedClock, utc_now
from tests.test_policy import World
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

SECRET = "receipt-signing-secret-for-tests"


@pytest.fixture(autouse=True)
def settings_for_tests(settings):
    settings.MAIL_PROVIDER = "smtp"
    settings.MAIL_WEBHOOK_SECRET = SECRET
    settings.MAIL_API_URL = ""
    settings.MAIL_API_KEY = ""
    settings.MAIL_API_ALLOW_INSECURE = False


@pytest.fixture
def site():
    return World()


def queue(site, address="asha@example.org", subject="Your link", body="hello"):
    with tenant_transaction(site.ctx):
        return mailer.queue_email(kind="test_mail", to_address=address, subject=subject, body=body)


def row():
    return QueuedEmail.objects.get()


# ------------------------------------------------------------------ the three providers
def test_smtp_is_the_default_and_the_row_records_which_provider_took_it(site):
    queue(site)
    assert mailer.send_due().sent == 1
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["asha@example.org"]
    done = row()
    assert (done.provider, done.delivery_status) == ("smtp", "accepted")
    assert done.provider_ref == "" and done.delivery_updated_at is not None


def test_the_console_provider_sends_nothing_anywhere_real(site, settings):
    settings.MAIL_PROVIDER = "console"
    providers.ConsoleProvider.sent.clear()
    queue(site)
    assert mailer.send_due().sent == 1
    assert mail.outbox == []
    done = row()
    assert done.provider == "console" and done.provider_ref == f"console-{done.public_id}"
    assert providers.ConsoleProvider.sent == [done.public_id]
    assert done.body == "" and done.to_address == "a***@example.org"      # wiped as always


def test_an_unknown_provider_is_a_retry_not_a_lost_message(site, settings):
    settings.MAIL_PROVIDER = "nosuchvendor"
    queue(site)
    result = mailer.send_due()
    assert (result.sent, result.retrying) == (0, 1)
    waiting = row()
    assert waiting.state == MailState.QUEUED and waiting.last_error == "MailProviderError"
    settings.MAIL_PROVIDER = "smtp"                                        # fixed: it goes out
    assert mailer.send_due(clock=FixedClock(utc_now() + dt.timedelta(hours=1))).sent == 1


def test_a_vendors_own_adapter_can_be_added_and_its_id_is_kept(site, settings):
    class Pretend(MailProvider):
        name = "pretend"
        got: list[MailMessage] = []

        def send(self, message):
            self.got.append(message)
            return ProviderResult(provider_ref="vendor-123")

    register_provider("pretend", Pretend)
    settings.MAIL_PROVIDER = "pretend"
    queue(site, subject="Hi there")
    assert mailer.send_due().sent == 1
    (seen,) = Pretend.got
    assert (seen.to, seen.subject, seen.body) == ("asha@example.org", "Hi there", "hello")
    assert seen.reference == row().public_id and seen.from_email
    assert (row().provider, row().provider_ref) == ("pretend", "vendor-123")


# ------------------------------------------------------------------ the web API provider
class Vendor:
    """A pretend mail vendor on a local port, answering as scripted."""

    def __init__(self, status=200, body=b'{"id": "vend-77"}', location=""):
        self.calls = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                outer.calls.append((self.path, dict(self.headers), self.rfile.read(length)))
                self.send_response(outer.status)
                if outer.location:
                    self.send_header("Location", outer.location)
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args):
                pass

        self.status, self.body, self.location = status, body, location
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/send"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def vendor(settings):
    made = []

    def start(**kwargs):
        v = Vendor(**kwargs)
        made.append(v)
        settings.MAIL_PROVIDER = "http_api"
        settings.MAIL_API_URL = v.url
        settings.MAIL_API_KEY = "vendor-key-123"
        settings.MAIL_API_ALLOW_INSECURE = True
        return v

    yield start
    for v in made:
        v.close()


def test_the_web_api_provider_posts_json_with_the_key_and_keeps_the_vendors_id(site, vendor):
    v = vendor()
    queue(site, subject="Reset", body="open this link")
    assert mailer.send_due().sent == 1
    ((path, headers, raw),) = v.calls
    sent = json.loads(raw)
    assert path == "/send" and headers["Authorization"] == "Bearer vendor-key-123"
    assert (sent["to"], sent["subject"], sent["text"]) == (
        "asha@example.org", "Reset", "open this link")
    assert sent["reference"] == row().public_id == headers["Idempotency-Key"]
    done = row()
    assert (done.provider, done.provider_ref) == ("http_api", "vend-77")
    assert mail.outbox == []


@pytest.mark.parametrize("answer, error", [
    (dict(status=500), "MailProviderError"), (dict(status=400), "MailProviderError"),
    (dict(status=302, location="http://127.0.0.1:1/elsewhere"), "MailProviderError"),
])
def test_a_vendor_that_refuses_or_redirects_means_try_again_later(site, vendor, answer, error):
    v = vendor(**answer)
    queue(site)
    result = mailer.send_due()
    assert (result.sent, result.retrying) == (0, 1)
    waiting = row()
    assert waiting.state == MailState.QUEUED and waiting.last_error == error
    assert len(v.calls) == 1                                  # a redirect is never followed


def test_a_vendor_answer_that_is_not_json_still_counts_as_accepted(site, vendor):
    vendor(body=b"queued")
    queue(site)
    assert mailer.send_due().sent == 1 and row().provider_ref == ""


def test_the_web_api_provider_is_refused_when_unconfigured_or_not_https(site, vendor, settings):
    v = vendor()
    for url, key, insecure in (("", "k", True), (v.url, "", True), (v.url, "k", False),
                               ("ftp://example.org/x", "k", True)):
        settings.MAIL_API_URL, settings.MAIL_API_KEY = url, key
        settings.MAIL_API_ALLOW_INSECURE = insecure
        QueuedEmail.objects.all().delete()
        queue(site)
        assert mailer.send_due().retrying == 1, (url, key, insecure)
    assert v.calls == []                                      # nothing was ever sent


def test_a_failure_keeps_only_the_error_class_never_the_address(site, vendor):
    vendor(status=500)
    queue(site, address="asha@example.org")
    mailer.send_due()
    assert "asha" not in row().last_error and "example" not in row().last_error


# ------------------------------------------------------------------ delivery receipts
def sent_with_id(site, provider_ref="vend-1"):
    queue(site)
    mailer.send_due()
    QueuedEmail.objects.update(provider_ref=provider_ref, provider="http_api")


def receipt(status, ref="vend-1", secret=SECRET, **extra):
    raw = json.dumps({"id": ref, "status": status, **extra}).encode()
    return raw, receipt_signature(raw, secret)


def test_a_signed_receipt_moves_the_message_forward(site):
    sent_with_id(site)
    assert row().delivery_status == "accepted"
    raw, sig = receipt("delivered")
    assert record_receipt(raw_body=raw, signature=sig) == 200
    assert row().delivery_status == "delivered"
    stats = mailer.mail_stats()
    assert (stats["delivered_24h"], stats["bounced_24h"]) == (1, 0)


def test_a_receipt_can_only_move_a_message_forward(site):
    sent_with_id(site)
    for status in ("bounced", "delivered", "deferred", "accepted"):
        raw, sig = receipt(status)
        assert record_receipt(raw_body=raw, signature=sig) == 200
    assert row().delivery_status == "bounced"            # the later, lower ones changed nothing
    raw, sig = receipt("complained")
    record_receipt(raw_body=raw, signature=sig)
    assert row().delivery_status == "complained"
    assert mailer.mail_stats()["bounced_24h"] == 1


def test_a_repeated_receipt_changes_nothing(site):
    sent_with_id(site)
    raw, sig = receipt("delivered")
    record_receipt(raw_body=raw, signature=sig)
    first = row().delivery_updated_at
    record_receipt(raw_body=raw, signature=sig, clock=FixedClock(utc_now() + dt.timedelta(hours=1)))
    assert row().delivery_updated_at == first


def test_a_receipt_with_a_wrong_or_missing_signature_is_refused_and_not_read(site):
    sent_with_id(site)
    raw, _ = receipt("bounced")
    for sig in ("", "0" * 64, receipt_signature(raw, "someone-elses-secret")):
        assert record_receipt(raw_body=raw, signature=sig) == 403
    assert row().delivery_status == "accepted"


def test_with_no_signing_secret_set_every_receipt_is_refused(site, settings):
    sent_with_id(site)
    raw, sig = receipt("delivered")
    settings.MAIL_WEBHOOK_SECRET = ""
    assert record_receipt(raw_body=raw, signature=sig) == 403
    assert record_receipt(raw_body=raw, signature=receipt_signature(raw, "")) == 403
    assert row().delivery_status == "accepted"


def test_unknown_messages_statuses_and_shapes_are_answered_without_telling_anything(site):
    sent_with_id(site)
    raw, sig = receipt("delivered", ref="nobody")
    assert record_receipt(raw_body=raw, signature=sig) == 200            # not ours: same answer
    raw, sig = receipt("opened")
    assert record_receipt(raw_body=raw, signature=sig) == 200             # not a status we track
    junk = b"not json at all"
    assert record_receipt(raw_body=junk, signature=receipt_signature(junk, SECRET)) == 400
    listy = b"[1, 2]"
    assert record_receipt(raw_body=listy, signature=receipt_signature(listy, SECRET)) == 400
    big = json.dumps({"id": "vend-1", "status": "delivered", "pad": "x" * 9000}).encode()
    assert record_receipt(raw_body=big, signature=receipt_signature(big, SECRET)) == 400
    assert row().delivery_status == "accepted"


def test_the_receipt_address_takes_no_sign_in_and_only_posts(site):
    sent_with_id(site)
    raw, sig = receipt("delivered")
    client = Client()
    r = client.post("/api/v1/webhooks/mail/status", raw, content_type="application/json",
                    HTTP_X_MAIL_SIGNATURE=sig)
    assert r.status_code == 200 and row().delivery_status == "delivered"
    bad = client.post("/api/v1/webhooks/mail/status", raw, content_type="application/json",
                      HTTP_X_MAIL_SIGNATURE="nope")
    assert bad.status_code == 403
    assert client.get("/api/v1/webhooks/mail/status").status_code == 405


# ------------------------------------------------------------------ the release check
def test_a_real_server_may_not_use_the_console_or_an_unknown_provider():
    assert release_problems(good()) == []
    assert any("console" in p for p in release_problems(good(MAIL_PROVIDER="console")))
    assert any("not smtp" in p for p in release_problems(good(MAIL_PROVIDER="carrier-pigeon")))


def test_the_web_api_provider_needs_an_https_address_a_key_and_a_receipt_secret():
    ok = dict(MAIL_PROVIDER="http_api", MAIL_API_URL="https://mail.vendor.example/v1/send",
              MAIL_API_KEY="Kq7vR2mXz9LpT4wYb8NsHd3GfJc6AeU1",
              MAIL_WEBHOOK_SECRET="Wn5vB8mQx2LpT7yYb4")
    assert release_problems(good(**ok)) == []
    assert any("https" in p for p in release_problems(good(**{**ok, "MAIL_API_URL": "http://x"})))
    assert any("key" in p for p in release_problems(good(**{**ok, "MAIL_API_KEY": ""})))
    assert any("signing secret" in p
               for p in release_problems(good(**{**ok, "MAIL_WEBHOOK_SECRET": "replace-me"})))


def test_a_namespace_without_mail_settings_still_means_smtp():
    conf = SimpleNamespace(**vars(good()))
    assert release_problems(conf) == []
