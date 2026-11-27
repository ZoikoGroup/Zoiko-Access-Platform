"""Outbound signed webhooks (GHE-13; DEV-127): registered and verified by a clinic administrator,
sent signed and reference-only, retried with backoff, dead-lettered, redriven by a person."""
import base64
import datetime as dt
import io
import json

import pytest
from django.core.management import call_command

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import emit, relay_once
from foundation.timeutil import FixedClock
from modules.evidence.models import AuditEvent
from modules.integration import api as integration
from modules.integration import services
from modules.integration.delivery import http_post as real_post
from modules.integration.delivery import sign
from modules.integration.models import (
    DeliveryState,
    EndpointState,
    WebhookDelivery,
    WebhookEndpoint,
)
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
URL = "https://partner.example.org/hooks/zoiko"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    services.register_subscribers()


class Receiver:
    """A stand-in for the partner's server. Answers the challenge and records every delivery."""

    def __init__(self, status=200):
        self.status = status
        self.calls = []
        self.echo = True

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        payload = json.loads(body)
        if payload.get("type") == "webhook.ping.v1":
            return self.status, json.dumps(
                {"challenge": payload["challenge"] if self.echo else "wrong"}).encode()
        return self.status, b""


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


@pytest.fixture
def receiver(monkeypatch):
    fake = Receiver()
    monkeypatch.setattr(services, "http_post", fake)
    return fake


def add(p, topics=("booking.",), url=URL, label="Partner system", subject="boss"):
    return p.post("staff/integrations/webhooks",
                  {"label": label, "url": url, "topics": list(topics)}, subject=subject)


def made(p, **kw):
    r = add(p, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["endpoint"]


def active(p, **kw):
    ep = made(p, **kw)
    r = p.post(f"staff/integrations/webhooks/{ep['public_id']}/verify", subject="boss")
    assert r.status_code == 200, r.content
    return ep


def listing(p, subject="boss"):
    return json.loads(p.get("staff/integrations/webhooks", subject=subject).content)["endpoints"]


def deliveries(p, state=""):
    path = "staff/integrations/deliveries" + (f"?state={state}" if state else "")
    return json.loads(p.get(path, subject="boss").content)["deliveries"]


def happen(p, kind="booking.test.v1", ref="abc123"):
    """An event occurs in the clinic and the relay hands it on."""
    with tenant_transaction(p.clinic.ctx):
        emit(event_type=kind, aggregate_type="booking", aggregate_ref=ref, payload={"state": "x"})
    relay_once(dispatch.dispatch)


def send(p, receiver, at=None, limit=50):
    with tenant_transaction(p.clinic.ctx):
        clock = FixedClock(at) if at else None
        return integration.deliver_due(transport=receiver, limit=limit, clock=clock)


def signed_with(secret, headers, body):
    """Is the Zoiko-Signature header the right signature of this delivery for this secret?"""
    stamp = int(headers["Zoiko-Signature"].split(",")[0].split("=")[1])
    return f"v1={sign(secret, stamp, headers['Zoiko-Delivery-Id'], body)}" in headers[
        "Zoiko-Signature"]


def later(minutes):
    return dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)


# ------------------------------------------------------------------ registering
def test_an_administrator_registers_an_address_and_sees_the_secret_only_once(p):
    ep = made(p)
    assert ep["state"] == "pending_verification" and ep["secret"].startswith("whsec_")
    assert ep["key_id"].startswith("whk_")
    shown = listing(p)
    assert [e["public_id"] for e in shown] == [ep["public_id"]]
    assert "secret" not in shown[0] and ep["secret"] not in json.dumps(shown)


def test_the_secret_is_stored_sealed(p):
    ep = made(p)
    with tenant_transaction(p.clinic.ctx):
        row = WebhookEndpoint.objects.get(public_id=ep["public_id"])
    assert row.secret_enc.startswith("zf1:") and ep["secret"] not in row.secret_enc


