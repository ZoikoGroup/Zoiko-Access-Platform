"""A booking confirmed by the clinic's own booking system, not only by a person.

By default a clinic's staff confirm a booking by hand, exactly as before. A clinic that chooses a
partner connector as its "booking system" (a setting, DEV-126) gets this flow instead:

  patient requests  -> a journey starts (J1.partner_booking) at "requested"
  staff "ask"       -> the journey moves to "awaiting provider" and the connector is asked
  partner confirms  -> ONLY then the booking becomes confirmed, with the partner's confirmation as
                       the journey's evidence; a refusal cancels the booking
  anything else     -> the booking stays "requested": a receipt is not a confirmation, an unknown
                       answer is asked about again, and a person settling it by hand does not count
                       as the provider's word (they must decide what to do with the booking)

This module only joins other modules together, through their public interfaces. It owns no tables.
"""
from __future__ import annotations

from typing import Any

from foundation.errors import ErrorCode, ZoikoError
from modules.connector import api as connector
from modules.policy import api as policy
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.workflow import api as workflow
from modules.workflow.api import Evidence

DEFINITION = "J1.partner_booking"
OPERATION_TYPE = "booking.confirm"
WRITE_BOOKING = "write:booking"
SYSTEM_ACTOR = "system"                 # nobody: the product itself
PROVIDER_ACTOR = "provider"             # the clinic's booking system speaking for itself
MANUAL = "manual_resolution"            # a connector operation a person settled by hand
LABELS = {"simulator": "Pretend partner (development only)"}
STILL_ASKING = ("pending", "sent", "accepted", "unknown")
# Who may ask the booking system to confirm: the same people who may confirm by hand.
CONFIRMING_ROLES = frozenset({"R04", "R13"})


# --------------------------------------------------------------------------- the setting


def options() -> list[dict[str, str]]:
    """What a clinic may choose: confirming by hand, or any connector this deployment has."""
    chosen = [{"key": "", "label": "The clinic confirms by hand"}]
    return chosen + [{"key": k, "label": LABELS.get(k, k)} for k in connector.registered()]


def system_of(organization_ref: str) -> str:
    return trust.booking_system_of(organization_ref)


def choose_system(organization_ref: str, system: str, *, by: str) -> str:
    if system and system not in connector.registered():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["system"])
    return trust.set_booking_system(organization_ref, system, by=by)


# --------------------------------------------------------------------------- the journey


def _start(booking_ref: str, organization_ref: str) -> dict[str, Any]:
    existing = workflow.running_instance_for(booking_ref, DEFINITION)
    if existing is not None:
        return existing

    def start() -> dict[str, Any]:
        return workflow.engine.start(
            definition_key=DEFINITION, subject_ref=booking_ref, owner_ref=organization_ref,
            actor_ref=SYSTEM_ACTOR, organization_ref=organization_ref)

    try:
        return start()
    except ZoikoError as exc:
        if exc.code != ErrorCode.NOT_FOUND:
            raise
        workflow.ensure_bundled_definitions()        # the first booking of a new cell
        return start()


def _advance(
    booking_ref: str, transition: str, actor_ref: str, actor_kind: str,
    evidence: list[Evidence] | None = None,
) -> bool:
    """Move the booking's journey on if it is running and the move is allowed from where it is.
    Anything else (no journey, already ended, a different state) means there is nothing to do."""
    instance = workflow.running_instance_for(booking_ref, DEFINITION)
    if instance is None:
        return False
    try:
        workflow.engine.submit_transition(
            instance_ref=instance["public_id"], transition=transition, actor_ref=actor_ref,
            actor_kind=actor_kind, expected_version=instance["entity_version"],
            evidence=evidence)
    except ZoikoError as exc:
        if exc.code in (ErrorCode.VALIDATION_FAILED, ErrorCode.CONFLICT_VERSION):
            return False
        raise
    return True


