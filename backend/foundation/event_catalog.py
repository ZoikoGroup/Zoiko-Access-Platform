"""The catalogue of events this product publishes, and the AsyncAPI 3.0 document built from it
(GHE-13 T-02, DEV-151).

Every event is a flat set of references (see foundation/outbox.py: no names, no free text). This
module says which events exist, which thing each is about, and which reference fields it carries.
A test reads the source and fails if code publishes an event that is not listed here, or this list
names one nobody publishes, so the catalogue cannot silently go stale. Each entry gets a schema
hash, so a change to what an event carries shows up as a changed hash in review."""
from __future__ import annotations

import hashlib
import json
from typing import Any

# type -> (what it is about, fields always present, fields sometimes present)
_BOOKING = ("booking", ("booking_ref", "slot_ref", "service_ref", "status"), ("side", "actor_ref"))
_REFERRAL = ("referral", ("referral_ref", "state"), ("actor_ref",))
# A partner operation moved on: which one, what kind, what it is about, and where it now stands.
_OPERATION = (
    "external_operation", ("operation_ref", "operation_type", "subject_ref", "state"), ())
_ENTRY = ("waitlist", ("entry_ref", "state"), ("actor_ref",))          # who joined or left
_ENTRY_SYSTEM = ("waitlist", ("entry_ref", "state"), ())                # done by the product

EVENTS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "booking.requested.v1": _BOOKING,
    "booking.confirmed.v1": _BOOKING,
    "booking.cancelled.v1": _BOOKING,
    "booking.rescheduled.v1": _BOOKING,
    "booking.reminder.v1": _BOOKING,
    "booking.attendance.v1": ("booking", ("booking_ref", "status", "attendance"), ()),
    "slot.opened.v1": ("service", ("service_ref",), ()),
    "waitlist.offered.v1": ("waitlist", ("entry_ref", "state"), ()),
    "waitlist.joined.v1": _ENTRY,
    "waitlist.prioritized.v1": _ENTRY,
    "waitlist.left.v1": _ENTRY,
    "waitlist.booked.v1": _ENTRY_SYSTEM,
    "waitlist.returned.v1": _ENTRY_SYSTEM,
    "message.sent.v1": ("thread", ("thread_ref", "side"), ("actor_ref",)),
    "referral.issued.v1": _REFERRAL,
    "referral.decided.v1": _REFERRAL,
    "referral.acknowledged.v1": _REFERRAL,
    "rights.requested.v1": ("rights_case", ("case_ref",), ()),
    "rights.withdrawn.v1": ("rights_case", ("case_ref", "side"), ()),
    "rights.closed.v1": ("rights_case", ("case_ref", "actor_ref"), ()),
    "family.decided.v1": ("family", ("grant_ref", "outcome"), ()),
    "delegation.revoked.v1": ("delegation", ("grant_ref", "delegate_ref"), ()),
    "delegation.age_review.v1": ("delegation", ("grant_ref", "person_ref"), ()),
    "consent.changed.v1": ("consent", ("consent_ref", "purpose", "state"), ()),
    "identity.subject.proofed.v1": ("person", ("person_ref", "outcome", "status"), ()),
    "connector.operation.confirmed.v1": _OPERATION,
    "connector.operation.rejected.v1": _OPERATION,
    "workflow.instance.started.v1": (
        "workflow_instance", ("instance_ref", "definition_key", "definition_version", "state"), ()),
    "workflow.instance.transitioned.v1": (
        "workflow_instance",
        ("instance_ref", "transition", "from_state", "to_state", "status"), ()),
    "workflow.timer.fired.v1": ("workflow_instance", ("instance_ref", "timer", "state"), ()),
    "professional.privilege.revoked.v1": (
        "role_assignment", ("assignment_ref", "actor_ref", "role_code", "organization_ref"), ()),
    "claim.consent_needed.v1": ("claim", ("claim_ref", "person_ref"), ()),
    "exchange.consent_needed.v1": ("exchange_request", ("request_ref", "person_ref", "kind"), ()),
    "exchange.reviewed.v1": ("exchange_request", ("request_ref", "person_ref", "kind"), ()),
    "workflow.changed.v1": ("workflow", ("instance_ref", "kind", "state", "overlay"), ()),
    "access.denied.v1": ("access_decision", ("operation", "purpose", "reason"), ()),
    "connector.operation.accepted.v1": _OPERATION,
    "connector.operation.unknown.v1": _OPERATION,
    "connector.operation.dead.v1": _OPERATION,
}

# Sent straight to a partner when it is being verified, not through the outbox: listed so a partner
# can see it, but not part of the published-event scan.
DIRECT: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "webhook.ping.v1": ("webhook", ("challenge",), ()),
    "developer.ping.v1": (
        "webhook", ("event_id", "event_type", "schema_version", "occurred_at", "aggregate_type",
                    "aggregate_ref", "correlation_id", "cell_id"), ()),
}


def _all() -> dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]:
    return {**EVENTS, **DIRECT}


def schema_of(event_type: str) -> dict[str, Any]:
    about, always, sometimes = _all()[event_type]
    props = {name: {"type": "string"} for name in (*always, *sometimes)}
    return {"type": "object", "additionalProperties": False, "required": list(always),
            "properties": props, "x-aggregate": about}


def schema_hash(event_type: str) -> str:
    canonical = json.dumps(schema_of(event_type), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_asyncapi() -> dict[str, Any]:
    channels: dict[str, Any] = {}
    messages: dict[str, Any] = {}
    operations: dict[str, Any] = {}
    for event_type in sorted(_all()):
        key = event_type.replace(".", "_")
        messages[key] = {
            "name": event_type, "contentType": "application/json",
            "payload": schema_of(event_type), "x-schema-hash": schema_hash(event_type)}
        channels[key] = {
            "address": event_type,
            "messages": {key: {"$ref": f"#/components/messages/{key}"}}}
        operations[f"publish_{key}"] = {
            "action": "send", "channel": {"$ref": f"#/channels/{key}"},
            "summary": f"Zoiko publishes {event_type}"}
    return {
        "asyncapi": "3.0.0",
        "info": {"title": "Zoiko Access events", "version": "1.0.0",
                 "description": "Events carry references only: never a name, a message or a "
                                "reason. No examples are included."},
        "channels": channels, "operations": operations, "components": {"messages": messages},
    }