def test_only_a_clinic_administrator_can_use_it(p):
    for who in ("ops", "doc", "pat"):
        assert add(p, subject=who).status_code == 403
        assert p.get("staff/integrations/webhooks", subject=who).status_code == 403
        assert p.get("staff/integrations/deliveries", subject=who).status_code == 403
    assert listing(p) == []


@pytest.mark.parametrize("url", [
    "http://partner.example.org/h", "ftp://partner.example.org/h", "https://u:pw@partner.example.org/h",
    "https://partner.example.org:8443/h", "not a url", "https:///nohost",
])
def test_only_plain_public_https_addresses_are_accepted(p, url):
    assert add(p, url=url).status_code == 422


@pytest.mark.parametrize("topics", [[], ["access."], ["booking"], ["Booking."], ["x" * 70],
                                    [f"booking.t{i}." for i in range(11)]])
def test_only_known_kinds_of_event_can_be_chosen(p, topics):
    assert add(p, topics=topics).status_code == 422


def test_at_most_ten_addresses_are_live_at_once(p):
    for i in range(10):
        assert add(p, label=f"System {i}").status_code == 201
    assert add(p, label="One too many").status_code == 429


# ------------------------------------------------------------------ verifying
def test_an_address_becomes_active_only_after_it_answers_the_signed_challenge(p, receiver):
    ep = made(p)
    r = p.post(f"staff/integrations/webhooks/{ep['public_id']}/verify", subject="boss")
    assert r.status_code == 200 and json.loads(r.content)["endpoint"]["state"] == "active"
    url, headers, body = receiver.calls[0]
    assert signed_with(ep["secret"], headers, body)


def test_a_wrong_answer_leaves_it_unverified(p, receiver):
    receiver.echo = False
    ep = made(p)
    r = p.post(f"staff/integrations/webhooks/{ep['public_id']}/verify", subject="boss")
    assert r.status_code == 422
    assert listing(p)[0]["state"] == "pending_verification"


def test_an_address_that_errors_is_not_verified(p, receiver):
    receiver.status = 500
    ep = made(p)
    assert p.post(f"staff/integrations/webhooks/{ep['public_id']}/verify",
                  subject="boss").status_code == 422


def test_an_address_on_a_private_network_cannot_be_verified(p):
    ep = made(p, url="https://127.0.0.1/hook")
    r = p.post(f"staff/integrations/webhooks/{ep['public_id']}/verify", subject="boss")
    assert r.status_code == 422 and listing(p)[0]["state"] == "pending_verification"


# ------------------------------------------------------------------ sending
def test_an_event_reaches_only_active_endpoints_that_asked_for_it(p, receiver):
    mine = active(p, topics=["booking."])
    other = active(p, topics=["message."], label="Messages only")
    waiting = made(p, topics=["booking."], label="Not verified")
    happen(p)
    rows = deliveries(p)
    assert [r["endpoint"] for r in rows] == [mine["public_id"]]
    shown = json.dumps(rows)
    assert other["public_id"] not in shown and waiting["public_id"] not in shown


def test_a_delivery_is_signed_and_carries_only_references(p, receiver):
    ep = active(p)
    happen(p, ref="ref123")
    receiver.calls.clear()
    assert send(p, receiver)["delivered"] == 1
    _, headers, body = receiver.calls[0]
    data = json.loads(body)
    assert set(data) == {"id", "type", "occurred_at", "aggregate", "data"}
    assert data["aggregate"] == {"type": "booking", "ref": "ref123"}
    assert data["data"] == {"state": "x"}
    assert signed_with(ep["secret"], headers, body)
    assert f"kid={ep['key_id']}" in headers["Zoiko-Signature"]
    assert headers["Zoiko-Delivery-Id"] == deliveries(p)[0]["delivery_id"]
    assert deliveries(p)[0]["state"] == "delivered"


