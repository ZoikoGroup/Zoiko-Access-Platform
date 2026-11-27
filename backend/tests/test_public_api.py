"""The public partner API (DEV-393): an active, checked API key with scopes; its own per-minute
limit with headers; sandbox keys see only synthetic data; reads are audited; and nothing opens a
person's record. Mostly refusals."""
import json
import logging
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.partner_support import issue
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
BASE = "/api/public/v1/"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_RATE_LIMITING = "enforce"
    settings.ZOIKO_PACK_GATING = "report"


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def key(p, scopes=("catalog:read",), environment="live", activate=True):
    r = p.post("staff/integrations/partners", {
        "label": "Booking partner", "allowed_types": ["partner.lab."],
        "scopes": list(scopes), "environment": environment}, subject="boss")
    assert r.status_code == 201, r.content
    made = json.loads(r.content)["client"]
    if not activate:
        return made
    return issue(p, made)


def get(path, made=None, token=None):
    header = token if token is not None else (
        f"Bearer {made['key_id']}:{made['secret']}" if made else "")
    extra: dict[str, Any] = {"HTTP_AUTHORIZATION": header} if header else {}
    return Client().get(BASE + path, **extra)


# ------------------------------------------------------------------ who may call
def test_an_active_key_says_who_it_is(p):
    made = key(p, scopes=("catalog:read", "events:send"))
    r = get("ping", made)
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    body = json.loads(r.content)
    assert body["key"] == made["public_id"] and body["environment"] == "live"
    assert body["scopes"] == ["catalog:read", "events:send"]
    assert body["customer"]["id"] == p.clinic.tenant_public_id


@pytest.mark.parametrize("token", ["", "Bearer", "Bearer nothing", "Basic abc",
                                   "Bearer pc.unknown12.deadbeef:secret"])
def test_no_key_or_a_malformed_one_is_refused_alike(p, token):
    assert get("ping", token=token or None).status_code == 401


def test_a_wrong_secret_or_a_key_not_yet_checked_is_refused(p):
    made = key(p)
    assert get("ping", token=f"Bearer {made['key_id']}:wrong-secret").status_code == 401
    pending = key(p, activate=False)                  # Zoiko's checks not done, no secret yet
    assert get("ping", token=f"Bearer {pending['key_id']}:anything").status_code == 401


def test_a_suspended_key_stops_at_once(p):
    made = key(p)
    r = p.post(f"staff/integrations/partners/{made['public_id']}/suspend", {}, subject="boss")
    assert r.status_code == 200, r.content
    assert get("ping", made).status_code == 401


# ------------------------------------------------------------------ scopes and data
def test_the_catalogue_needs_its_scope(p):
    made = key(p, scopes=("events:send",))
    r = get("services", made)
    assert r.status_code == 403 and json.loads(r.content)["fields"] == ["scope"]


def test_a_live_key_sees_published_services_only_and_no_people(p):
    made = key(p)
    r = get("services", made)
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["environment"] == "live"
    for s in body["services"]:
        assert set(s) == {"id", "name", "category", "duration_minutes", "mode"}
    assert p.person not in r.content.decode()


def test_a_sandbox_key_sees_only_synthetic_services(p):
    made = key(p, environment="sandbox")
    body = json.loads(get("services", made).content)
    assert body["environment"] == "sandbox"
    assert all(s["id"].startswith("sandbox-") for s in body["services"])


def test_reads_are_audited_on_the_customers_record(p):
    made = key(p)
    get("services", made)
    with tenant_transaction(p.clinic.ctx):
        rows = AuditEvent.objects.filter(action="public_api.services_read",
                                         tenant_id=p.clinic.ctx.tenant_id)
        assert rows.filter(actor_ref=made["public_id"]).count() == 1


def test_the_directory_must_be_switched_on_for_the_customers_country(p, settings):
    settings.ZOIKO_PACK_GATING = "enforce"
    made = key(p)
    r = get("services", made)
    assert r.status_code == 403 and json.loads(r.content)["code"] == "JURISDICTION_INACTIVE"


# ------------------------------------------------------------------ the key's own limit
def test_each_key_has_its_own_limit_with_headers(p):
    made = key(p)
    from modules.integration.models import PartnerClient
    with tenant_transaction(p.clinic.ctx):
        PartnerClient.objects.filter(public_id=made["public_id"]).update(rate_per_minute=2)
    first = get("ping", made)
    assert first["X-RateLimit-Limit"] == "2" and first["X-RateLimit-Remaining"] == "1"
    assert get("ping", made).status_code == 200
    third = get("ping", made)
    assert third.status_code == 429 and 1 <= int(third["Retry-After"]) <= 60
    assert third["X-RateLimit-Remaining"] == "0"
    other = key(p)
    assert get("ping", other).status_code == 200               # another key is unaffected


def test_report_mode_allows_and_logs_only_the_kind(p, settings, caplog):
    settings.ZOIKO_RATE_LIMITING = "report"
    made = key(p)
    from modules.integration.models import PartnerClient
    with tenant_transaction(p.clinic.ctx):
        PartnerClient.objects.filter(public_id=made["public_id"]).update(rate_per_minute=1)
    with caplog.at_level(logging.WARNING, logger="zoiko.ratelimit"):
        for _ in range(3):
            assert get("ping", made).status_code == 200
    lines = {r.getMessage() for r in caplog.records if r.name == "zoiko.ratelimit"}
    assert lines == {"rate_limit would_refuse kind=api_key"}


# ------------------------------------------------------------------ keys and the contract
def test_a_read_only_key_cannot_send_events(p):
    import time

    from modules.integration.delivery import signature_header
    made = key(p, scopes=("catalog:read",))
    raw = json.dumps({"type": "partner.lab.result_ready.v1", "data": {"order": "o-1"}}).encode()
    stamp = int(time.time())
    r = Client().post("/api/v1/partner-events", raw, content_type="application/json",
                      HTTP_ZOIKO_SIGNATURE=signature_header(
                          made["secret"], made["key_id"], stamp, "delivery-0001", raw),
                      HTTP_ZOIKO_DELIVERY_ID="delivery-0001")
    assert r.status_code == 403


@pytest.mark.parametrize("bad", [{"scopes": ["everything"]}, {"scopes": []},
                                 {"environment": "staging"}])
def test_unknown_scopes_or_environments_are_refused(p, bad):
    body = {"label": "Partner", "allowed_types": ["partner.lab."], **bad}
    assert p.post("staff/integrations/partners", body, subject="boss").status_code == 422


def test_older_keys_keep_sending_events():
    from modules.integration.inbound import scopes_of
    from modules.integration.models import PartnerClient
    assert scopes_of(PartnerClient(scopes=[])) == ["events:send"]


def test_the_contract_is_published_and_only_get_is_allowed(p):
    r = Client().get(BASE + "openapi.json")
    assert r.status_code == 200 and json.loads(r.content)["info"]["title"] == \
        "Zoiko Access Public API"
    made = key(p)
    r = Client().post(BASE + "ping", HTTP_AUTHORIZATION=f"Bearer {made['key_id']}:{made['secret']}")
    assert r.status_code == 405
