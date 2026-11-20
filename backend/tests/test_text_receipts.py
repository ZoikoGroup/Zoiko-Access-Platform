"""Twilio's delivery receipts for texts (DEV-199; GHE-16 TC21): signed, forward-only, idempotent."""
import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedText
from foundation.texts import queue_text, send_due_texts, twilio_signature
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
URL = "https://api.example.co.uk/api/v1/webhooks/twilio/status"
TOKEN = "test-auth-token"


@pytest.fixture(autouse=True)
def twilio(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.TWILIO_AUTH_TOKEN = TOKEN
    settings.TWILIO_STATUS_URL = URL


@pytest.fixture
def sent():
    """One text, sent, with Twilio's id SM1 recorded."""
    p = Practice()
    with tenant_transaction(p.clinic.ctx):
        queue_text(kind="test", to_number="+447700900123", template="update")
    assert send_due_texts(send=lambda number, body: "SM1") == 1
    return QueuedText.objects.get()


def receipt(status, sid="SM1", signature=None, token=TOKEN):
    params = {"MessageSid": sid, "MessageStatus": status, "AccountSid": "AC123"}
    sig = signature if signature is not None else twilio_signature(URL, params, token)
    return Client().post("/api/v1/webhooks/twilio/status", params, HTTP_X_TWILIO_SIGNATURE=sig)


def status_of(row):
    row.refresh_from_db()
    return row.delivery_status


def test_the_provider_id_is_kept_when_a_text_is_sent(sent):
    assert sent.provider_ref == "SM1" and sent.delivery_status == ""


def test_a_signed_receipt_records_how_far_the_text_got(sent):
    assert receipt("sent").status_code == 200
    assert status_of(sent) == "sent"
    assert receipt("delivered").status_code == 200
    assert status_of(sent) == "delivered" and sent.delivery_updated_at is not None


def test_a_late_receipt_never_moves_a_text_backwards(sent):
    receipt("delivered")
    assert receipt("sent").status_code == 200                   # arrives late, out of order
    assert receipt("queued").status_code == 200
    assert status_of(sent) == "delivered"


def test_a_repeated_receipt_changes_nothing(sent):
    receipt("sent")
    first = QueuedText.objects.get().delivery_updated_at
    receipt("sent")
    assert QueuedText.objects.get().delivery_updated_at == first


def test_a_failure_is_recorded_and_stays(sent):
    receipt("undelivered")
    receipt("sent")
    assert status_of(sent) == "undelivered"


@pytest.mark.parametrize("signature", ["", "bm90IGEgc2lnbmF0dXJl", None])
def test_a_wrong_or_missing_signature_is_refused_and_nothing_changes(sent, signature):
    sig = "" if signature is None else signature
    bad = receipt("delivered", signature=sig) if signature is not None else receipt(
        "delivered", token="someone-elses-token")
    assert bad.status_code == 403
    assert status_of(sent) == ""


def test_without_a_configured_token_every_receipt_is_refused(sent, settings):
    settings.TWILIO_AUTH_TOKEN = ""
    assert receipt("delivered", token="").status_code == 403


def test_an_unknown_message_or_status_is_accepted_quietly_and_ignored(sent):
    assert receipt("delivered", sid="SM-nobody").status_code == 200
    assert receipt("teleported").status_code == 200
    assert status_of(sent) == ""


def test_the_signature_matches_twilios_published_method():
    # HMAC-SHA1 over the URL followed by each form field name and value in name order, base64.
    import base64
    import hashlib
    import hmac
    params = {"b": "2", "a": "1"}
    expected = base64.b64encode(
        hmac.new(b"tok", (URL + "a1b2").encode(), hashlib.sha1).digest()).decode()
    assert twilio_signature(URL, params, "tok") == expected
