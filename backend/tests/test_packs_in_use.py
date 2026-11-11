import copy
import datetime as dt
import json
import time

import pytest

from foundation.timeutil import UTC
from modules.packs import api as packs
from modules.packs.signing import build_signed_notice, build_signed_pack, generate_keypair
from modules.trust import api as trust
from tests.test_api import API, Web, book
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

ALL_ON = {"booking.request": {"enabled": True}, "messaging.secure": {"enabled": True},
          "referral.exchange": {"enabled": True}}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Country:
    """A clinic with a patient, a doctor and an administrator, in a cell that serves one country."""

    def __init__(self, settings, *, enforced=True, country="GB"):
        settings.PACKS_ENFORCED = enforced
        settings.ZOIKO_COUNTRY = country
        self.private, public = generate_keypair()
        settings.PACK_PUBLIC_KEYS = f"g1:{public}"
        self.web = Web()
        self.web.patient("pat")
        self.web.login("doc", trust.ActorType.CLINICIAN, "R03")

    def install(self, capabilities, *, version=1, epoch=0, ends_in=dt.timedelta(days=30),
                country="GB"):
        now = dt.datetime.now(UTC)
        return packs.install_pack(build_signed_pack(
            private_key_b64=self.private, key_id="g1", country=country, version=version,
            issued_at=now - dt.timedelta(days=1), valid_from=now - dt.timedelta(days=1),
            expires_at=now + ends_in, revocation_epoch=epoch,
            capabilities=copy.deepcopy(capabilities)))

    def book(self):
        return book(self.web, self.web.slots[0]["public_id"])

    def code(self, response):
        return json.loads(response.content).get("code")


@pytest.fixture
def cell(settings):
    return Country(settings)


# ------------------------------------------------------------------ enforcement is a switch
def test_with_enforcement_off_nothing_changes_and_no_pack_is_needed(settings):
    off = Country(settings, enforced=False, country="")
    assert off.book().status_code == 201


def test_with_enforcement_on_and_no_pack_every_gated_feature_refuses(cell):
    refused = cell.book()
    assert refused.status_code == 403 and cell.code(refused) == "JURISDICTION_INACTIVE"
    assert cell.web.post("messages/threads", {}, subject="pat").status_code == 403
    assert cell.web.post("clinician/referrals", {}, subject="doc").status_code == 403
    assert cell.web.post("referrals/x/decide", {}, subject="doc").status_code == 403


def test_reading_what_already_exists_is_never_gated(cell):
    assert cell.web.get("bookings", subject="pat").status_code == 200
    assert cell.web.get("services", subject="pat").status_code == 200
    assert cell.web.get("clinician/referrals", subject="doc").status_code == 200
    assert cell.web.get("messages/threads", subject="pat").status_code == 200


def test_enforcement_with_no_country_set_refuses_too(settings):
    nowhere = Country(settings, country="")
    nowhere.install(ALL_ON)
    assert nowhere.book().status_code == 403             # no country: no pack can apply


# ------------------------------------------------------------------ the pack decides
def test_a_pack_that_switches_booking_on_lets_patients_book(cell):
    cell.install({"booking.request": {"enabled": True}})
    assert cell.book().status_code == 201
    assert cell.web.post("messages/threads", {}, subject="pat").status_code == 403   # not listed


def test_a_capability_the_pack_lists_as_off_stays_off(cell):
    cell.install({"booking.request": {"enabled": False}, "messaging.secure": {"enabled": True}})
    assert cell.code(cell.book()) == "JURISDICTION_INACTIVE"
    asked = cell.web.post("messages/threads", {}, subject="pat")
    assert asked.status_code == 422                       # allowed through; the body is what failed


def test_each_feature_is_gated_by_its_own_capability(cell):
    cell.install({"referral.exchange": {"enabled": True}})
    assert cell.web.post("clinician/referrals", {}, subject="doc").status_code == 422
    refused = cell.web.post("messages/threads/x/reply", {"body": "hi"}, subject="pat")
    assert cell.code(refused) == "JURISDICTION_INACTIVE"


def test_another_countrys_pack_does_not_switch_this_one_on(cell):
    cell.install(ALL_ON, country="FR")
    assert cell.code(cell.book()) == "JURISDICTION_INACTIVE"


def test_an_expired_pack_switches_everything_off_again(cell):
    cell.install(ALL_ON, ends_in=dt.timedelta(seconds=2))
    assert cell.book().status_code == 201
    time.sleep(2.5)
    assert cell.code(cell.web.post("messages/threads", {}, subject="pat")) == (
        "JURISDICTION_INACTIVE")


def test_a_revoked_pack_switches_everything_off_at_once(cell):
    cell.install(ALL_ON, epoch=1)
    assert cell.book().status_code == 201
    packs.apply_revocation_notice(build_signed_notice(
        private_key_b64=cell.private, key_id="g1", min_epoch=2,
        issued_at=dt.datetime.now(UTC)))
    assert cell.code(cell.web.post("clinician/referrals", {}, subject="doc")) == (
        "JURISDICTION_INACTIVE")


def test_a_newer_pack_can_switch_a_feature_off(cell):
    cell.install(ALL_ON, version=1)
    cell.install({**ALL_ON, "booking.request": {"enabled": False}}, version=2)
    assert cell.code(cell.book()) == "JURISDICTION_INACTIVE"


# ------------------------------------------------------------------ what Zoiko staff see
def test_the_platform_page_says_whether_packs_are_enforced_and_what_this_country_has(cell):
    cell.install({"booking.request": {"enabled": True}, "messaging.secure": {"enabled": False}})
    platform = Platform()
    platform.staff("sam", "R11")
    page = json.loads(platform.client.get(
        API + "platform/packs", **platform.header(platform.tenant_public_id, "sam")).content)
    assert page["enforced"] is True and page["country"] == "GB"
    assert page["country_capabilities"] == ["booking.request"]


def test_the_platform_page_is_honest_when_nothing_is_enforced(settings):
    quiet = Country(settings, enforced=False, country="")
    platform = Platform()
    platform.staff("sam", "R11")
    page = json.loads(platform.client.get(
        API + "platform/packs", **platform.header(platform.tenant_public_id, "sam")).content)
    assert (page["enforced"], page["country"], page["country_capabilities"]) == (False, "", [])
    assert quiet.book().status_code == 201