def test_the_same_event_delivered_twice_makes_one_delivery(p, receiver):
    active(p)
    with tenant_transaction(p.clinic.ctx):
        event = emit(event_type="booking.test.v1", aggregate_type="booking", aggregate_ref="r1",
                     payload={"state": "x"})
    relay_once(dispatch.dispatch)
    from foundation.outbox import PublishedEvent
    again = PublishedEvent(
        event_id=event.event_id, event_type=event.event_type, schema_version=1,
        aggregate_type="booking", aggregate_ref="r1", ordering_key=event.ordering_key,
        correlation_id="-", causation_id="", occurred_at=event.occurred_at,
        cell_id=event.cell_id, tenant_id=event.tenant_id, payload={"state": "x"})
    with tenant_transaction(p.clinic.ctx):
        services.enqueue(again)
    assert len(deliveries(p)) == 1


def test_a_server_error_is_retried_later_with_the_same_delivery_id(p, receiver):
    active(p)
    happen(p)
    receiver.status = 503
    assert send(p, receiver)["retrying"] == 1
    first = deliveries(p)[0]
    assert first["state"] == "retrying" and first["attempts"] == 1 and first["last_status"] == 503
    assert send(p, receiver) == {"delivered": 0, "retrying": 0, "dead_letter": 0}   # not due yet
    receiver.status = 200
    assert send(p, receiver, at=later(120))["delivered"] == 1
    done = deliveries(p)[0]
    assert done["state"] == "delivered" and done["attempts"] == 2
    assert done["delivery_id"] == first["delivery_id"]


def test_a_clear_refusal_is_not_retried_but_too_many_requests_is(p, receiver):
    active(p)
    happen(p, ref="a")
    receiver.status = 400
    assert send(p, receiver)["dead_letter"] == 1
    happen(p, ref="b")
    receiver.status = 429
    assert send(p, receiver)["retrying"] == 1


def test_giving_up_after_eight_tries_parks_it_and_a_person_can_redrive_it(p, receiver):
    active(p)
    happen(p)
    receiver.status = 500
    for i in range(8):
        send(p, receiver, at=later(200 * (i + 1)))
    parked = deliveries(p, "dead_letter")
    assert len(parked) == 1 and parked[0]["attempts"] == 8
    r = p.post(f"staff/integrations/deliveries/{parked[0]['public_id']}/redrive", subject="boss")
    assert r.status_code == 200 and json.loads(r.content)["delivery"]["state"] == "pending"
    receiver.status = 200
    assert send(p, receiver)["delivered"] == 1


def test_only_a_parked_delivery_can_be_redriven(p, receiver):
    active(p)
    happen(p)
    send(p, receiver)
    one = deliveries(p)[0]
    assert p.post(f"staff/integrations/deliveries/{one['public_id']}/redrive",
                  subject="boss").status_code == 409
    assert p.post(f"staff/integrations/deliveries/{one['public_id']}/redrive",
                  subject="ops").status_code == 403


def test_ten_parked_deliveries_in_a_row_pause_the_address(p, receiver):
    active(p)
    receiver.status = 400
    for i in range(10):
        happen(p, ref=f"r{i}")
        send(p, receiver)
    assert listing(p)[0]["state"] == "paused" and listing(p)[0]["failure_streak"] == 10


def test_an_address_on_a_private_network_is_never_sent_to(p, receiver):
    ep = active(p)
    with tenant_transaction(p.clinic.ctx):
        WebhookEndpoint.objects.filter(public_id=ep["public_id"]).update(
            url="https://127.0.0.1/hook")
    happen(p)
    with tenant_transaction(p.clinic.ctx):
        counts = integration.deliver_due(transport=real_post)   # no network is reached
    assert counts["dead_letter"] == 1
    assert deliveries(p)[0]["last_error"] == "unsafe_target"


