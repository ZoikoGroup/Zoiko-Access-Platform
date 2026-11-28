"""Real texts go only to a market a signed country pack has switched on (GHE-16 TC01, TC07;
DEV-228). The development stand-in sends nothing real, so it is not checked."""
import json

import pytest

from foundation.models import QueuedText
from modules.comms import api as comms
from tests.test_clinician import Practice
from tests.test_emergency_number import set_country
from tests.test_packs import full, signed, step
from tests.test_phone_check import NUMBER, give_phone
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


@pytest.fixture
def real_texts(settings):
    settings.TEXT_PROVIDER = "twilio"            # nothing is sent: the worker does not run here


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12"), ("noor", "R12")):
        w.staff(subject, role)
    return w


@pytest.fixture
def p():
    practice = Practice()
    give_phone(practice, NUMBER)
    return practice


def ask_code(p):
    return p.post("my/phone-check", {}, subject="pat")


def codes():
    return QueuedText.objects.filter(kind="phone_check").count()


def messaging_pack(world, on=True):
    manifest = full()
    manifest["capabilities"] = {"provider_directory": True, "booking": True, "messaging": on}
    return signed(world, manifest=manifest)


def test_no_real_text_goes_to_a_market_with_no_country_or_no_pack(world, p, real_texts):
    r = ask_code(p)
    assert r.status_code == 403 and json.loads(r.content)["code"] == "JURISDICTION_INACTIVE"
    set_country(world, p, "GB")
    assert ask_code(p).status_code == 403
    assert codes() == 0


def test_a_pack_that_does_not_switch_messaging_on_is_not_enough(world, p, real_texts):
    set_country(world, p, "GB")
    messaging_pack(world, on=False)
    assert ask_code(p).status_code == 403


def test_a_signed_pack_with_messaging_lets_the_text_go(world, p, real_texts):
    set_country(world, p, "GB")
    messaging_pack(world)
    r = ask_code(p)
    assert r.status_code in (200, 201), r.content
    assert codes() == 1


def test_a_suspended_pack_stops_texts_again(world, p, real_texts):
    set_country(world, p, "GB")
    pack = messaging_pack(world)
    assert ask_code(p).status_code in (200, 201)
    assert step(world, pack, "suspend", "iyer", note="privacy review reopened").status_code == 200
    assert ask_code(p).status_code == 403


def test_the_development_stand_in_is_not_checked(p, settings):
    settings.TEXT_PROVIDER = "console"
    assert ask_code(p).status_code in (200, 201)
