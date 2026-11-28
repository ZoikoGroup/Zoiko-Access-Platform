"""A booking confirmed by the clinic's own booking system, not only by a person.

By default a clinic's staff confirm a booking by hand, exactly as before. A clinic that chooses a
partner connector as its "booking system" (a setting on the organization) gets this instead:

  patient requests  -> the booking is "requested", as always
  staff "ask"       -> the connector is asked to confirm it; the booking stays "requested"
  partner confirms  -> ONLY then the booking becomes confirmed, with the connector operation as
                       the history's related reference; a refusal cancels the booking
  anything else     -> the booking stays "requested": a receipt is not a confirmation, an unknown
                       answer is asked about again, and a person settling the operation by hand
                       does not count as the provider's word (they decide what to do next: ask
                       again, cancel, or switch the clinic back to confirming by hand)

There is no workflow engine here (it was retired, DEV-211): where a booking stands with its
provider is read from the booking itself and the connector's record of what was asked. This module
only joins other modules together, through their public interfaces. It owns no tables.
"""
from __future__ import annotations

from typing import Any

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from modules.connector import api as connector
from modules.jurisdiction import api as jurisdiction
from modules.policy import api as policy
from modules.scheduling import api as scheduling
from modules.trust import api as trust

OPERATION_TYPE = "booking.confirm"
WRITE_BOOKING = "write:booking"
MANUAL = "manual_resolution"            # a connector operation a person settled by hand
LABELS = {"simulator": "Pretend partner (development only)"}
STILL_ASKING = ("pending", "sent", "accepted", "unknown")
# Who may ask the booking system to confirm: the same people who may confirm by hand.
CONFIRMING_ROLES = frozenset({"R04", "R13"})
AWAITING = "awaiting_provider"
REFUSED = "refused"


# --------------------------------------------------------------------------- the setting


def options() -> list[dict[str, str]]:
    """What a clinic may choose: confirming by hand, or any connector this deployment has."""
    chosen = [{"key": "", "label": "The clinic confirms by hand"}]
    return chosen + [{"key": k, "label": LABELS.get(k, k)} for k in connector.registered()]


def system_of(organization_ref: str) -> str:
    """The connector that confirms this clinic's bookings, or "" when staff confirm by hand."""
    return trust.booking_system_of(organization_ref)


PACK_CAPABILITY = "partner_booking"


def market_allows(system: str) -> bool:
    """Does the customer's market still allow this partner (DEV-224, DEV-229)? The pretend partner
    always may; a real one only while a signed, current pack switches partner bookings on."""
    if connector.is_simulated(system):
        return True
    if not connector.is_live(system):              # DEV-230: certified and put live by Zoiko
        return False
    return PACK_CAPABILITY in jurisdiction.customer_capabilities(require_context().tenant_id)


def partner_decides(organization_ref: str) -> bool:
    """True when the clinic's booking system, not its staff, must confirm: a system is chosen, it
    is working (not paused or halted, DEV-221) and its market still allows it (DEV-229). Otherwise
    staff confirm by hand: the alternate path."""
    system = system_of(organization_ref)
    return bool(system) and not connector.is_down(system) and market_allows(system)


def choose_system(organization_ref: str, system: str, *, by: str,
                  capabilities: frozenset[str] = frozenset()) -> str:
    """Set the clinic's booking system. Only a connector this deployment actually has may be
    chosen; "" goes back to confirming by hand. A real partner may be switched on only while a
    signed country pack for the customer's country allows partner bookings (`capabilities`, read
    by the caller; GHE-13 T-46, DEV-224). The pretend partner (development only) is exempt."""
    if not isinstance(system, str) or (system and system not in connector.registered()):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["system"])
    if system and not connector.is_simulated(system) and (
            PACK_CAPABILITY not in capabilities or not connector.is_live(system)):
        raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE)
    return trust.set_booking_system(organization_ref, system, by=by)


# --------------------------------------------------------------------------- asking


