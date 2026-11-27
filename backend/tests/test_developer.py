import datetime as dt
import hashlib
import http.server
import json
import socket
import threading
import uuid

import pytest
from django.test import Client

from foundation import crypto, dispatch
from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent
from foundation.timeutil import UTC, FixedClock
from modules.developer import api as developer
from modules.developer import services as dev_services
from modules.developer.models import (
    ApiClient,
    WebhookDelivery,
    WebhookSubscription,
)
from modules.developer.signing import sign_body
from modules.trust.models import Tenant
from tests.conftest import as_app_role, publish_service
from tests.test_api import Web
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
ALL = ("catalog:read", "webhooks:manage", "events:read")
PUBLIC_URL = "https://8.8.8.8/hook"          # a public address, so no name lookup is needed
API = "/api/public/v1/"


def code_of(call, *args, **kwargs):
    with pytest.raises(ZoikoError) as caught:
        call(*args, **kwargs)
    return caught.value.code


class Dev:
    """One customer and one API client of it."""

    def __init__(self, scopes=ALL, environment="production", world=None, name="Acme"):
        self.world = world or World()
        self.ctx = self.world.ctx
        self.tenant = self.world.tenant_public_id
        client, self.key = developer.create_client(
            tenant_public_id=self.tenant, name=name, environment=environment,
            scopes=list(scopes), created_by_ref="admin1")
        self.client_ref = client["public_id"]

    def hook(self, prefixes=("booking.",), url=PUBLIC_URL):
        with tenant_transaction(self.ctx):
            return developer.create_webhook(
                client_ref=self.client_ref, url=url, event_prefixes=list(prefixes))


class Recorder:
    """A pretend receiver: remembers what it was sent and answers as scripted."""

    def __init__(self, *answers):
        self.answers = list(answers) or [200]
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def live_clock():
    """A fixed clock a minute from the real now: deliveries queued just now are due, and the test
    controls every later moment."""
    return FixedClock(dt.datetime.now(UTC) + dt.timedelta(minutes=1))


def event(ctx, event_type="booking.requested.v1", ref="BK1"):
    return PublishedEvent(
        event_id=uuid.uuid4(), event_type=event_type, schema_version=1,
        aggregate_type="booking", aggregate_ref=ref, ordering_key=f"booking:{ref}",
        correlation_id="corr-1", causation_id="", occurred_at=T0, cell_id=ctx.cell_id,
        tenant_id=ctx.tenant_id, payload={"booking_ref": ref})


def queue(dev, **kw):
    ev = event(dev.ctx, **kw)
    with tenant_transaction(dev.ctx):
        return ev, dev_services.fan_out(ev)


def deliver(dev, sender, clock=None):
    with bind_context(dev.ctx):
        return developer.deliver_due(sender=sender, clock=clock)


def deliveries(dev, hook):
    with tenant_transaction(dev.ctx):
        return developer.list_deliveries(dev.client_ref, hook["public_id"])


# ------------------------------------------------------------------ keys
def test_a_key_is_shown_once_and_only_its_hash_is_kept():
    dev = Dev()
    version, tenant, client, secret = dev.key.split(".")
    assert (version, tenant, client) == ("zk1", dev.tenant, dev.client_ref)
    with tenant_transaction(dev.ctx):
        row = ApiClient.objects.get()
        assert row.key_hash == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in json.dumps(
            {f.name: str(getattr(row, f.name)) for f in ApiClient._meta.fields})


def test_a_good_key_authenticates_and_records_its_use():
    dev = Dev(scopes=("catalog:read",), environment="sandbox")
    who = developer.authenticate_key(f"Bearer {dev.key}")
    assert (who.client_ref, who.tenant_ref, who.environment, who.scopes) == (
        dev.client_ref, dev.tenant, "sandbox", ("catalog:read",))
    with tenant_transaction(dev.ctx):
        assert ApiClient.objects.get().last_used_at is not None


