"""The local emergency number a patient sees (GHE-08 AT47, DEV-185): only from a signed, current
country pack for the clinic's country; never invented, never guessed between disagreeing packs."""
import json

import pytest

from modules.trust import api as trust
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_packs import full, signed, step
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12"), ("noor", "R12")):
        w.staff(subject, role)
    return w


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def set_country(world, p, country):
    return world.client.post(
        API + f"platform/customers/{p.clinic.tenant_public_id}/country",
        json.dumps({"country": country}), content_type="application/json",
        **world.header(world.tenant_public_id, "sam"))


def number(p, subject="pat"):
    r = p.get("my/emergency", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["number"]


def with_number(value, days=30):
    return full(days=days, settings={"data_request_days": days, "emergency_number": value,
                                     "default_language": "en-GB", "time_zone": "Europe/London"})


def test_a_patient_sees_the_number_from_a_signed_pack_for_their_clinics_country(world, p):
    signed(world, ref="gb-care-navigation", manifest=with_number("999"))
    set_country(world, p, "GB")
    assert number(p) == "999"


def test_no_country_or_no_pack_means_no_number_is_shown(world, p):
    assert number(p) is None                                     # no country, no pack
    signed(world, ref="gb-care-navigation", manifest=with_number("999"))
    assert number(p) is None                                     # a pack, but no country set
    set_country(world, p, "DE")
    assert number(p) is None                                     # a pack for another country


def test_a_suspended_pack_stops_showing_its_number(world, p):
    pack = signed(world, ref="gb-care-navigation", manifest=with_number("999"))
    set_country(world, p, "GB")
    assert number(p) == "999"
    step(world, pack, "suspend", "iyer", note="Regulator query")
    assert number(p) is None


def test_a_pack_that_states_no_number_shows_none(world, p):
    signed(world, ref="gb-care-navigation", manifest=with_number(""))
    set_country(world, p, "GB")
    assert number(p) is None


def test_packs_that_disagree_give_no_answer_but_packs_that_agree_do(world, p):
    signed(world, ref="gb-one", manifest=with_number("999"))
    set_country(world, p, "GB")
    assert number(p) == "999"
    signed(world, ref="gb-two", manifest=with_number("112"))
    assert number(p) is None                                     # two numbers: guess neither
    signed(world, ref="gb-three", manifest=with_number("999"))
    assert number(p) is None                                     # still one that differs


def test_only_patients_ask_for_it(p):
    for who in ("ops", "doc", "boss"):
        assert p.get("my/emergency", subject=who).status_code == 403