# ------------------------------------------------------------------ pausing, revoking, rotating
def test_a_paused_address_receives_nothing_until_resumed(p, receiver):
    ep = active(p)
    step = f"staff/integrations/webhooks/{ep['public_id']}"
    assert p.post(f"{step}/pause", subject="boss").status_code == 200
    happen(p, ref="while-paused")
    assert deliveries(p) == []
    assert p.post(f"{step}/resume", subject="boss").status_code == 200
    happen(p, ref="after")
    assert len(deliveries(p)) == 1


def test_an_unverified_address_cannot_be_resumed_into_service(p):
    ep = made(p)
    r = p.post(f"staff/integrations/webhooks/{ep['public_id']}/resume", subject="boss")
    assert r.status_code == 409 and listing(p)[0]["state"] == "pending_verification"


def test_a_revoked_address_is_final(p, receiver):
    ep = active(p)
    step = f"staff/integrations/webhooks/{ep['public_id']}"
    assert p.post(f"{step}/revoke", subject="boss").status_code == 200
    for name in ("resume", "pause", "verify", "rotate-secret"):
        assert p.post(f"{step}/{name}", subject="boss").status_code == 409
    happen(p)
    assert deliveries(p) == []


def test_rotating_the_secret_makes_a_new_one_and_the_old_stops_working(p, receiver):
    ep = active(p)
    r = p.post(f"staff/integrations/webhooks/{ep['public_id']}/rotate-secret", subject="boss")
    new = json.loads(r.content)["endpoint"]
    assert new["secret"] != ep["secret"] and new["key_id"] != ep["key_id"]
    happen(p)
    receiver.calls.clear()
    send(p, receiver)
    _, headers, body = receiver.calls[0]
    assert signed_with(new["secret"], headers, body)
    assert not signed_with(ep["secret"], headers, body)


def test_the_sealed_secret_moves_to_a_new_key_with_the_rest(p, settings, receiver):
    ep = active(p)
    settings.FIELD_KEYS = f"{settings.FIELD_KEYS},t2:{base64.b64encode(b'C' * 32).decode()}"
    settings.FIELD_KEY_ID = "t2"
    call_command("rotate_field_keys", stdout=io.StringIO())
    with tenant_transaction(p.clinic.ctx):
        row = WebhookEndpoint.objects.get(public_id=ep["public_id"])
        assert row.secret_enc.startswith("zf1:t2:")
    happen(p)
    receiver.calls.clear()
    assert send(p, receiver)["delivered"] == 1
    _, headers, body = receiver.calls[0]
    assert signed_with(ep["secret"], headers, body)


# ------------------------------------------------------------------ separation and record
def test_another_customer_sees_none_of_it(p, receiver):
    active(p)
    happen(p)
    outsider = Web()
    outsider.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    got = json.loads(outsider.get("staff/integrations/webhooks", subject="admin2").content)
    assert got["endpoints"] == []
    assert json.loads(outsider.get("staff/integrations/deliveries",
                                   subject="admin2").content)["deliveries"] == []
    mine = deliveries(p)[0]["public_id"]
    assert outsider.post(f"staff/integrations/deliveries/{mine}/redrive",
                         subject="admin2").status_code == 404


def test_every_change_is_audited_without_the_address_or_secret(p, receiver):
    ep = active(p, url="https://partner.example.org/very-private-path")
    step = f"staff/integrations/webhooks/{ep['public_id']}"
    p.post(f"{step}/pause", subject="boss")
    p.post(f"{step}/rotate-secret", subject="boss")
    with tenant_transaction(p.clinic.ctx):
        found = list(AuditEvent.objects.filter(object_ref=ep["public_id"]).order_by("chain_seq"))
    assert [e.action for e in found] == [
        "integration.endpoint_created", "integration.verified", "integration.pause",
        "integration.secret_rotated"]
    dump = json.dumps([e.details for e in found])
    assert "very-private-path" not in dump and ep["secret"] not in dump


def test_the_state_names_are_the_documented_ones():
    assert set(EndpointState.values) == {"pending_verification", "active", "paused", "revoked"}
    assert set(DeliveryState.values) == {"pending", "retrying", "delivered", "dead_letter"}