def test_every_way_of_failing_to_authenticate_looks_the_same():
    dev = Dev()
    other = Dev()
    version, tenant, client, secret = dev.key.split(".")
    bad = [
        None, "", "Basic abc", "Bearer", "Bearer ", f"Bearer {dev.key}x",
        f"Bearer {version}.{tenant}.{client}.wrong", f"Bearer {version}.{tenant}.nope.{secret}",
        f"Bearer {version}.{other.tenant}.{client}.{secret}",       # another customer's tenant
        f"Bearer zk9.{tenant}.{client}.{secret}", f"Bearer {tenant}.{client}.{secret}",
        f"Bearer {version}.{tenant}.{client}.{secret}.extra",
    ]
    for header in bad:
        assert code_of(developer.authenticate_key, header) == ErrorCode.UNAUTHENTICATED, header


def test_a_revoked_expired_or_suspended_client_is_refused():
    revoked, expired, suspended = Dev(), Dev(), Dev()
    with tenant_transaction(revoked.ctx):
        developer.revoke_client(revoked.client_ref, actor_ref="admin1")
    with tenant_transaction(expired.ctx):
        ApiClient.objects.update(expires_at=dt.datetime(2020, 1, 1, tzinfo=UTC))
    Tenant.objects.filter(public_id=suspended.tenant).update(status="suspended")
    for dev in (revoked, expired, suspended):
        assert code_of(developer.authenticate_key, f"Bearer {dev.key}") == (
            ErrorCode.UNAUTHENTICATED)


def test_clients_are_created_with_valid_scopes_and_names():
    world = World()
    base = {"tenant_public_id": world.tenant_public_id, "name": "A", "environment": "sandbox",
            "scopes": ["catalog:read"], "created_by_ref": "admin1"}
    for change in ({"scopes": []}, {"scopes": ["root"]}, {"scopes": ["catalog:read"] * 2},
                   {"environment": "staging"}, {"name": " "}, {"name": "x" * 101},
                   {"created_by_ref": "no spaces"}):
        assert code_of(developer.create_client, **{**base, **change}) == (
            ErrorCode.VALIDATION_FAILED), change
    assert code_of(developer.create_client, **{**base, "tenant_public_id": "nope"}) == (
        ErrorCode.NOT_FOUND)


def test_a_scope_the_client_lacks_is_refused():
    who = developer.authenticate_key(f"Bearer {Dev(scopes=('catalog:read',)).key}")
    developer.require_scope(who, "catalog:read")
    assert code_of(developer.require_scope, who, "webhooks:manage") == ErrorCode.POLICY_DENIED


# ------------------------------------------------------------------ where webhooks may point
@pytest.mark.parametrize("url", [
    "http://8.8.8.8/hook", "https://127.0.0.1/hook", "https://10.1.2.3/hook",
    "https://192.168.0.9/hook", "https://172.16.0.1/hook", "https://169.254.169.254/latest",
    "https://[::1]/hook", "https://0.0.0.0/hook", "https://user:pw@8.8.8.8/hook",
    "ftp://8.8.8.8/hook", "not a url", "https:///path", "https://8.8.8.8:notaport/",
    "https://8.8.8.8/" + "a" * 500,
])
def test_a_destination_that_could_reach_our_own_network_is_refused(url):
    assert code_of(developer.check_destination, url) == ErrorCode.VALIDATION_FAILED


def test_a_public_address_is_accepted():
    developer.check_destination("https://8.8.8.8/hook")
    developer.check_destination("https://[2001:4860:4860::8888]/hook")


def fake_lookup(*addresses):
    def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]
    return lookup


def test_a_name_is_checked_by_what_it_resolves_to(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_lookup("93.184.216.34"))
    developer.check_destination("https://hooks.example.com/x")
    for resolves_to in (("127.0.0.1",), ("10.0.0.7",), ("93.184.216.34", "10.0.0.7")):
        monkeypatch.setattr(socket, "getaddrinfo", fake_lookup(*resolves_to))
        assert code_of(developer.check_destination, "https://hooks.example.com/x") == (
            ErrorCode.VALIDATION_FAILED), resolves_to

    def unresolvable(*args, **kwargs):
        raise OSError("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", unresolvable)
    assert code_of(developer.check_destination, "https://nowhere.example/x") == (
        ErrorCode.VALIDATION_FAILED)


def test_development_may_allow_plain_http_and_private_addresses(settings):
    settings.WEBHOOK_ALLOW_INSECURE = True
    developer.check_destination("http://8.8.8.8/hook")
    assert code_of(developer.check_destination, "http://127.0.0.1/hook") == (
        ErrorCode.VALIDATION_FAILED)
    settings.WEBHOOK_ALLOW_PRIVATE = True
    developer.check_destination("http://127.0.0.1:9/hook")
    assert code_of(developer.check_destination, "ftp://127.0.0.1/hook") == (
        ErrorCode.VALIDATION_FAILED)