def ask_provider(
    *, booking_ref: str, actor_public_id: str, decision: policy.Decision
) -> dict[str, str]:
    """Staff ask the clinic's booking system to confirm a requested booking. The booking does NOT
    become confirmed here: it waits for the system's own answer. The caller has already been
    authorized (`decision`); the connector checks the receipt again for exactly this booking."""
    if decision.role_code not in CONFIRMING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    booking = scheduling.get_booking(booking_ref)
    org = booking["slot"]["organization_ref"]
    system = system_of(org)
    if not system:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["system"])
    if booking["status"] != "requested":
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    asked = [o for o in connector.list_operations(subject_ref=booking_ref, limit=50)
             if o["operation_type"] == OPERATION_TYPE]
    if any(o["state"] in STILL_ASKING for o in asked):
        return stages([booking_ref])[booking_ref]                   # already asked: be patient
    instance = _start(booking_ref, org)
    if instance["state"] == "requested":
        instance = workflow.engine.submit_transition(
            instance_ref=instance["public_id"], transition="ask_provider",
            actor_ref=actor_public_id, actor_kind="person",
            expected_version=instance["entity_version"])
    elif instance["state"] != "awaiting_provider":
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    connector.request_operation(
        connector_key=system, operation_type=OPERATION_TYPE, subject_ref=booking_ref,
        organization_ref=org, idempotency_key=f"ask-{booking_ref}-{len(asked) + 1}",
        request={"booking_ref": booking_ref, "service_ref": booking["slot"]["service_ref"]},
        requested_by_ref=actor_public_id, decision=decision, authorized_as=WRITE_BOOKING,
        receipt_subject_ref=booking["person_ref"])
    return stages([booking_ref])[booking_ref]


def stages(booking_refs: list[str]) -> dict[str, dict[str, str]]:
    """Where each booking stands with its provider system: the journey state and, while waiting,
    what became of the latest question. Bookings without a journey are not listed."""
    if not booking_refs:
        return {}
    out: dict[str, dict[str, str]] = {}
    for ref, instance in workflow.instances_for(booking_refs, DEFINITION).items():
        operation = ""
        if instance["state"] == "awaiting_provider":
            latest = [o for o in connector.list_operations(subject_ref=ref, limit=5)
                      if o["operation_type"] == OPERATION_TYPE]
            operation = latest[0]["state"] if latest else ""
        out[ref] = {"stage": instance["state"], "operation": operation}
    return out


# --------------------------------------------------------------------------- reacting to events


def on_requested(booking_ref: str) -> None:
    """A booking was requested: if its clinic uses a booking system, start its journey."""
    try:
        booking = scheduling.get_booking(booking_ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return
        raise
    org = booking["slot"]["organization_ref"]
    if booking["status"] == "requested" and system_of(org):
        _start(booking_ref, org)


def on_cancelled(booking_ref: str, by: str) -> None:
    if by != SYSTEM_ACTOR:                         # a refusal by the provider already ended it
        _advance(booking_ref, "cancel", by, "person")


def on_attendance(booking_ref: str, attendance: str) -> None:
    transition = {"attended": "attend", "no_show": "miss"}.get(attendance)
    if transition:
        _advance(booking_ref, transition, "clinic", "person")


def on_provider_answer(operation_ref: str, confirmed: bool) -> None:
    """The connector settled a "confirm this booking" question. Only the PROVIDER's own answer
    counts: a receipt, a timeout and a person's manual settlement never confirm or refuse."""
    operation = connector.get_operation(operation_ref)
    if operation["operation_type"] != OPERATION_TYPE:
        return
    byhand = operation["last_code"] == MANUAL
    if byhand or (confirmed and not operation["source_confirmed"]):
        return
    ref = operation["subject_ref"]
    try:
        booking = scheduling.get_booking(ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return
        raise
    if booking["status"] != "requested":
        return                                      # cancelled or already confirmed meanwhile
    evidence = [Evidence("source_confirmed_outcome", operation_ref)]
    if not _advance(ref, "provider_confirm" if confirmed else "provider_refuse",
                    PROVIDER_ACTOR, "partner", evidence):
        return
    if confirmed:
        scheduling.confirm_from_provider(booking_ref=ref, operation_ref=operation_ref)
    else:
        scheduling.decline_from_provider(booking_ref=ref, operation_ref=operation_ref)
