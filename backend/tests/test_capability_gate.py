"""Country-pack capabilities on every customer endpoint, from one table (DEV-383): what the
customer's market has not switched on is refused (enforce), only logged (report) or ignored (off);
Zoiko's own console is never gated; every route is either gated or listed as not, so a new endpoint
cannot slip past; and production must enforce."""
import json
import logging

import pytest
from django.urls import URLPattern, URLResolver

from api import capability_gate as gate
from api.urls import urlpatterns
from foundation.release_check import release_problems
from tests.test_clinician import Practice
from tests.test_country_deadline import set_country
from tests.test_packs import full, signed
from tests.test_platform import Platform
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_PACK_GATING = "enforce"


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12")):
        w.staff(subject, role)
    return w


@pytest.fixture
def p():
    return Practice()


def pack(world, *capabilities):
    manifest = full()
    manifest["capabilities"] = {name: True for name in capabilities}
    return signed(world, manifest=manifest)


def code_of(r):
    return json.loads(r.content)


# ------------------------------------------------------------------ the table covers everything
def _routes(patterns, prefix=""):
    for item in patterns:
        if isinstance(item, URLResolver):
            yield from _routes(item.url_patterns, prefix + str(item.pattern))
        elif isinstance(item, URLPattern):
            yield prefix + str(item.pattern)


def test_every_api_route_is_either_gated_or_listed_with_a_reason():
    routes = set(_routes(urlpatterns))
    customer = {r for r in routes if not r.startswith(gate.PLATFORM_PREFIX)}
    unknown = sorted(customer - set(gate.GATED) - set(gate.UNGATED))
    assert unknown == [], f"add these to api/capability_gate.py (GATED or UNGATED): {unknown}"
    assert not set(gate.GATED) & set(gate.UNGATED)
    stale = sorted((set(gate.GATED) | set(gate.UNGATED)) - routes)
    assert stale == [], f"these are no longer routes: {stale}"


def test_only_known_capabilities_are_used():
    from modules.jurisdiction import api as jurisdiction
    assert set(gate.GATED.values()) <= set(jurisdiction.CAPABILITIES)


# ------------------------------------------------------------------ enforce
def test_without_a_country_or_pack_the_gated_features_are_refused(p):
    r = p.get("bookings", subject="pat")
    assert r.status_code == 403 and code_of(r)["code"] == "JURISDICTION_INACTIVE"
    assert code_of(r)["fields"] == ["booking"]
    assert code_of(p.get("messages/threads", subject="pat"))["fields"] == ["messaging"]
    assert code_of(p.get("staff/waitlist", subject="ops"))["fields"] == ["waiting_list"]
    assert code_of(p.get("services", subject="pat"))["fields"] == ["provider_directory"]


def test_what_is_not_gated_keeps_working(p):
    assert p.get("me", subject="pat").status_code == 200
    assert p.get("rights", subject="pat").status_code == 200        # rights never switched off


def test_a_signed_pack_switches_on_exactly_what_it_names(world, p):
    set_country(world, p, "GB")
    pack(world, "booking", "provider_directory")
    assert p.get("bookings", subject="pat").status_code == 200
    assert p.get("services", subject="pat").status_code == 200
    r = p.get("messages/threads", subject="pat")                   # not in the pack
    assert r.status_code == 403 and code_of(r)["fields"] == ["messaging"]


def test_a_pack_for_another_country_counts_for_nothing(world, p):
    set_country(world, p, "IE")
    pack(world, "booking")            # signed() makes a GB pack
    assert p.get("bookings", subject="pat").status_code == 403


def test_writing_is_refused_too_not_only_reading(p):
    r = p.post("bookings", {"slot": "nothing"}, subject="pat")
    assert r.status_code == 403 and code_of(r)["fields"] == ["booking"]


def test_zoikos_own_console_is_never_gated(world):
    r = world.client.get("/api/v1/platform/hazards", **world.header(world.tenant_public_id, "iyer"))
    assert r.status_code == 200


def test_an_unknown_mode_fails_closed(p, settings):
    settings.ZOIKO_PACK_GATING = "maybe"
    assert p.get("bookings", subject="pat").status_code == 403


# ------------------------------------------------------------------ report and off
def test_report_allows_and_logs_the_route_and_capability_only(p, settings, caplog):
    settings.ZOIKO_PACK_GATING = "report"
    with caplog.at_level(logging.WARNING, logger="zoiko.pack_gating"):
        assert p.get("bookings", subject="pat").status_code == 200
    line = next(r.getMessage() for r in caplog.records if r.name == "zoiko.pack_gating")
    assert line == "pack_gating would_refuse capability=booking route=bookings"


def test_off_does_nothing(p, settings):
    settings.ZOIKO_PACK_GATING = "off"
    assert p.get("bookings", subject="pat").status_code == 200


# ------------------------------------------------------------------ production
@pytest.mark.parametrize("value", ["report", "off", "", "Enforce "])
def test_a_real_server_must_enforce(value):
    problems = release_problems(good(ZOIKO_PACK_GATING=value))
    if value.strip().lower() == "enforce":
        assert problems == []
    else:
        assert any("ZOIKO_PACK_GATING" in p for p in problems)


# ------------------------------------------------------------------ what the apps are told
def test_the_apps_are_told_what_is_switched_off_so_they_can_hide_it(world, p, settings):
    def off():
        return code_of(p.get("me", subject="pat"))["switched_off"]

    assert off() == ["booking", "messaging", "provider_directory", "referrals", "waiting_list"]
    set_country(world, p, "GB")
    pack(world, "booking", "messaging")
    assert off() == ["provider_directory", "referrals", "waiting_list"]
    settings.ZOIKO_PACK_GATING = "report"
    assert off() == []                                  # nothing is refused, so nothing hidden
