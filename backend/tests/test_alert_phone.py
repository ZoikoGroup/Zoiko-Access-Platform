"""A clinician's own mobile number for critical-result texts (DEV-334)."""
import re

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedText
from foundation.texts import TEMPLATES
from modules.comms import api as comms
from modules.comms.models import EmailPreference
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
NUMBER = "+44 7700 900456"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def last_code():
    row = QueuedText.objects.filter(kind="phone_check").order_by("-created_at").first()
    return re.search(r"\b(\d{6})\b", row.body).group(1)


def proved(p, subject="doc"):
    assert p.post("staff/alert-phone", {"number": NUMBER}, subject=subject).status_code == 201
    r = p.post("staff/alert-phone/confirm", {"code": last_code()}, subject=subject)
    assert r.status_code == 200, r.content
    return r.json()["alert_phone"]


def critical_for(p, actor):
    with tenant_transaction(p.clinic.ctx):
        return comms.alert_critical_result(actor)


def test_a_doctor_proves_their_number_and_gets_a_text_for_a_critical_result(p):
    got = proved(p)
    assert got["verified"] is True and got["number"].startswith("+44")
    assert "900456" not in got["number"]
    on = p.post("staff/alert-phone", {"critical_texts": True}, subject="doc")
    assert on.status_code == 200 and on.json()["alert_phone"]["critical_texts"] is True
    assert critical_for(p, p.doc) == 1  # the text (no confirmed email in this test)
    [text] = QueuedText.objects.filter(kind="critical_result")
    assert text.body == TEMPLATES["urgent"][0]  # fixed wording: never what or whom


def test_no_text_without_a_proved_number_or_without_asking(p):
    assert p.post("staff/alert-phone", {"critical_texts": True}, subject="doc").status_code == 409
    assert p.post("staff/alert-phone", {"number": NUMBER}, subject="doc").status_code == 201
    assert critical_for(p, p.doc) == 0  # not proved, not asked
    p.post("staff/alert-phone/confirm", {"code": last_code()}, subject="doc")
    assert critical_for(p, p.doc) == 0  # proved but not asked
    assert not QueuedText.objects.filter(kind="critical_result").exists()


def test_the_number_is_stored_encrypted_and_a_wrong_code_is_refused(p):
    assert p.post("staff/alert-phone", {"number": "07700 900456"}, subject="doc").status_code == 422
    p.post("staff/alert-phone", {"number": NUMBER}, subject="doc")
    assert p.post("staff/alert-phone/confirm", {"code": "000000"}, subject="doc").status_code in (
        409, 422)
    with tenant_transaction(p.clinic.ctx):
        stored = EmailPreference.objects.get(actor_ref=p.doc).alert_phone_enc
    assert stored and "900456" not in stored


def test_only_clinic_staff_and_clinicians_have_an_alert_number(p):
    assert p.get("staff/alert-phone", subject="pat").status_code == 403
    assert p.get("staff/alert-phone", subject="doc").status_code == 200
    assert p.get("staff/alert-phone", subject="ops").status_code == 200



# ------------------------------------------------ held requests, texted to administrators (DEV-336)
def test_an_administrator_who_asked_gets_one_hold_text_a_day(p):
    import datetime as dt

    from modules.comms.alert_phone import text_hold
    from modules.trust import api as trust

    boss = p.login("boss", trust.ActorType.FACILITY_USER, "R13")
    proved(p, "boss")
    assert p.post("staff/alert-phone", {"hold_texts": True}, subject="boss").status_code == 200
    assert p.post("staff/alert-phone", {"hold_texts": True}, subject="doc").status_code == 403
    today = dt.date(2026, 11, 19)
    with tenant_transaction(p.clinic.ctx):
        assert text_hold(boss, today) == 1
        assert text_hold(boss, today) == 0  # once a day
        assert text_hold(boss, today + dt.timedelta(days=1)) == 1
        assert text_hold(p.doc, today) == 0  # never asked
    [first, _] = QueuedText.objects.filter(kind="hold_escalation")
    assert first.body == TEMPLATES["urgent_staff"][0]
