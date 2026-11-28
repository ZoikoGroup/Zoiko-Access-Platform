"""Proving a phone number before texts go to it (DEV-198)."""
import json
import re

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedText
from modules.comms import api as comms
from modules.comms.models import PhoneCheck
from modules.evidence.models import AuditEvent
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
NUMBER = "+44 7700 900123"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


@pytest.fixture
def p():
    practice = Practice()
    give_phone(practice, NUMBER)
    return practice


def give_phone(p, phone):
    r = p.post(f"people/{p.person}/profile", {"name": "Asha Rao", "birth_date": "1990-04-12",
                                              "phone": phone}, subject="pat")
    assert r.status_code == 200, r.content


def ask(p, subject="pat"):
    return p.post("my/phone-check", {}, subject=subject)


def last_code():
    row = QueuedText.objects.filter(kind="phone_check").order_by("-created_at").first()
    assert row is not None
    found = re.search(r"\b(\d{6})\b", row.body)
    assert found is not None
    return found.group(1)


def confirm(p, code, subject="pat"):
    return p.post("my/phone-check/confirm", {"code": code}, subject=subject)


def verified(p):
    return json.loads(p.get("my/email-notices", subject="pat").content)["phone_verified"]


def test_the_right_code_verifies_the_number(p):
    assert verified(p) is False
    assert ask(p).status_code == 201
    text = QueuedText.objects.get(kind="phone_check")
    assert text.template == "code" and text.to_number == NUMBER.replace(" ", "")
    assert confirm(p, last_code()).status_code == 200
    assert verified(p) is True


def test_neither_the_code_nor_the_number_is_stored_in_the_check(p):
    ask(p)
    code = last_code()
    with tenant_transaction(p.clinic.ctx):
        row = PhoneCheck.objects.get()
    assert code not in row.code_digest and "7700" not in row.number_digest


def test_a_wrong_code_uses_a_try_and_five_wrong_tries_end_the_code(p):
    ask(p)
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert confirm(p, wrong).status_code == 422
    with tenant_transaction(p.clinic.ctx):
        assert PhoneCheck.objects.get().attempts == 5                   # every try was counted
    assert confirm(p, code).status_code == 409                          # even the right one now
    assert verified(p) is False


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", 123456, None])
def test_a_code_must_be_six_digits(p, bad):
    ask(p)
    assert confirm(p, bad).status_code == 422


def test_an_expired_or_used_code_does_not_work(p):
    ask(p)
    code = last_code()
    assert confirm(p, code).status_code == 200
    assert confirm(p, code).status_code == 409                          # used
    ask(p)
    fresh = last_code()
    with tenant_transaction(p.clinic.ctx):
        PhoneCheck.objects.filter(used_at__isnull=True).update(expires_at="2000-01-01T00:00:00Z")
    assert confirm(p, fresh).status_code == 409                         # expired


def test_at_most_three_codes_an_hour(p):
    for _ in range(3):
        assert ask(p).status_code == 201
    assert ask(p).status_code == 429


def test_changing_the_number_ends_the_verification(p):
    ask(p)
    confirm(p, last_code())
    assert verified(p) is True
    give_phone(p, "+44 7700 900456")
    assert verified(p) is False


def test_no_code_without_an_international_number_on_the_profile(p):
    give_phone(p, "07700 900123")
    assert ask(p).status_code == 422
    give_phone(p, "")
    assert ask(p).status_code == 422


def test_only_patients_can_check_a_phone(p):
    for who in ("ops", "doc"):
        assert ask(p, subject=who).status_code == 403
        assert confirm(p, "123456", subject=who).status_code == 403


def test_each_step_is_audited(p):
    ask(p)
    confirm(p, "000000" if last_code() != "000000" else "111111")
    confirm(p, last_code())
    with tenant_transaction(p.clinic.ctx):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"comms.phone_code_sent", "comms.phone_code_wrong", "comms.phone_verified"} <= actions