# ------------------------------------------------------------------ signing
def test_a_signature_proves_the_body_and_the_time():
    body = b'{"event_id":"e1"}'
    signature = sign_body("whsec_secret", 1000, body)
    ok = {"timestamp": "1000", "body": body, "signature": signature, "now": 1100}
    assert developer.verify_signature("whsec_secret", **ok)
    assert not developer.verify_signature("whsec_other", **ok)
    assert not developer.verify_signature("whsec_secret", **{**ok, "body": b"{}"})
    assert not developer.verify_signature("whsec_secret", **{**ok, "now": 1000 + 301})   # replay
    assert not developer.verify_signature("whsec_secret", **{**ok, "now": 1000 - 301})   # future
    assert developer.verify_signature("whsec_secret", **{**ok, "now": 1300})
    assert not developer.verify_signature("whsec_secret", **{**ok, "timestamp": "soon"})
    assert not developer.verify_signature("whsec_secret", **{**ok, "signature": ""})
    assert not developer.verify_signature("whsec_secret", **{**ok, "timestamp": "1001"})


# ------------------------------------------------------------------ subscriptions
def test_a_webhook_secret_is_shown_once_and_stored_encrypted():
    dev = Dev()
    hook, secret = dev.hook()
    assert secret.startswith("whsec_") and "secret" not in hook
    with tenant_transaction(dev.ctx):
        stored = WebhookSubscription.objects.get().secret_token
        assert secret not in stored
        assert crypto.decrypt(stored, tenant_id=dev.ctx.tenant_id,
                              context=dev_services.SECRET_CONTEXT) == secret


def test_a_subscription_must_name_public_event_families_and_a_safe_address():
    dev = Dev()
    for prefixes in ([], ["admin."], ["Booking."], ["booking.", "ai."], ["booking."] * 11, [1]):
        assert code_of(dev.hook, prefixes=prefixes) == ErrorCode.VALIDATION_FAILED, prefixes
    assert code_of(dev.hook, url="https://127.0.0.1/x") == ErrorCode.VALIDATION_FAILED
    dev.hook(prefixes=("booking.", "referral.", "connector.", "workflow."))


def test_one_client_may_hold_only_a_limited_number_of_webhooks():
    dev = Dev()
    for _ in range(dev_services.MAX_WEBHOOKS_PER_CLIENT):
        dev.hook()
    assert code_of(dev.hook) == ErrorCode.RATE_LIMITED


def test_a_revoked_client_can_make_no_more_webhooks_and_its_deliveries_stop():
    dev = Dev()
    hook, _ = dev.hook()
    queue(dev)
    with tenant_transaction(dev.ctx):
        developer.revoke_client(dev.client_ref, actor_ref="admin1")
        developer.revoke_client(dev.client_ref, actor_ref="admin1")          # twice is harmless
    assert code_of(dev.hook) == ErrorCode.NOT_FOUND
    receiver = Recorder()
    assert deliver(dev, receiver) == 0 and receiver.calls == []              # nothing is sent
    assert deliveries(dev, hook)[0]["state"] == "pending"


def test_a_client_cannot_see_or_change_another_clients_webhooks():
    one = Dev()
    hook, _ = one.hook()
    with tenant_transaction(one.ctx):
        assert code_of(developer.set_webhook_status, "nobody", hook["public_id"], "paused") == (
            ErrorCode.NOT_FOUND)
        assert developer.list_webhooks("nobody") == []
        assert code_of(developer.send_test_event, "nobody", hook["public_id"]) == (
            ErrorCode.NOT_FOUND)
        assert developer.list_deliveries("nobody", hook["public_id"]) == []


def test_pausing_stops_deliveries_and_resuming_clears_the_failure_streak():
    dev = Dev()
    hook, _ = dev.hook()
    with tenant_transaction(dev.ctx):
        assert code_of(developer.set_webhook_status, dev.client_ref, hook["public_id"],
                       "disabled") == ErrorCode.VALIDATION_FAILED
        developer.set_webhook_status(dev.client_ref, hook["public_id"], "paused")
    assert queue(dev)[1] == 0                      # a paused hook is not even queued for
    with tenant_transaction(dev.ctx):
        WebhookSubscription.objects.update(dead_streak=4)
        back = developer.set_webhook_status(dev.client_ref, hook["public_id"], "active")
    assert (back["status"], back["dead_streak"]) == ("active", 0)


