"""Decided 27 November 2026 (DEV-403): Stripe tells us when a payment changes, and SES (through an
SNS topic) tells us what happened to each email. Both are refused unless correctly signed; Stripe's
event only says WHICH payment, and its outcome is asked of Stripe again."""
import base64
import datetime as dt
import hashlib
import hmac
import json
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from django.test import Client

from foundation import sns_events
from foundation.models import QueuedEmail
from tests.test_payments import open_bill, pay_online
from tests.test_payments import p as p  # noqa: F401  (the fixture)
from tests.test_payments import registry as registry  # noqa: F401  (the fixture)
from tests.test_providers import stripe as stripe  # noqa: F401  (the fixture)
from tests.test_sendgrid import _queued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
TOPIC = "arn:aws:sns:eu-west-2:123456789012:zoiko-ses-events"
CERT_URL = "https://sns.eu-west-2.amazonaws.com/SimpleNotificationService-test.pem"
SECRET = "whsec_test_secret"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STRIPE_WEBHOOK_SECRET = SECRET
    settings.SES_EVENTS_TOPIC_ARN = TOPIC


# ------------------------------------------------------------------ Stripe
def stripe_post(event, secret=SECRET, stamp=None, tamper=False):
    body = json.dumps(event).encode()
    stamp = stamp or int(time.time())
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    if tamper:
        body = body.replace(b"checkout", b"Checkout")
    return Client().post("/api/v1/webhooks/stripe/events", body, content_type="application/json",
                         HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={sig}")


def session_event(session_id, kind="checkout.session.completed"):
    return {"type": kind, "data": {"object": {"id": session_id, "payment_status": "paid"}}}


def test_a_signed_event_makes_us_ask_stripe_and_the_invoice_is_paid(p, stripe):
    from tests.test_payments import bill_of
    inv = open_bill(p, 5000)
    made = json.loads(pay_online(p, inv).content)["payment"]
    stripe.state.update(payment_status="paid", status="complete")
    r = stripe_post(session_event(made["provider_ref"]))
    assert r.status_code == 200 and json.loads(r.content)["used"] is True
    assert bill_of(p, inv)["status"] == "paid"
    assert stripe.calls[-1][0] == "GET"                       # asked Stripe, not the event


def test_what_the_event_claims_is_never_taken_as_the_outcome(p, stripe):
    from tests.test_payments import bill_of
    inv = open_bill(p, 5000)
    made = json.loads(pay_online(p, inv).content)["payment"]
    stripe_post(session_event(made["provider_ref"]))            # Stripe itself still says open
    assert bill_of(p, inv)["status"] == "issued"


@pytest.mark.parametrize("bad", [{"secret": "whsec_other"}, {"stamp": 1_000_000_000},
                                 {"tamper": True}])
def test_an_unsigned_stale_or_changed_event_is_refused(bad):
    assert stripe_post(session_event("cs_test_x"), **bad).status_code == 401


def test_other_events_and_payments_that_are_not_ours_are_ignored(p, stripe):
    assert json.loads(stripe_post({"type": "customer.created", "data": {}}).content)["used"] \
        is False
    assert json.loads(stripe_post(session_event("cs_test_" + "z" * 58)).content)["used"] is False


# ------------------------------------------------------------------ SES through SNS
@pytest.fixture
def aws_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    fetched = []

    def fetch(url):
        fetched.append(url)
        return cert

    monkeypatch.setattr(sns_events, "fetch_certificate", fetch)
    return key, fetched


def sns_message(key, event, *, kind="Notification", topic=TOPIC, cert_url=CERT_URL, **extra):
    message = {"Type": kind, "MessageId": "m-1", "TopicArn": topic,
               "Message": json.dumps(event), "Timestamp": "2026-11-27T10:00:00.000Z",
               "SignatureVersion": "2", "SigningCertURL": cert_url, **extra}
    message["Signature"] = base64.b64encode(key.sign(
        sns_events.canonical(message), padding.PKCS1v15(), hashes.SHA256())).decode()
    return message


def ses_post(message):
    return Client().post("/api/v1/webhooks/ses/events", json.dumps(message),
                         content_type="text/plain")


def ses_event(ref, kind="Delivery", **more):
    return {"eventType": kind, "mail": {"tags": {"zoiko_ref": [ref]},
                                        "destination": ["asha@example.org"]}, **more}


def delivery_of(ref):
    return QueuedEmail.objects.get(public_id=ref).delivery_status


def test_a_signed_delivery_is_recorded_from_our_topic_only(aws_key):
    key, fetched = aws_key
    ref = _queued()
    r = ses_post(sns_message(key, ses_event(ref)))
    assert r.status_code == 200 and delivery_of(ref) == "delivered"
    assert fetched == [CERT_URL]


def test_a_permanent_bounce_is_a_bounce_and_a_transient_one_only_a_delay(aws_key):
    key, _ = aws_key
    hard, soft = _queued(), _queued()
    ses_post(sns_message(key, ses_event(hard, "Bounce", bounce={"bounceType": "Permanent"})))
    ses_post(sns_message(key, ses_event(soft, "Bounce", bounce={"bounceType": "Transient"})))
    assert delivery_of(hard) == "bounced" and delivery_of(soft) == "deferred"


def test_another_topic_a_strange_certificate_host_or_a_changed_message_is_refused(aws_key):
    key, _ = aws_key
    ref = _queued()
    assert ses_post(sns_message(key, ses_event(ref), topic=TOPIC + "-other")).status_code == 401
    assert ses_post(sns_message(key, ses_event(ref),
                                cert_url="https://evil.example/cert.pem")).status_code == 401
    changed = sns_message(key, ses_event(ref))
    changed["Message"] = json.dumps(ses_event("someone-else"))
    assert ses_post(changed).status_code == 401
    assert delivery_of(ref) in ("", None)


def test_the_subscription_to_our_topic_is_confirmed(aws_key, monkeypatch):
    key, _ = aws_key
    visited: list[str] = []

    def confirm(message: dict) -> bool:
        visited.append(message["SubscribeURL"])
        return True

    monkeypatch.setattr(sns_events, "confirm_subscription", confirm)
    url = "https://sns.eu-west-2.amazonaws.com/?Action=ConfirmSubscription&Token=t"
    r = ses_post(sns_message(key, {}, kind="SubscriptionConfirmation", SubscribeURL=url,
                             Token="t"))
    assert r.status_code == 200 and visited == [url]
