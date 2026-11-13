"""Sending, answering and reviewing requests between a clinic and a partner (DEV-163).

Every function here runs with the PLATFORM tenant bound (the exchange lives in Zoiko's space) and
is told who is asking: the caller has already been authorised in their own customer's space. Each
function filters on the caller's own customer, so a clinic never reads another clinic's rows and a
partner never reads a request sent to someone else; a row that does not name the caller is simply
"not found". What a partner is shown never includes the clinic's patient reference.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from django.db import transaction
from django.db.models import Q

from foundation import pagination
from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import KIND_ROLE, ExchangeEvent, ExchangeRequest, Kind, State, Urgency

MAX_OPEN_TO_ONE_PARTNER = 200
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.()/&+_-]{2,79}$")
_RESULT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
_PERSON = re.compile(r"^[A-Za-z0-9_-]{8,22}$")

# step -> (who may take it, states it may be taken from, state it leads to)
CLINIC_STEPS = {
    "cancel": ((State.REQUESTED, State.ACCEPTED, State.IN_PROGRESS), State.CANCELLED),
    "review": ((State.RESULT_AVAILABLE,), State.REVIEWED),
}
PARTNER_STEPS = {
    "accept": ((State.REQUESTED,), State.ACCEPTED),
    "decline": ((State.REQUESTED,), State.DECLINED),
    "start": ((State.ACCEPTED,), State.IN_PROGRESS),
    "deliver": ((State.IN_PROGRESS,), State.RESULT_AVAILABLE),
}


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _event(req: ExchangeRequest, what: str, side: str, actor: str) -> None:
    last = ExchangeEvent.objects.filter(request=req, **_scope()).order_by("-seq").first()
    ExchangeEvent.objects.create(
        request=req,
        seq=(last.seq + 1) if last else 1,
        what=what,
        side=side,
        actor_ref=actor,
        **_scope(),
    )


def _audit(actor: str, action: str, req: ExchangeRequest) -> None:
    record(
        actor_ref=actor,
        action=action,
        object_type="exchange_request",
        object_ref=req.public_id,
        outcome=AuditOutcome.ALLOWED,
    )


def clinic_dict(r: ExchangeRequest) -> dict[str, Any]:
    """What the sending clinic sees."""
    return {
        "id": r.public_id,
        "ref_no": r.ref_no,
        "kind": r.kind,
        "partner": r.to_name,
        "partner_ref": r.to_tenant_ref,
        "token": r.subject_token,
        "patient": r.person_ref,
        "label": r.label,
        "urgency": r.urgency,
        "state": r.state,
        "decision_note": r.decision_note,
        "result_ref": r.result_ref,
        "on_hold": r.on_hold_at is not None,
        "hold_owner": r.on_hold_owner_ref,
        "created_at": r.created_at.isoformat(),
        "result_at": r.result_at.isoformat() if r.result_at else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
    }


def partner_dict(r: ExchangeRequest) -> dict[str, Any]:
    """What the receiving partner sees: never the clinic's patient reference."""
    return {
        "id": r.public_id,
        "ref_no": r.ref_no,
        "kind": r.kind,
        "from": r.from_name,
        "token": r.subject_token,
        "label": r.label,
        "urgency": r.urgency,
        "state": r.state,
        "decision_note": r.decision_note,
        "result_ref": r.result_ref,
        "on_hold": r.on_hold_at is not None,
        "created_at": r.created_at.isoformat(),
        "accepted_at": r.accepted_at.isoformat() if r.accepted_at else None,
        "result_at": r.result_at.isoformat() if r.result_at else None,
    }


def patient_dict(r: ExchangeRequest) -> dict[str, Any]:
    """What the patient sees about a request made for them: what was asked, of whom and how far it
    has got. No order number, no partner reference and no reason text."""
    return {
        "id": r.public_id,
        "kind": r.kind,
        "partner": r.to_name,
        "label": r.label,
        "urgency": r.urgency,
        "state": r.state,
        "created_at": r.created_at.isoformat(),
        "result_at": r.result_at.isoformat() if r.result_at else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "person": r.person_ref,
    }


def send(
    *,
    from_tenant_ref: str,
    from_name: str,
    to_tenant_ref: str,
    to_name: str,
    kind: Any,
    subject_token: Any,
    person_ref: Any,
    label: Any,
    urgency: Any,
    actor_ref: str,
) -> dict[str, Any]:
    if kind not in Kind.values:
        raise _bad("kind")
    if not isinstance(subject_token, str) or not _TOKEN.match(subject_token):
        raise _bad("token")
    if not isinstance(label, str) or not _LABEL.match(label.strip()):
        raise _bad("label")
    if urgency not in Urgency.values:
        raise _bad("urgency")
    if not isinstance(person_ref, str) or not _PERSON.match(person_ref):
        raise _bad("patient")
    if from_tenant_ref == to_tenant_ref:
        raise _bad("partner")
    with transaction.atomic():
        open_now = ExchangeRequest.objects.filter(
            from_tenant_ref=from_tenant_ref,
            to_tenant_ref=to_tenant_ref,
            state__in=(State.REQUESTED, State.ACCEPTED, State.IN_PROGRESS),
            **_scope(),
        ).count()
        if open_now >= MAX_OPEN_TO_ONE_PARTNER:
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        row = ExchangeRequest.objects.create(
            ref_no="XCH-" + secrets.token_hex(4).upper(),
            kind=kind,
            from_tenant_ref=from_tenant_ref,
            from_name=from_name,
            to_tenant_ref=to_tenant_ref,
            to_name=to_name,
            subject_token=subject_token,
            person_ref=person_ref,
            label=label.strip(),
            urgency=urgency,
            created_by_ref=actor_ref,
            **_scope(),
        )
        _event(row, "sent", "clinic", actor_ref)
        _audit(actor_ref, "exchange.send", row)
        return clinic_dict(row)