# ------------------------------------------------------------------ from event to delivery
def test_a_public_event_becomes_one_signed_delivery_of_references_only():
    dev = Dev()
    hook, secret = dev.hook()
    ev, queued = queue(dev)
    assert queued == 1
    receiver = Recorder(200)
    clock = live_clock()
    assert deliver(dev, receiver, clock) == 1
    (url, headers, body), = receiver.calls
    assert url == PUBLIC_URL
    sent = json.loads(body)
    assert set(sent) == {"event_id", "event_type", "schema_version", "occurred_at",
                         "aggregate_type", "aggregate_ref", "correlation_id", "cell_id"}
    assert (sent["event_id"], sent["event_type"]) == (str(ev.event_id), "booking.requested.v1")
    assert "booking_ref" not in sent                       # the event's own payload stays inside
    assert headers["Idempotency-Key"] == headers["X-Zoiko-Event-Id"] == str(ev.event_id)
    assert headers["X-Zoiko-Event-Type"] == "booking.requested.v1"
    assert developer.verify_signature(
        secret, timestamp=headers["X-Zoiko-Timestamp"], body=body,
        signature=headers["X-Zoiko-Signature"], now=int(clock.now().timestamp()))
    (done,) = deliveries(dev, hook)
    assert (done["state"], done["attempts"], done["last_status"]) == ("delivered", 1, 200)
    assert deliver(dev, receiver, clock) == 0                  # delivered once, never again


def test_an_event_arriving_twice_is_queued_once():
    dev = Dev()
    dev.hook()
    ev = event(dev.ctx)
    with tenant_transaction(dev.ctx):
        assert dev_services.fan_out(ev) == 1
        assert dev_services.fan_out(ev) == 0
        assert WebhookDelivery.objects.count() == 1


def test_only_public_families_matching_the_subscription_and_scope_are_passed_on():
    dev = Dev()
    dev.hook(prefixes=("referral.",))
    no_scope = Dev(scopes=("webhooks:manage",))                # may manage hooks, not hear events
    no_scope.hook()
    assert queue(dev, event_type="booking.requested.v1")[1] == 0        # not subscribed
    assert queue(dev, event_type="access.denied.v1")[1] == 0            # not a public family
    assert queue(dev, event_type="ai.permit.changed.v1")[1] == 0        # never public
    assert queue(dev, event_type="referral.issued.v1")[1] == 1
    assert queue(no_scope, event_type="booking.requested.v1")[1] == 0


def test_events_of_one_customer_never_reach_another_customers_webhooks():
    one, two = Dev(), Dev()
    one.hook()
    two.hook()
    assert queue(one)[1] == 1
    assert WebhookDelivery.objects.filter(tenant_id=two.ctx.tenant_id).count() == 0
    assert WebhookDelivery.objects.filter(tenant_id=one.ctx.tenant_id).count() == 1
    with tenant_transaction(two.ctx), as_app_role():
        assert WebhookDelivery.objects.count() == 0


def test_the_event_worker_routes_events_to_webhooks():
    dev = Dev()
    hook, _ = dev.hook()
    saved = dispatch.subscriptions()
    dispatch.clear_subscriptions()
    try:
        developer.register_subscribers()
        developer.register_subscribers()                       # safe to repeat
        consumers = [s.consumer for s in dispatch.subscriptions()]
        assert len(consumers) == len(set(consumers)) == len(developer.PUBLIC_EVENT_PREFIXES)
        ev = event(dev.ctx)
        dispatch.dispatch(ev)
        dispatch.dispatch(ev)                              # a duplicate changes nothing
        dispatch.dispatch(event(dev.ctx, event_type="access.denied.v1"))
    finally:
        dispatch.clear_subscriptions()
        for s in saved:
            dispatch.subscribe(s.consumer, s.prefix, s.handler)
    assert len(deliveries(dev, hook)) == 1