def _questions(booking_ref: str, limit: int = 50) -> list[dict[str, Any]]:
    """The "confirm this booking" operations asked about this booking, newest first."""
    return [o for o in connector.list_operations(subject_ref=booking_ref, limit=limit)
            if o["operation_type"] == OPERATION_TYPE]


def ask_provider(
    *, booking_ref: str, actor_public_id: str, decision: policy.Decision
) -> dict[str, str]:
    """Staff ask the clinic's booking system to confirm a requested booking. The booking does NOT
    become confirmed here: it waits for the system's own answer. The caller has already been
    authorized (`decision`); the connector checks the receipt again for exactly this booking and
    patient. Asking while an earlier question is still open asks nothing new."""
    if decision.role_code not in CONFIRMING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    booking = scheduling.get_booking(booking_ref)
    org = booking["slot"]["organization_ref"]
    if booking["status"] != "requested":
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)              # only a waiting booking
    system = system_of(org)
    if not system:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["system"])
    if not market_allows(system):
        raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE)   # DEV-229: the pack no longer allows it
    asked = _questions(booking_ref)
    if any(o["state"] in STILL_ASKING for o in asked):
        return stages([booking_ref])[booking_ref]                   # already asked: be patient
    # References only: the partner is told which booking and which service, never who it is for.
    connector.request_operation(
        connector_key=system, operation_type=OPERATION_TYPE, subject_ref=booking_ref,
        organization_ref=org, idempotency_key=f"ask-{booking_ref}-{len(asked) + 1}",
        request={"booking_ref": booking_ref, "service_ref": booking["slot"]["service_ref"]},
        requested_by_ref=actor_public_id, decision=decision, authorized_as=WRITE_BOOKING,
        receipt_subject_ref=booking["person_ref"])
    return stages([booking_ref])[booking_ref]


def stages(booking_refs: list[str]) -> dict[str, dict[str, str]]:
    """Where each booking stands with its provider system. Only bookings whose clinic's system was
    ever asked are listed. `stage` is "awaiting_provider" while the booking is still requested,
    "refused" when the provider said no, otherwise the booking's own status; `operation` is the
    state of the latest question while waiting, else ""."""
    out: dict[str, dict[str, str]] = {}
    for ref in booking_refs:
        asked = _questions(ref, limit=5)
        if not asked:
            continue
        try:
            status = scheduling.get_booking(ref)["status"]
        except ZoikoError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                continue
            raise
        latest = asked[0]
        if status == "requested":
            out[ref] = {"stage": AWAITING, "operation": latest["state"]}
        elif (status == "cancelled" and latest["state"] == "rejected"
              and latest["last_code"] != MANUAL):
            out[ref] = {"stage": REFUSED, "operation": ""}
        else:
            out[ref] = {"stage": status, "operation": ""}
    return out


# --------------------------------------------------------------------------- the answer


def on_provider_answer(operation_ref: str, confirmed: bool) -> None:
    """The connector settled a "confirm this booking" question. Only the PROVIDER's own answer
    counts: a receipt, a timeout and a person's manual settlement never confirm or refuse. An
    answer that arrives after the booking was cancelled or confirmed some other way changes
    nothing."""
    operation = connector.get_operation(operation_ref)
    if operation["operation_type"] != OPERATION_TYPE:
        return
    if operation["last_code"] == MANUAL:
        return
    if confirmed and not operation["source_confirmed"]:
        return
    ref = operation["subject_ref"]
    try:
        booking = scheduling.get_booking(ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return
        raise
    if booking["status"] != "requested":
        return                                      # cancelled or already settled meanwhile
    if booking["slot"]["organization_ref"] != operation["organization_ref"]:
        return                                      # never act on another clinic's booking
    if confirmed:
        scheduling.confirm_from_provider(booking_ref=ref, operation_ref=operation_ref)
    else:
        scheduling.decline_from_provider(booking_ref=ref, operation_ref=operation_ref)
