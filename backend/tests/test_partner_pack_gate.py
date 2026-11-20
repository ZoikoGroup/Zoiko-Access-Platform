"""A real partner's booking system can be switched on only while a signed country pack for the
customer's country allows partner bookings (GHE-13 T-46, DEV-224). The pretend partner, which only
ever sees synthetic data in development, is exempt."""
import json

import pytest

from modules.connector import adapters
from modules.connector.adapters import Outcome, PartnerResult
from modules.connector.simulator import simulator
from tests.test_bookingflow import Flow
from tests.test_emergency_number import set_country
from tests.test_packs import full, signed, step
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class RealPartner:
    key, idempotent, simulated = "acme-booking", True, False

    def submit(self, operation):
        return PartnerResult(Outcome.ACCEPTED)

    def query(self, operation):
        return PartnerResult(Outcome.CONFIRMED)


@pytest.fixture(autouse=True)
def partners():
    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    adapters.register(RealPartner())
    yield
    adapters.clear()


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12"), ("noor", "R12")):
        w.staff(subject, role)
    return w


def certify_live(world, key="acme-booking"):
    """DEV-230: a real partner must be certified and live before a clinic may use it."""
    from tests.test_platform import API

    for name, who in (("enrol", "sam"), ("certify", "iyer"), ("live", "tess")):
        r = world.client.post(API + f"platform/connectors/{key}/{name}", "{}",
                              content_type="application/json",
                              **world.header(world.tenant_public_id, who))
        assert r.status_code in (200, 201), r.content


def choose(flow, system):
    return flow.web.post("staff/booking-system", {"system": system}, subject="boss")


def code(r):
    return json.loads(r.content).get("code")


def with_partner_booking(on=True):
    manifest = full()
    manifest["capabilities"] = {"provider_directory": True, "booking": True,
                                "partner_booking": on}
    return manifest


def test_without_a_country_or_a_pack_a_real_partner_cannot_be_switched_on(world):
    flow = Flow(system="")
    r = choose(flow, "acme-booking")
    assert r.status_code == 403 and code(r) == "JURISDICTION_INACTIVE", r.content
    set_country(world, flow.web, "GB")
    assert code(choose(flow, "acme-booking")) == "JURISDICTION_INACTIVE"


def test_a_signed_pack_that_does_not_allow_partner_booking_is_not_enough(world):
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    signed(world, manifest=with_partner_booking(on=False))
    assert code(choose(flow, "acme-booking")) == "JURISDICTION_INACTIVE"


def test_a_signed_pack_that_allows_it_lets_the_administrator_switch_it_on(world):
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    signed(world, manifest=with_partner_booking())
    certify_live(world)
    r = choose(flow, "acme-booking")
    assert r.status_code == 200, r.content
    assert json.loads(r.content)["system"] == "acme-booking"
    assert choose(flow, "").status_code == 200                   # going back is always allowed


def test_a_suspended_pack_no_longer_allows_it(world):
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    pack = signed(world, manifest=with_partner_booking())
    assert step(world, pack, "suspend", "iyer", note="legal review reopened").status_code == 200
    assert code(choose(flow, "acme-booking")) == "JURISDICTION_INACTIVE"


def test_the_pretend_partner_needs_no_pack_because_it_only_sees_synthetic_data():
    flow = Flow(system="")
    assert choose(flow, "simulator").status_code == 200


def test_when_the_pack_lapses_the_partner_is_no_longer_asked_and_staff_confirm_by_hand(world):
    """DEV-229: switching on is not enough; the market must still allow it when it is used."""
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    pack = signed(world, manifest=with_partner_booking())
    certify_live(world)
    assert choose(flow, "acme-booking").status_code == 200
    first, second = flow.request(0), flow.request(1)
    assert flow.confirm_by_hand(first).status_code == 409             # the partner decides
    shown = json.loads(flow.web.get("staff/booking-system", subject="desk").content)
    assert shown["partner_decides"] is True
    assert step(world, pack, "suspend", "iyer", note="contract under review").status_code == 200
    r = flow.ask(second)
    assert r.status_code == 403 and code(r) == "JURISDICTION_INACTIVE"
    assert flow.confirm_by_hand(first).status_code == 200              # the alternate path
    shown = json.loads(flow.web.get("staff/booking-system", subject="desk").content)
    assert shown["partner_decides"] is False