# ------------------------------------------------------------------ when delivery goes wrong
def test_a_failing_receiver_is_retried_with_a_growing_wait_then_given_up():
    dev = Dev()
    hook, _ = dev.hook()
    queue(dev)
    clock = live_clock()
    receiver = Recorder(500)
    assert deliver(dev, receiver, clock) == 1
    first = deliveries(dev, hook)[0]
    assert (first["state"], first["attempts"], first["last_status"]) == ("pending", 1, 500)
    assert deliver(dev, receiver, clock) == 0                  # not due yet
    for _ in range(dev_services.MAX_DELIVERY_ATTEMPTS - 1):
        clock.advance(dt.timedelta(hours=2))
        assert deliver(dev, receiver, clock) == 1
    last = deliveries(dev, hook)[0]
    assert (last["state"], last["attempts"]) == ("dead", dev_services.MAX_DELIVERY_ATTEMPTS)
    clock.advance(dt.timedelta(hours=2))
    assert deliver(dev, receiver, clock) == 0                  # given up: no more tries


def test_only_a_2xx_answer_counts_as_delivered():
    for status, delivered in ((200, True), (204, True), (299, True), (301, False),
                              (400, False), (404, False), (429, False), (503, False)):
        dev = Dev()
        hook, _ = dev.hook()
        queue(dev)
        deliver(dev, Recorder(status))
        assert (deliveries(dev, hook)[0]["state"] == "delivered") is delivered, status


def test_a_connection_failure_is_recorded_by_class_only():
    dev = Dev()
    hook, _ = dev.hook()
    queue(dev)
    deliver(dev, Recorder(ConnectionError("secret detail that must not be kept")))
    seen = deliveries(dev, hook)[0]
    assert (seen["state"], seen["last_error"], seen["last_status"]) == (
        "pending", "ConnectionError", None)


def test_repeated_dead_deliveries_switch_the_subscription_off_until_resumed():
    dev = Dev()
    hook, _ = dev.hook()
    for _ in range(dev_services.DISABLE_AFTER_DEAD):
        queue(dev)
    clock = live_clock()
    receiver = Recorder(500)
    for _ in range(dev_services.MAX_DELIVERY_ATTEMPTS):
        deliver(dev, receiver, clock)
        clock.advance(dt.timedelta(hours=2))
    with tenant_transaction(dev.ctx):
        row = WebhookSubscription.objects.get()
        assert (row.status, row.dead_streak) == ("disabled", dev_services.DISABLE_AFTER_DEAD)
    assert queue(dev)[1] == 0                                  # a disabled hook gets nothing new
    with tenant_transaction(dev.ctx):
        assert code_of(developer.set_webhook_status, dev.client_ref, hook["public_id"],
                       "disabled") == ErrorCode.VALIDATION_FAILED
        developer.set_webhook_status(dev.client_ref, hook["public_id"], "active")
    assert queue(dev)[1] == 1


def test_a_given_up_delivery_can_be_redriven_by_its_own_client_only():
    dev = Dev()
    hook, _ = dev.hook()
    queue(dev)
    clock = live_clock()
    for _ in range(dev_services.MAX_DELIVERY_ATTEMPTS):
        deliver(dev, Recorder(500), clock)
        clock.advance(dt.timedelta(hours=2))
    (dead,) = deliveries(dev, hook)
    assert dead["state"] == "dead"
    with tenant_transaction(dev.ctx):
        assert code_of(developer.redrive_delivery, "someone_else", dead["public_id"]) == (
            ErrorCode.NOT_FOUND)
        again = developer.redrive_delivery(dev.client_ref, dead["public_id"])
        assert (again["state"], again["attempts"]) == ("pending", 0)
        assert code_of(developer.redrive_delivery, dev.client_ref, dead["public_id"]) == (
            ErrorCode.UNSAFE_REPLAY)                            # only a dead one
    assert deliver(dev, Recorder(200), FixedClock(dt.datetime.now(UTC))) == 1


def test_a_test_ping_goes_through_the_same_signed_path():
    dev = Dev()
    hook, secret = dev.hook()
    with tenant_transaction(dev.ctx):
        ping = developer.send_test_event(dev.client_ref, hook["public_id"])
    assert (ping["event_type"], ping["state"]) == ("developer.ping.v1", "pending")
    receiver = Recorder(200)
    clock = live_clock()
    deliver(dev, receiver, clock)
    (_, headers, body), = receiver.calls
    assert developer.verify_signature(
        secret, timestamp=headers["X-Zoiko-Timestamp"], body=body,
        signature=headers["X-Zoiko-Signature"], now=int(clock.now().timestamp()))
    assert deliveries(dev, hook)[0]["state"] == "delivered"