def _sent_rows(tenant_ref: str, person_refs: list[str] | None, state: str | None) -> Any:
    rows = ExchangeRequest.objects.filter(from_tenant_ref=tenant_ref, **_scope())
    if person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    if state:
        if state not in State.values:
            raise _bad("state")
        rows = rows.filter(state=state)
    return rows


def list_sent(
    *, tenant_ref: str, person_refs: list[str] | None = None, state: str | None = None
) -> list[dict[str, Any]]:
    rows = _sent_rows(tenant_ref, person_refs, state)
    return [clinic_dict(r) for r in rows.order_by("-created_at")[:200]]


def page_sent(
    *,
    tenant_ref: str,
    person_refs: list[str] | None,
    state: str | None,
    limit: int,
    cursor: str | None,
    actor_ref: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page, newest first, and the cursor for the next (DEV-171)."""
    found, nxt = pagination.page(
        _sent_rows(tenant_ref, person_refs, state),
        limit=limit,
        cursor=cursor,
        actor=actor_ref,
        lst="exchange.sent",
    )
    return [clinic_dict(r) for r in found], nxt


def list_for_patients(*, tenant_ref: str, person_refs: list[str]) -> list[dict[str, Any]]:
    """Requests the clinic made for these patients (the caller has checked each one)."""
    if not person_refs:
        return []
    rows = ExchangeRequest.objects.filter(
        from_tenant_ref=tenant_ref, person_ref__in=person_refs, **_scope()
    )
    return [patient_dict(r) for r in rows.order_by("-created_at")[:200]]


def _incoming_rows(tenant_ref: str, state: str | None) -> Any:
    rows = ExchangeRequest.objects.filter(to_tenant_ref=tenant_ref, **_scope())
    if state:
        if state not in State.values:
            raise _bad("state")
        rows = rows.filter(state=state)
    return rows


def list_incoming(*, tenant_ref: str, state: str | None = None) -> list[dict[str, Any]]:
    rows = _incoming_rows(tenant_ref, state)
    return [partner_dict(r) for r in rows.order_by("-created_at")[:200]]


def page_incoming(
    *, tenant_ref: str, state: str | None, limit: int, cursor: str | None, actor_ref: str
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of what was sent to this partner, newest first (DEV-172)."""
    found, nxt = pagination.page(
        _incoming_rows(tenant_ref, state),
        limit=limit,
        cursor=cursor,
        actor=actor_ref,
        lst="exchange.incoming",
    )
    return [partner_dict(r) for r in found], nxt


def person_of(request_ref: str, tenant_ref: str) -> str:
    """The sending clinic's patient reference, so the clinic can check its own policy."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref, from_tenant_ref=tenant_ref, **_scope()
    ).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row.person_ref


def kind_of(request_ref: str, tenant_ref: str) -> str:
    """The kind of a request addressed to this partner, so the partner's role can be checked."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref, to_tenant_ref=tenant_ref, **_scope()
    ).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row.kind


