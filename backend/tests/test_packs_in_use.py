"""Booking, messaging and referrals switched on by the one country-pack system: a signed, current pack
for the customer's own country (GHE-04, DEV-181, DEV-364)."""
import json

import pytest

from modules.trust import api as trust
from tests.test_api import API, Web, book
from tests.test_country_deadline import set_country
from tests.test_packs import full, signed, step
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

ALL_ON = ("booking", "messaging", "referrals")


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Market:
    """Zoiko's platform with its pack reviewers, and one clinic with a patient and a doctor."""

    def __init__(self, settings, *, enforced=True, country="GB"):
        settings.PACKS_ENFORCED = enforced
        self.world = Platform()
        for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12")):
            self.world.staff(subject, role)
        self.web = Web()
        self.web.patient("pat")
        self.web.login("doc", trust.ActorType.CLINICIAN, "R03")
        if country:
            assert set_country(self.world, self.web, country).status_code == 200

    def sign(self, *capabilities, country="GB", ref="gb-care-navigation"):
        manifest = full()
        manifest["capabilities"] = {name: True for name in capabilities}
        return signed(self.world, ref=ref, country=country, manifest=manifest)

    def book(self):
        return book(self.web, self.web.slots[0]["public_id"])

    def code(self, response):
        return json.loads(response.content).get("code")

    def page(self):
        r = self.world.client.get(API + "platform/packs",
                                  **self.world.header(self.world.tenant_public_id, "sam"))
        assert r.status_code == 200, r.content
        return json.loads(r.content)


@pytest.fixture
def market(settings):
    return Market(settings)


# ------------------------------------------------------------------ enforcement is a switch
def test_with_enforcement_off_nothing_changes_and_no_pack_is_needed(settings):
    off = Market(settings, enforced=False, country="")
    assert off.book().status_code == 201


def test_with_enforcement_on_and_no_pack_every_gated_feature_refuses(market):
    refused = market.book()
    assert refused.status_code == 403 and market.code(refused) == "JURISDICTION_INACTIVE"
    assert market.web.post("messages/threads", {}, subject="pat").status_code == 403
    assert market.web.post("clinician/referrals", {}, subject="doc").status_code == 403
    assert market.web.post("referrals/x/decide", {}, subject="doc").status_code == 403


def test_reading_what_already_exists_is_never_gated(market):
    assert market.web.get("bookings", subject="pat").status_code == 200
    assert market.web.get("services", subject="pat").status_code == 200
    assert market.web.get("clinician/referrals", subject="doc").status_code == 200
    assert market.web.get("messages/threads", subject="pat").status_code == 200


def test_a_customer_with_no_country_is_refused_too(settings):
    nowhere = Market(settings, country="")
    nowhere.sign(*ALL_ON)
    assert nowhere.book().status_code == 403             # no country: no pack can apply


# ------------------------------------------------------------------ the pack decides
def test_a_pack_that_switches_booking_on_lets_patients_book(market):
    market.sign("provider_directory", "booking")
    assert market.book().status_code == 201
    assert market.web.post("messages/threads", {}, subject="pat").status_code == 403   # not on


def test_each_feature_is_gated_by_its_own_capability(market):
    market.sign("referrals")
    assert market.web.post("clinician/referrals", {}, subject="doc").status_code == 422
    refused = market.web.post("messages/threads/x/reply", {"body": "hi"}, subject="pat")
    assert market.code(refused) == "JURISDICTION_INACTIVE"
    assert market.code(market.book()) == "JURISDICTION_INACTIVE"


def test_another_countrys_pack_does_not_switch_this_one_on(market):
    market.sign(*ALL_ON, country="FR", ref="fr-care-navigation")
    assert market.code(market.book()) == "JURISDICTION_INACTIVE"


def test_moving_the_customer_to_another_country_follows_that_countrys_pack(market):
    market.sign(*ALL_ON)
    assert market.book().status_code == 201
    assert set_country(market.world, market.web, "FR").status_code == 200
    assert market.code(market.web.post("messages/threads", {}, subject="pat")) == (
        "JURISDICTION_INACTIVE")


def test_a_suspended_pack_switches_everything_off_at_once(market):
    pack = market.sign(*ALL_ON)
    assert market.book().status_code == 201
    assert step(market.world, pack, "suspend", "iyer", note="Regulator query").status_code == 200
    assert market.code(market.web.post("clinician/referrals", {}, subject="doc")) == (
        "JURISDICTION_INACTIVE")


def test_a_retired_pack_counts_for_nothing(market):
    pack = market.sign(*ALL_ON)
    assert step(market.world, pack, "retire").status_code == 200
    assert market.code(market.book()) == "JURISDICTION_INACTIVE"


# ------------------------------------------------------------------ what Zoiko staff see
def test_the_platform_page_says_what_each_country_has_and_for_how_many_customers(market):
    market.sign("provider_directory", "booking")
    page = market.page()
    assert page["enforced"] is True
    assert page["countries"] == [{"country": "GB", "on": ["booking", "provider_directory"],
                                  "customers": 1}]


def test_the_platform_page_is_honest_when_nothing_is_enforced(settings):
    quiet = Market(settings, enforced=False, country="")
    page = quiet.page()
    assert (page["enforced"], page["countries"]) == (False, [])
    assert quiet.book().status_code == 201


def test_the_old_cell_wide_pages_and_names_are_gone(market):
    r = market.world.client.get(API + "platform/signed-packs",
                                **market.world.header(market.world.tenant_public_id, "sam"))
    assert r.status_code == 404
    from api.support import require_feature
    with pytest.raises(ValueError):
        require_feature("booking.request")