# ------------------------------------------------------------------ the real HTTP sender
class Receiver(http.server.BaseHTTPRequestHandler):
    seen = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        Receiver.seen.append((self.headers.get("X-Zoiko-Signature"), self.rfile.read(length)))
        self.send_response(Receiver.status)
        if Receiver.status == 302:
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def local_receiver(settings):
    settings.WEBHOOK_ALLOW_INSECURE = True
    settings.WEBHOOK_ALLOW_PRIVATE = True
    Receiver.seen, Receiver.status = [], 200
    server = http.server.HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/hook"
    server.shutdown()
    server.server_close()


def test_the_real_sender_posts_the_signed_body_and_reports_the_status(local_receiver):
    body = b'{"event_id":"e1"}'
    headers = {"Content-Type": "application/json", "X-Zoiko-Signature": "v1=abc"}
    assert dev_services.http_sender(local_receiver, headers, body) == 200
    sent_signature, sent_body = Receiver.seen[0]
    assert sent_body == body and sent_signature == "v1=abc"
    Receiver.status = 500
    assert dev_services.http_sender(local_receiver, headers, body) == 500


def test_the_real_sender_never_follows_a_redirect(local_receiver):
    Receiver.status = 302
    assert dev_services.http_sender(local_receiver, {}, b"{}") == 302
    assert len(Receiver.seen) == 1


def test_the_real_sender_refuses_a_private_address_at_send_time():
    assert code_of(dev_services.http_sender, "https://127.0.0.1/x", {}, b"{}") == (
        ErrorCode.VALIDATION_FAILED)


# ------------------------------------------------------------------ the public HTTP API
def api(dev=None, key=None):
    client = Client()
    token = key or (dev.key if dev else None)
    extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}

    def call(method, path, body=None, **more):
        return getattr(client, method)(
            API + path, json.dumps(body) if body is not None else None,
            content_type="application/json", **extra, **more)

    return call


def test_the_contract_is_public_versioned_and_describes_signing():
    response = Client().get(API + "openapi.json")
    spec = json.loads(response.content)
    assert response.status_code == 200
    assert spec["info"]["version"] == "1.0.0" and "/webhooks" in spec["paths"]
    assert spec["x-webhook-signing"]["event_families"] == list(developer.PUBLIC_EVENT_PREFIXES)
    assert Client().post(API + "openapi.json").status_code == 405


def test_every_data_endpoint_needs_a_valid_key():
    for method, path in (("get", "ping"), ("get", "services"), ("get", "webhooks"),
                         ("post", "webhooks"), ("post", "webhooks/x/test")):
        response = api()(method, path)
        assert response.status_code == 401, path
        assert json.loads(response.content)["code"] == "UNAUTHENTICATED"
    assert api(key="zk1.bad.bad.bad")("get", "ping").status_code == 401


def test_ping_says_who_the_client_is_and_the_wrong_method_is_refused():
    dev = Dev(scopes=("catalog:read",), environment="sandbox")
    body = json.loads(api(dev)("get", "ping").content)
    assert (body["client"], body["environment"], body["scopes"]) == (
        dev.client_ref, "sandbox", ["catalog:read"])
    assert api(dev)("post", "ping", {}).status_code == 405


def test_a_sandbox_client_sees_only_synthetic_data():
    dev = Dev(scopes=("catalog:read",), environment="sandbox")
    body = json.loads(api(dev)("get", "services").content)
    assert body["sandbox"] is True
    assert {s["id"] for s in body["services"]} == {"sandbox-service-1", "sandbox-service-2"}