def clinic_step(
    *,
    request_ref: str,
    tenant_ref: str,
    step: str,
    actor_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    if step not in CLINIC_STEPS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    allowed_from, target = CLINIC_STEPS[step]
    with transaction.atomic():
        row = (
            ExchangeRequest.objects.select_for_update()
            .filter(public_id=request_ref, from_tenant_ref=tenant_ref, **_scope())
            .first()
        )
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state == target:
            return {**clinic_dict(row), "changed": False}  # repeating a step is harmless
        if row.state not in allowed_from:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.state = target
        if step == "review":
            row.reviewed_by_ref, row.reviewed_at = actor_ref, utc_now(clock)
        row.entity_version += 1
        row.save()
        _event(row, step, "clinic", actor_ref)
        _audit(actor_ref, f"exchange.{step}", row)
        return {**clinic_dict(row), "changed": True}


def announce_reviewed(*, request_ref: str, person_ref: str, kind: str) -> None:
    """Tell the clinic's own space that a doctor reviewed a result, so the patient can be told
    (DEV-166). Runs with the CLINIC's tenant bound and inside its transaction: references only."""
    emit(
        event_type="exchange.reviewed.v1",
        aggregate_type="exchange_request",
        aggregate_ref=request_ref,
        payload={"request_ref": request_ref, "person_ref": person_ref, "kind": kind},
    )


def partner_step(
    *,
    request_ref: str,
    tenant_ref: str,
    step: str,
    actor_ref: str,
    note: Any = "",
    result_ref: Any = "",
    clock: Clock | None = None,
) -> dict[str, Any]:
    if step not in PARTNER_STEPS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    allowed_from, target = PARTNER_STEPS[step]
    note = (note or "").strip() if isinstance(note, str) else ""
    if step == "decline" and not 3 <= len(note) <= 200:
        raise _bad("note")
    if step == "deliver" and (not isinstance(result_ref, str) or not _RESULT.match(result_ref)):
        raise _bad("result_ref")
    now = utc_now(clock)
    with transaction.atomic():
        row = (
            ExchangeRequest.objects.select_for_update()
            .filter(public_id=request_ref, to_tenant_ref=tenant_ref, **_scope())
            .first()
        )
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state == target:
            return partner_dict(row)
        if row.on_hold_at is not None or row.state not in allowed_from:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.state = target
        row.responded_by_ref = actor_ref
        if step == "accept":
            row.accepted_at = now
        if step == "decline":
            row.decision_note = note
        if step == "deliver":
            row.result_ref, row.result_at = result_ref, now
        row.entity_version += 1
        row.save()
        _event(row, step, "partner", actor_ref)
        _audit(actor_ref, f"exchange.{step}", row)
        return partner_dict(row)


def history(*, request_ref: str, tenant_ref: str) -> list[dict[str, Any]]:
    """The steps of a request, for either party to it. Who did it is shown as a side, not a name."""
    row = ExchangeRequest.objects.filter(public_id=request_ref, **_scope()).first()
    if row is None or tenant_ref not in (row.from_tenant_ref, row.to_tenant_ref):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    events = ExchangeEvent.objects.filter(request=row, **_scope()).order_by("seq")
    return [{"what": e.what, "side": e.side, "at": e.created_at.isoformat()} for e in events]


def partner_counts(tenant_ref: str) -> dict[str, int]:
    base = ExchangeRequest.objects.filter(to_tenant_ref=tenant_ref, **_scope())
    return {
        "new": base.filter(state=State.REQUESTED).count(),
        "in_progress": base.filter(state__in=(State.ACCEPTED, State.IN_PROGRESS)).count(),
        "delivered": base.filter(state__in=(State.RESULT_AVAILABLE, State.REVIEWED)).count(),
    }


def clinic_counts(tenant_ref: str, actor_ref: str = "") -> dict[str, int]:
    base = ExchangeRequest.objects.filter(from_tenant_ref=tenant_ref, **_scope())
    return {
        "waiting": base.filter(
            state__in=(State.REQUESTED, State.ACCEPTED, State.IN_PROGRESS)
        ).count(),
        "to_review": base.filter(state=State.RESULT_AVAILABLE).count(),
        "on_hold": base.filter(on_hold_at__isnull=False, state__in=OPEN_STATES).count(),
        "on_hold_mine": base.filter(
            on_hold_at__isnull=False, state__in=OPEN_STATES, on_hold_owner_ref=actor_ref
        ).count()
        if actor_ref
        else 0,
    }


def role_for(kind: str) -> str:
    return KIND_ROLE[Kind(kind)]


OPEN_STATES = (State.REQUESTED, State.ACCEPTED, State.IN_PROGRESS)


def hold_open_for(customer_ref: str, *, actor_ref: str, clock: Clock | None = None) -> int:
    """A customer was suspended: every request still open between it and someone else is put on
    hold, so the other side sees it and a person there decides (cancel, or wait). Runs inside the
    suspension's own transaction. Returns how many were held."""
    held = 0
    rows = ExchangeRequest.objects.select_for_update().filter(
        Q(from_tenant_ref=customer_ref) | Q(to_tenant_ref=customer_ref),
        state__in=OPEN_STATES,
        on_hold_at__isnull=True,
        **_scope(),
    )
    for row in rows:
        row.on_hold_at = utc_now(clock)
        # the partner went away: the person who sent it must decide (cancel, or wait)
        row.on_hold_owner_ref = row.created_by_ref if row.to_tenant_ref == customer_ref else ""
        row.entity_version += 1
        row.save(update_fields=["on_hold_at", "on_hold_owner_ref", "entity_version", "updated_at"])
        _event(row, "on_hold", "platform", actor_ref)
        _audit(actor_ref, "exchange.on_hold", row)
        held += 1
    return held


def release_open_for(customer_ref: str, *, actor_ref: str) -> int:
    """The customer was reinstated: requests held because of it carry on."""
    freed = 0
    rows = ExchangeRequest.objects.select_for_update().filter(
        Q(from_tenant_ref=customer_ref) | Q(to_tenant_ref=customer_ref),
        on_hold_at__isnull=False,
        **_scope(),
    )
    for row in rows:
        row.on_hold_at, row.on_hold_owner_ref = None, ""
        row.entity_version += 1
        row.save(update_fields=["on_hold_at", "on_hold_owner_ref", "entity_version", "updated_at"])
        _event(row, "released", "platform", actor_ref)
        _audit(actor_ref, "exchange.released", row)
        freed += 1
    return freed
