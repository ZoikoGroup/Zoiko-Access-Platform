"""Email through SendGrid's web API (DEV-350)."""
import json

import pytest
from django.core.mail import EmailMessage

from foundation import sendgrid_backend
from foundation.sendgrid_backend import SendGridBackend, SendGridError


class Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def calls(monkeypatch, settings):
    settings.SENDGRID_API_KEY = "SG.test-key"
    settings.SENDGRID_API_HOST = ""
    seen = []

    def fake(request, timeout):
        seen.append(request)
        return Answer(202)

    monkeypatch.setattr(sendgrid_backend.urllib.request, "urlopen", fake)
    return seen


def test_a_message_goes_to_the_api_with_no_tracking_and_the_key_only_in_the_header(calls):
    msg = EmailMessage("An update", "Sign in to see it.", "Zoiko Access <no-reply@zoiko.example>",
                       ["asha@example.org"])
    assert SendGridBackend().send_messages([msg]) == 1
    [req] = calls
    assert req.full_url == "https://api.sendgrid.com/v3/mail/send"
    assert req.get_header("Authorization") == "Bearer SG.test-key"
    sent = json.loads(req.data)
    assert sent["personalizations"][0]["to"] == [{"email": "asha@example.org"}]
    assert sent["from"] == {"email": "no-reply@zoiko.example", "name": "Zoiko Access"}
    assert sent["tracking_settings"]["click_tracking"]["enable"] is False
    assert "SG.test-key" not in req.data.decode()


def test_a_failure_raises_so_the_queue_tries_again_and_no_key_sends_nothing(calls, settings,
                                                                           monkeypatch):
    def broken(request, timeout):
        raise OSError("down")

    monkeypatch.setattr(sendgrid_backend.urllib.request, "urlopen", broken)
    msg = EmailMessage("x", "y", "a@zoiko.example", ["b@example.org"])
    with pytest.raises(OSError):
        SendGridBackend().send_messages([msg])
    settings.SENDGRID_API_KEY = ""
    with pytest.raises(SendGridError):
        SendGridBackend().send_messages([msg])
    settings.SENDGRID_API_KEY = "k"
    settings.SENDGRID_API_HOST = "http://plain.example"
    with pytest.raises(SendGridError):
        SendGridBackend().send_messages([msg])


# ------------------------------------------------ delivery receipts (DEV-354)
@pytest.fixture
def signer(settings):
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    der = key.public_key().public_bytes(serialization.Encoding.DER,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    settings.SENDGRID_WEBHOOK_PUBLIC_KEY = base64.b64encode(der).decode()

    def sign(body: bytes, ts: str) -> str:
        return base64.b64encode(key.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))).decode()

    return sign


def _queued(ref_kind="verify_email"):
    from foundation.cell_context import tenant_transaction
    from foundation.mail import queue_email
    from tests.test_clinician import Practice

    p = Practice()
    with tenant_transaction(p.clinic.ctx):
        return queue_email(kind=ref_kind, to_address="asha@example.org",
                           values={"link": "https://app.test/?token=x"}).public_id


def post_events(events, sign=None, ts=None, tamper=False):
    import time

    from django.test import Client

    body = json.dumps(events).encode()
    ts = ts or str(int(time.time()))
    headers = {}
    if sign:
        headers["HTTP_X_TWILIO_EMAIL_EVENT_WEBHOOK_SIGNATURE"] = sign(body, ts)
        headers["HTTP_X_TWILIO_EMAIL_EVENT_WEBHOOK_TIMESTAMP"] = ts
    if tamper:
        body = body.replace(b"delivered", b"bounce")
    return Client().post("/api/v1/webhooks/sendgrid/events", body,
                         content_type="application/json", **headers)


@pytest.mark.django_db(transaction=True)
def test_a_signed_receipt_is_recorded_and_a_final_one_is_never_undone(signer):
    import time

    from foundation.models import QueuedEmail

    ref = _queued()
    now = int(time.time())
    r = post_events([{"event": "delivered", "zoiko_ref": ref, "timestamp": now,
                      "email": "asha@example.org"}], sign=signer)
    assert r.status_code == 200 and r.json()["recorded"] == 1
    row = QueuedEmail.objects.get(public_id=ref)
    assert row.delivery_status == "delivered" and row.delivery_at is not None
    later = post_events([{"event": "deferred", "zoiko_ref": ref, "timestamp": now}], sign=signer)
    assert later.json()["recorded"] == 0  # delivered is final
    ignored = post_events([{"event": "open", "zoiko_ref": ref, "timestamp": now},
                           {"event": "delivered", "zoiko_ref": "nobody", "timestamp": now}],
                          sign=signer)
    assert ignored.json()["recorded"] == 0


@pytest.mark.django_db(transaction=True)
def test_an_unsigned_stale_or_tampered_receipt_is_refused(signer, settings):
    import time

    ref = _queued()
    event = [{"event": "delivered", "zoiko_ref": ref, "timestamp": int(time.time())}]
    assert post_events(event).status_code == 401  # no signature
    assert post_events(event, sign=signer, ts=str(int(time.time()) - 3600)).status_code == 401
    assert post_events(event, sign=signer, tamper=True).status_code == 401
    settings.SENDGRID_WEBHOOK_PUBLIC_KEY = ""
    assert post_events(event, sign=signer).status_code == 401  # no key set: nothing accepted


def test_the_api_call_carries_our_email_id_back_for_the_receipt(calls):
    msg = EmailMessage("x", "y", "a@zoiko.example", ["b@example.org"],
                       headers={"X-Zoiko-Ref": "abc123"})
    SendGridBackend().send_messages([msg])
    assert json.loads(calls[0].data)["custom_args"] == {"zoiko_ref": "abc123"}