def test_a_production_client_sees_the_published_catalog_and_nothing_personal(settings):
    settings.DEBUG = True                 # the staff API used to make a service needs the dev login
    settings.DEV_AUTH_STUB = True
    web = Web()
    web.staff("desk")
    made = web.post("services", {"organization": web.clinic.org, "name": "Cardiology",
                                 "category": "cardiology", "duration_minutes": 30},
                    subject="desk")
    service = json.loads(made.content)["service"]["id"]
    dev = Dev(scopes=("catalog:read",), world=web.clinic)
    before = json.loads(api(dev)("get", "services").content)
    assert "Cardiology" not in [s["name"] for s in before["services"]]      # a draft is not public
    publish_service(web.clinic.ctx, service)
    body = json.loads(api(dev)("get", "services").content)
    assert body["sandbox"] is False
    (item,) = [s for s in body["services"] if s["name"] == "Cardiology"]
    assert item == {"id": service, "name": "Cardiology", "category": "cardiology",
                    "duration_minutes": 30, "mode": item["mode"]}
    assert api(Dev(scopes=("webhooks:manage",)))("get", "services").status_code == 403


def test_creating_a_webhook_over_http_needs_an_idempotency_key_and_shows_the_secret_once():
    dev = Dev()
    call = api(dev)
    body = {"url": PUBLIC_URL, "events": ["booking."]}
    assert call("post", "webhooks", body).status_code == 422             # no key
    key = uuid.uuid4().hex
    made = call("post", "webhooks", body, HTTP_IDEMPOTENCY_KEY=key)
    first = json.loads(made.content)
    assert made.status_code == 201 and first["secret"].startswith("whsec_")
    again = call("post", "webhooks", body, HTTP_IDEMPOTENCY_KEY=key)
    replay = json.loads(again.content)
    assert again.status_code == 200 and again["Idempotent-Replayed"] == "true"
    assert replay["webhook"]["public_id"] == first["webhook"]["public_id"]
    assert replay["secret"] is None                                      # never shown again
    clash = call("post", "webhooks", {**body, "events": ["referral."]}, HTTP_IDEMPOTENCY_KEY=key)
    assert clash.status_code == 409
    listing = call("get", "webhooks")
    assert len(json.loads(listing.content)["webhooks"]) == 1
    for text in (made.content.decode(), listing.content.decode()):
        assert "secret_token" not in text and "key_hash" not in text


def test_a_webhook_over_http_must_point_somewhere_safe_and_name_public_events():
    call = api(Dev())
    for body in ({"url": "https://127.0.0.1/x", "events": ["booking."]},
                 {"url": "http://8.8.8.8/x", "events": ["booking."]},
                 {"url": PUBLIC_URL, "events": ["admin."]},
                 {"url": PUBLIC_URL, "events": "booking."}, {"url": PUBLIC_URL}, {"events": []}):
        response = call("post", "webhooks", body, HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
        assert response.status_code == 422, body


def test_webhooks_can_be_paused_tested_and_inspected_over_http():
    dev = Dev()
    call = api(dev)
    made = call("post", "webhooks", {"url": PUBLIC_URL, "events": ["booking."]},
                HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
    hook = json.loads(made.content)["webhook"]["public_id"]
    paused = json.loads(call("post", f"webhooks/{hook}/pause").content)
    assert paused["webhook"]["status"] == "paused"
    resumed = json.loads(call("post", f"webhooks/{hook}/resume").content)
    assert resumed["webhook"]["status"] == "active"
    ping = call("post", f"webhooks/{hook}/test")
    assert ping.status_code == 202
    listed = json.loads(call("get", f"webhooks/{hook}/deliveries").content)["deliveries"]
    assert [d["event_type"] for d in listed] == ["developer.ping.v1"]
    redrive = call("post", f"deliveries/{listed[0]['public_id']}/redrive")
    assert redrive.status_code == 409                                    # it is not given up


def test_one_client_cannot_reach_anothers_webhooks_over_http():
    one, two = Dev(), Dev()
    made = api(one)("post", "webhooks", {"url": PUBLIC_URL, "events": ["booking."]},
                     HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
    hook = json.loads(made.content)["webhook"]["public_id"]
    stranger = api(two)
    for method, path in (("post", f"webhooks/{hook}/pause"), ("post", f"webhooks/{hook}/test"),
                         ("get", f"webhooks/{hook}/deliveries")):
        response = stranger(method, path)
        assert response.status_code in (404, 200), path
        if response.status_code == 200:
            assert json.loads(response.content)["deliveries"] == []
    assert json.loads(stranger("get", "webhooks").content)["webhooks"] == []
    assert api(two)("post", f"webhooks/{hook}/pause").status_code == 404
