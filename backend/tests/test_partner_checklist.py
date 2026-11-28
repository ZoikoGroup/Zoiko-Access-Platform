"""Decided 27 November 2026 (DEV-401): the first partners are one laboratory and one pharmacy, and
before going live a Zoiko officer records four checks: identity, a signed contract, the security
questionnaire and a passed sandbox test run."""
import json

import pytest

from modules.trust import api as trust
from tests.partner_support import CHECKS, check, record_all_checks
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def partner(p, kind="partner.lab."):
    r = p.post("staff/integrations/partners", {"label": "First partner",
                                               "allowed_types": [kind]}, subject="boss")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["client"]


def go_live(p, made):
    return p.post(f"staff/integrations/partners/{made['public_id']}/issue-secret", {},
                  subject="boss")


def test_there_are_four_checks_and_three_are_not_enough(p):
    made = partner(p)
    assert set(made["checks"]) == {"identity", "legal", "security", "sandbox_test"}
    for which in CHECKS[:3]:
        assert check(p, made["public_id"], which).status_code == 200
    assert go_live(p, made).status_code == 403                     # no sandbox test run yet
    assert check(p, made["public_id"], "sandbox_test",
                 note="Ran the sandbox test suite: all passed").status_code == 200
    assert go_live(p, made).status_code == 200


@pytest.mark.parametrize("kind", ["partner.lab.", "partner.pharmacy."])
def test_a_laboratory_or_a_pharmacy_can_go_live(p, kind):
    made = partner(p, kind)
    record_all_checks(p, made["public_id"])
    r = go_live(p, made)
    assert r.status_code == 200 and json.loads(r.content)["client"]["secret"]


def test_other_kinds_of_partner_wait_even_with_every_check(p):
    made = partner(p, "partner.imaging.")
    record_all_checks(p, made["public_id"])
    r = go_live(p, made)
    assert r.status_code == 403 and json.loads(r.content)["fields"] == ["partner_kind"]


def test_the_list_of_kinds_is_a_setting(p, settings):
    settings.PARTNER_LIVE_KINDS = ("partner.lab.", "partner.pharmacy.", "partner.imaging.")
    made = partner(p, "partner.imaging.")
    record_all_checks(p, made["public_id"])
    assert go_live(p, made).status_code == 200
