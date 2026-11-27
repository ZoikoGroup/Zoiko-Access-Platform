"""Fill the staff console's Exceptions, Journeys and Integrations screens with demo data.

DEVELOPMENT ONLY. Run it inside the backend container, against the "Demo Clinic" customer
(or another one: add `-e DEMO_TENANT="E2E Clinic"` after `exec`):

    docker compose exec -T backend python manage.py shell < scripts/seed_console_demo.py

It uses the pretend partner (the simulator), so no real partner is contacted. Run it again for more
rows; nothing is overwritten. It refuses to run unless the backend is in development mode.
"""
import datetime as dt
import os
import uuid

from django.conf import settings

from foundation.cell_context import bind_context, tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.connector import api as connector
from modules.developer import api as developer
from modules.policy.api import decide
from modules.trust import api as trust
from modules.workflow import api as workflow
from modules.workflow.api import Evidence

assert settings.DEBUG and settings.CONNECTOR_SIMULATOR, "development mode only"

CUSTOMER = os.environ.get("DEMO_TENANT", "Demo Clinic")
tenant = next(t for t in trust.list_tenants() if t["name"] == CUSTOMER)
ctx = trust.context_for(tenant["public_id"])
tag = uuid.uuid4().hex[:6]

with tenant_transaction(ctx):
    org = next(iter(trust.organization_names()))
    engineer = trust.register_actor(
        actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject=f"demo.partner.{tag}")
    trust.assign_role(actor_public_id=engineer.public_id, organization_public_id=org,
                      role_code="R10", by="system")
engineer_ref = engineer.public_id

# ---------------------------------------------------------------- partner operations
for label, sim, extra, attempts in (
    ("a confirmed booking", "confirm_now", {}, 5),
    ("a reply that got lost", "timeout", {}, 5),
    ("a partner that is down", "down", {"sim_fail": 50}, 1),
    ("a partner that never answers", "silent", {}, 5),
    ("a partner that refuses", "reject", {}, 5),
):
    with tenant_transaction(ctx):
        decision = decide(actor_public_id=engineer_ref, operation="write:partner",
                          purpose_code="INTEGRATION", organization_public_id=org)
    with tenant_transaction(ctx):
        connector.request_operation(
            connector_key="simulator", operation_type="booking.confirm",
            subject_ref=f"DEMO-{tag}-{sim}", organization_ref=org,
            idempotency_key=uuid.uuid4().hex, request={"sim": sim, **extra},
            requested_by_ref=engineer_ref, decision=decision, max_attempts=attempts)
    print(f"requested: {label}")
with bind_context(ctx):
    connector.send_due()
print("worker pass done: some operations now need a person")

# ---------------------------------------------------------------- journeys
workflow.load_bundled_definitions()


def start(subject, clock=None):
    with tenant_transaction(ctx):
        return workflow.engine.start(
            definition_key="J1.booking", subject_ref=subject, owner_ref="demo.doctor",
            actor_ref="demo_seed", organization_ref=org, clock=clock)


late = start(f"DEMO-{tag}-late", FixedClock(dt.datetime(2020, 1, 1, tzinfo=UTC)))
with tenant_transaction(ctx):
    workflow.engine.fire_due_timers()                      # its deadline ran out long ago
moving = start(f"DEMO-{tag}-moving")
with tenant_transaction(ctx):
    workflow.engine.submit_transition(
        instance_ref=moving["public_id"], transition="confirm", actor_ref="partner.demo",
        actor_kind="partner", expected_version=moving["entity_version"],
        evidence=[Evidence("source_confirmed_outcome", f"EV-{tag}")])
gone = start(f"DEMO-{tag}-cancelled")
with tenant_transaction(ctx):
    workflow.engine.submit_transition(
        instance_ref=gone["public_id"], transition="cancel", actor_ref="reception.demo",
        actor_kind="person", expected_version=gone["entity_version"])
print("journeys started: one overdue, one confirmed, one cancelled")

# ---------------------------------------------------------------- an API client and webhook
client, key = developer.create_client(
    tenant_public_id=tenant["public_id"], name=f"Demo Labs {tag}", environment="sandbox",
    scopes=["catalog:read", "webhooks:manage", "events:read"], created_by_ref="demo_seed",
    organization_ref=org)
with tenant_transaction(ctx):
    hook, _secret = developer.create_webhook(
        client_ref=client["public_id"], url="https://8.8.8.8/demo-hook",
        event_prefixes=["booking.", "connector."])
    developer.send_test_event(client["public_id"], hook["public_id"])
with bind_context(ctx):
    developer.deliver_due(sender=lambda url, headers, body: 200)   # one test message "delivered"
with tenant_transaction(ctx):
    developer.send_test_event(client["public_id"], hook["public_id"])   # one left waiting
print(f"API client {client['public_id']} (sandbox) with one webhook")
print(f"Its key (shown only now): {key}")
print("\nSign in to the staff console as clinicadmin@gmail.com (Demo Clinic) and open")
print("Exceptions, Journeys and Integrations.")
