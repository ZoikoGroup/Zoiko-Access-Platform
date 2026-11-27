"""A partner's interface is certified before any clinic may use it: sandbox, certified, live, by
three different people, per profile version (GHE-13 T-47, T-48; DEV-230)."""
import json

import pytest

from modules.connector import adapters
from modules.connector.simulator import simulator
from tests.test_bookingflow import Flow
from tests.test_emergency_number import set_country
from tests.test_packs import signed
from tests.test_partner_pack_gate import RealPartner, choose, with_partner_booking
from tests.test_platform import API, Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def partner():
    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    real = RealPartner()
    adapters.register(real)
    yield real
    adapters.clear()


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12"), ("noor", "R12")):
        w.staff(subject, role)
    return w


def step(world, name, who, key="acme-booking", **body):
    return world.client.post(API + f"platform/connectors/{key}/{name}", json.dumps(body),
                             content_type="application/json",
                             **world.header(world.tenant_public_id, who))


def overview(world, who="sam"):
    r = world.client.get(API + "platform/connectors", **world.header(world.tenant_public_id, who))
    assert r.status_code == 200, r.content
    return {c["connector_key"]: c for c in json.loads(r.content)["connectors"]}


def take_live(world):
    assert step(world, "enrol", "sam").status_code == 201
    assert step(world, "certify", "iyer").status_code == 200
    assert step(world, "live", "tess").status_code == 200


def test_three_different_people_take_a_partner_from_sandbox_to_live(world, partner):
    assert overview(world)["acme-booking"]["certification"] is None
    assert step(world, "enrol", "sam").status_code == 201
    assert step(world, "live", "tess").status_code == 409          # not certified yet
    assert step(world, "certify", "sam").status_code == 403        # support cannot certify
    assert step(world, "certify", "iyer").status_code == 200
    assert step(world, "live", "sam").status_code == 403           # enrolled it
    assert step(world, "live", "iyer").status_code == 403          # safety does not put live
    assert step(world, "live", "tess").status_code == 200
    cert = overview(world)["acme-booking"]["certification"]
    assert cert["stage"] == "live" and len({cert["enrolled_by"], cert["certified_by"],
                                             cert["live_by"]}) == 3


def test_the_pretend_partner_needs_and_takes_no_certificate(world, partner):
    assert overview(world)["simulator"]["simulated"] is True
    assert step(world, "enrol", "sam", key="simulator").status_code == 422


def test_a_clinic_cannot_use_a_partner_that_is_not_live_even_with_a_pack(world, partner):
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    signed(world, manifest=with_partner_booking())
    assert choose(flow, "acme-booking").status_code == 403
    take_live(world)
    assert choose(flow, "acme-booking").status_code == 200


def test_a_new_interface_version_needs_certifying_again(world, partner):
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    signed(world, manifest=with_partner_booking())
    take_live(world)
    assert choose(flow, "acme-booking").status_code == 200
    partner.profile_version = "2"                                   # the partner changed
    assert overview(world)["acme-booking"]["certification"] is None
    shown = json.loads(flow.web.get("staff/booking-system", subject="desk").content)
    assert shown["partner_decides"] is False                       # staff confirm by hand
    assert step(world, "enrol", "sam").status_code == 201          # the new version starts over


def test_suspending_a_live_partner_needs_a_reason_and_stops_its_use(world, partner):
    flow = Flow(system="")
    set_country(world, flow.web, "GB")
    signed(world, manifest=with_partner_booking())
    take_live(world)
    assert choose(flow, "acme-booking").status_code == 200
    assert step(world, "suspend", "noor", note="").status_code == 422
    assert step(world, "suspend", "noor", note="failed a security retest").status_code == 200
    shown = json.loads(flow.web.get("staff/booking-system", subject="desk").content)
    assert shown["partner_decides"] is False


def test_only_zoikos_own_staff_see_or_change_certifications(world, partner):
    flow = Flow(system="")
    assert flow.web.get("platform/connectors", subject="boss").status_code == 403
