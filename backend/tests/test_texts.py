"""Text messages (DEV-194; GHE-16 TC05, TC49): only approved templates, never care details, sent by
the worker through Twilio when it is configured and through a logging stand-in in development."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from foundation.models import QueuedText
from foundation.texts import TEMPLATES, TextError, mask_number, queue_text, send_due_texts
from modules.comms import api as comms
from tests.test_clinician import Practice
from tests.test_email_notices import confirmed_booking, give_email, switch

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
NUMBER = "+447700900123"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.PATIENT_APP_URL = "http://patient.test"
    settings.TEXT_PROVIDER = ""
    comms.register_subscribers()


@pytest.fixture
def p():
    return Practice()


def queued(p, **kw):
    with tenant_transaction(p.clinic.ctx):
        return queue_text(**{"kind": "test", "to_number": NUMBER, "template": "update", **kw})


# ------------------------------------------------------------------ only approved wording
def test_only_the_approved_templates_exist_and_none_can_hold_free_text():
    assert set(TEMPLATES) == {"update", "code", "urgent", "urgent_staff"}
    for wording, allowed in TEMPLATES.values():
        assert len(wording) <= 160 and set(allowed) <= {"code"}


def test_a_text_is_built_from_its_template_and_nothing_else(p):
    row = queued(p)
    assert row.body == "There is an update for you on Zoiko Access. Sign in to see it."
    assert row.state == "queued" and len(row.content_digest) == 64
    code = queued(p, template="code", values={"code": "482913"})
    assert "482913" in code.body


@pytest.mark.parametrize("over", [
    {"template": "free_text"},                                   # not an approved template
    {"values": {"note": "Your HIV test result is ready"}},       # a value the template has not got
    {"template": "code"},                                        # a value missing
    {"template": "code", "values": {"code": "Chest pain"}},      # the wrong shape of value
    {"template": "code", "values": {"code": "12"}},
    {"to_number": "07700 900123"},                               # not an international number
    {"to_number": "+44 7700"}, {"to_number": ""},
])
def test_anything_outside_the_approved_shape_is_refused(p, over):
    with pytest.raises(ZoikoError):
        queued(p, **over)


def test_a_text_can_only_be_queued_inside_the_change(p):
    with pytest.raises(TextError):
        queue_text(kind="test", to_number=NUMBER, template="update")


# ------------------------------------------------------------------ sending
def test_the_worker_sends_and_then_wipes_the_text_and_masks_the_number(p):
    queued(p)
    calls = []
    assert send_due_texts(send=lambda number, body: calls.append((number, body))) == 1
    assert calls == [(NUMBER, TEMPLATES["update"][0])]
    row = QueuedText.objects.get()
    assert row.state == "sent" and row.body == "" and row.to_number == mask_number(NUMBER)
    assert NUMBER not in row.to_number


def test_with_no_provider_nothing_is_sent_and_the_text_waits(p, settings):
    queued(p)
    assert send_due_texts() == 0
    assert QueuedText.objects.get().state == "queued"


def test_a_failure_is_retried_later_and_given_up_after_the_last_attempt(p):
    queued(p)

    def broken(number, body):
        raise ConnectionError("down")

    assert send_due_texts(send=broken) == 0
    row = QueuedText.objects.get()
    assert row.state == "queued" and row.attempts == 1 and row.last_error == "ConnectionError"
    QueuedText.objects.update(attempts=5, next_attempt_at=row.created_at)
    send_due_texts(send=broken)
    row.refresh_from_db()
    assert row.state == "failed" and row.body == "" and NUMBER not in row.to_number


def test_twilio_is_called_with_the_account_and_the_text(p, settings, monkeypatch):
    settings.TEXT_PROVIDER = "twilio"
    settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN = "AC123", "secret-token"
    settings.TWILIO_FROM = "+447700900999"
    seen = {}

    class Answer:
        status = 201

        def read(self, *args):
            return b'{"sid": "SM9"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_open(request, timeout):
        seen["url"], seen["data"] = request.full_url, request.data.decode()
        seen["auth"] = request.headers["Authorization"]
        return Answer()

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    queued(p)
    assert send_due_texts() == 1
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert "To=%2B447700900123" in seen["data"] and "From=%2B447700900999" in seen["data"]
    assert seen["auth"].startswith("Basic ") and "secret-token" not in seen["auth"]


def test_twilio_without_its_account_values_sends_nothing_and_retries(p, settings):
    settings.TEXT_PROVIDER = "twilio"
    settings.TWILIO_ACCOUNT_SID = settings.TWILIO_AUTH_TOKEN = settings.TWILIO_FROM = ""
    queued(p)
    assert send_due_texts() == 0
    assert QueuedText.objects.get().last_error == "TextError"


# ------------------------------------------------------------------ the patient's choice
def give_phone(p, phone):
    r = p.post(f"people/{p.person}/profile", {"name": "Asha Rao", "birth_date": "1990-04-12",
                                              "phone": phone}, subject="pat")
    assert r.status_code == 200, r.content


def texts(p, on):
    return p.post("my/email-notices", {"texts": on}, subject="pat")


def verify_phone(p):
    """Prove the number the way a patient does (DEV-198): ask for a code and type it back."""
    import re
    assert p.post("my/phone-check", {}, subject="pat").status_code == 201
    body = QueuedText.objects.filter(kind="phone_check").order_by("-created_at").first().body
    code = re.search(r"\b(\d{6})\b", body).group(1)
    assert p.post("my/phone-check/confirm", {"code": code}, subject="pat").status_code == 200
    QueuedText.objects.filter(kind="phone_check").delete()      # keep only what this test is about


def test_a_patient_who_asked_for_texts_gets_the_generic_one_and_nothing_more(p):
    give_email(p)
    give_phone(p, "+44 7700 900123")
    verify_phone(p)
    switch(p, True)
    assert texts(p, True).status_code == 200
    assert json.loads(p.get("my/email-notices", subject="pat").content)["texts"] is True
    confirmed_booking(p)
    (row,) = QueuedText.objects.all()
    assert row.template == "update" and row.to_number == NUMBER
    assert "confirmed" not in row.body.lower() and "appointment" not in row.body.lower()


def test_no_text_unless_the_patient_asked_for_texts(p):
    give_email(p)
    switch(p, True)
    give_phone(p, "+44 7700 900123")
    confirmed_booking(p)
    assert QueuedText.objects.count() == 0


def test_no_text_to_a_number_the_patient_has_not_proved(p):
    give_email(p)
    switch(p, True)
    texts(p, True)
    give_phone(p, "+44 7700 900123")                             # never verified
    confirmed_booking(p)
    assert QueuedText.objects.count() == 0


def test_no_text_to_a_number_that_is_not_international(p):
    give_email(p)
    switch(p, True)
    texts(p, True)
    give_phone(p, "07700 900123")
    confirmed_booking(p)
    assert QueuedText.objects.count() == 0


def test_the_texts_switch_must_be_a_real_yes_or_no(p):
    assert texts(p, "yes").status_code == 422