def test_a_redirect_is_never_followed(settings):
    """The sender stops at a redirect and reports it, so a partner cannot bounce it to a private
    address (GHE-13 T-21)."""
    import http.server
    import threading

    from modules.integration.delivery import http_post

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings.WEBHOOK_ALLOW_PRIVATE_TARGETS = True          # only so the test can use localhost
    try:
        status, _ = http_post(f"http://127.0.0.1:{server.server_port}/hook", {}, b"{}", 3.0)
    finally:
        server.shutdown()
    assert status == 302 and hits == ["/hook"]


def test_the_dashboard_tells_an_administrator_how_many_deliveries_gave_up(p, receiver):
    active(p)
    receiver.status = 400
    happen(p, ref="gave-up-1")
    send(p, receiver)
    mine = json.loads(p.get("staff/dashboard", subject="boss").content)["attention"]
    assert mine["webhooks_gave_up"] == 1
    other = json.loads(p.get("staff/dashboard", subject="ops").content)["attention"]
    assert other["webhooks_gave_up"] == 0                    # a number for administrators only
    parked = deliveries(p, "dead_letter")[0]
    p.post(f"staff/integrations/deliveries/{parked['public_id']}/redrive", subject="boss")
    receiver.status = 200
    send(p, receiver)
    assert json.loads(p.get("staff/dashboard", subject="boss").content
                      )["attention"]["webhooks_gave_up"] == 0


# --------------------------------------------------------- what a delivery keeps (T-23)
def test_a_delivery_carries_its_owner_the_rules_and_a_fingerprint_of_the_message(p, receiver):
    import hashlib

    from modules.policy import api as policy
    ep = active(p)
    happen(p, ref="evidence-1")
    [row] = deliveries(p)
    with tenant_transaction(p.clinic.ctx):
        endpoint_row = WebhookEndpoint.objects.get(public_id=ep["public_id"])
        stored = WebhookDelivery.objects.get(public_id=row["public_id"])
    assert row["owner"] == endpoint_row.created_by_ref and row["owner_name"]
    assert row["policy_digest"] == policy.POLICY_DIGEST and len(row["policy_digest"]) == 64
    assert row["body_digest"] == hashlib.sha256(stored.body.encode()).hexdigest()


def test_a_delivery_that_gave_up_keeps_all_three_and_they_survive_sending_it_again(p, receiver):
    active(p)
    receiver.status = 400
    happen(p, ref="evidence-2")
    before = deliveries(p)[0]
    send(p, receiver)
    parked = deliveries(p, "dead_letter")[0]
    for key in ("owner", "policy_digest", "body_digest"):
        assert parked[key] == before[key] and parked[key]
    p.post(f"staff/integrations/deliveries/{parked['public_id']}/redrive", subject="boss")
    receiver.status = 200
    send(p, receiver)
    after = deliveries(p, "delivered")[0]
    assert (after["owner"], after["policy_digest"], after["body_digest"]) == (
        parked["owner"], parked["policy_digest"], parked["body_digest"])


# ------------------------------------------------- one failing partner does not stop another (C-02)
def test_one_failing_address_does_not_hold_up_another_and_its_backlog_stays_visible(p, receiver):
    good, bad = Receiver(200), Receiver(503)

    def route(url, headers, body, timeout):
        return (bad if "bad" in url else good)(url, headers, body, timeout)

    ok = active(p, url="https://good.example.org/hooks", label="Healthy system")
    sick = active(p, url="https://bad.example.org/hooks", label="Failing system")
    happen(p, ref="independent-1")
    with tenant_transaction(p.clinic.ctx):
        integration.deliver_due(transport=route)
    rows = {d["endpoint"]: d["state"] for d in deliveries(p)}
    assert rows[ok["public_id"]] == "delivered" and rows[sick["public_id"]] == "retrying"
