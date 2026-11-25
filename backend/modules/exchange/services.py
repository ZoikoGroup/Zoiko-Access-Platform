"""Sending, answering and reviewing requests between a clinic and a partner (DEV-163).

Every function here runs with the PLATFORM tenant bound (the exchange lives in Zoiko's space) and
is told who is asking: the caller has already been authorised in their own customer's space. Each
function filters on the caller's own customer, so a clinic never reads another clinic's rows and a
partner never reads a request sent to someone else; a row that does not name the caller is simply
"not found". What a partner is shown never includes the clinic's patient reference.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q

from foundation import fhir, pagination
from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.identifiers import contains_nhs_number
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import (
    CONSENT_DAYS,
    HOLD_ESCALATE_DAYS,
    KIND_ROLE,
    MAX_SUMMARY,
    Consent,
    Correction,
    ExchangeEvent,
    ExchangeRequest,
    ExchangeResultVersion,
    Kind,
    ResultFlag,
    State,
    Urgency,
)

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


def _clean_summary(value: Any) -> str:
    if not isinstance(value, str):
        raise _bad("summary")
    text = value.replace("\r\n", "\n").strip()
    if not 1 <= len(text) <= MAX_SUMMARY or "\x00" in text:
        raise _bad("summary")
    return text


MAX_VALUES = 50
VALUE_FLAGS = ("", "normal", "abnormal", "high", "low", "critical")
_VALUE_KEYS = {"name", "code", "value", "unit", "low", "high", "flag"}


def _plain(value: Any, field: str, most: int, *, required: bool = False) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= most or any(
        ord(ch) < 32 for ch in value
    ):
        raise _bad(field)
    return value.strip()


def _number(value: Any, field: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise _bad(field)
    return value


def _clean_values(values: Any) -> list[dict[str, Any]]:
    """The measured values of a result (DEV-330): at most 50, each with a name (up to 80
    characters), an optional SNOMED CT code, a value (a number, or a short text), an optional unit,
    normal range and flag. Nothing else is accepted, so no free text can hide in them."""
    if values in (None, []):
        return []
    if not isinstance(values, list) or len(values) > MAX_VALUES:
        raise _bad("values")
    out = []
    for v in values:
        if not isinstance(v, dict) or not set(v) <= _VALUE_KEYS:
            raise _bad("values")
        code = _plain(v.get("code"), "values", 18)
        if code and not fhir.sctid_ok(code):
            raise _bad("values")
        value = v.get("value")
        if isinstance(value, str):
            value = _plain(value, "values", 40, required=True)
        else:
            value = _number(value, "values")
            if value is None:
                raise _bad("values")
        flag = v.get("flag") or ""
        if flag not in VALUE_FLAGS:
            raise _bad("values")
        out.append({
            "name": _plain(v.get("name"), "values", 80, required=True),
            "code": code,
            "value": value,
            "unit": _plain(v.get("unit"), "values", 20),
            "low": _number(v.get("low"), "values"),
            "high": _number(v.get("high"), "values"),
            "flag": flag,
        })
    return out


def _seal_values(row: ExchangeRequest, values: list[dict[str, Any]]) -> str:
    if not values:
        return ""
    return encrypt(json.dumps(values), tenant_id=require_context().tenant_id,
                   context=f"exchange:{row.public_id}:values")


def _open_values(row: ExchangeRequest, sealed: str) -> list[dict[str, Any]]:
    if not sealed:
        return []
    try:
        found = json.loads(decrypt(sealed, tenant_id=row.tenant_id,
                                   context=f"exchange:{row.public_id}:values"))
    except (CryptoError, ValueError):
        return []
    return found if isinstance(found, list) else []


def consent_of(r: ExchangeRequest) -> str:
    """pending, granted, declined, withdrawn, or expired (a granted one past its end)."""
    if (
        r.consent_state == Consent.GRANTED
        and r.consent_expires_at is not None
        and r.consent_expires_at <= utc_now()
    ):
        return "expired"
    return str(r.consent_state)


def shared_identity(r: ExchangeRequest) -> dict[str, str] | None:
    """The name and birth date the patient agreed to share, only while that agreement stands."""
    if consent_of(r) != Consent.GRANTED or not r.shared_identity_enc:
        return None
    try:
        data = json.loads(
            decrypt(
                r.shared_identity_enc,
                tenant_id=r.tenant_id,
                context=f"exchange:{r.public_id}:identity",
            )
        )
    except (CryptoError, ValueError):
        return None
    return {"name": str(data.get("name", "")), "birth_date": str(data.get("birth_date", ""))}


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
        "order_code": r.order_code or None,
        "urgency": r.urgency,
        "state": r.state,
        "decision_note": r.decision_note,
        "result_ref": r.result_ref,
        "result_flag": r.result_flag,
        "has_summary": bool(r.result_summary_enc),
        "has_values": bool(r.result_values_enc),
        "result_version": r.result_version,
        "result_correction": r.result_correction or None,
        "on_hold": r.on_hold_at is not None,
        "hold_owner": r.on_hold_owner_ref,
        "hold_escalated": r.on_hold_escalated_at is not None,
        "consent": consent_of(r),
        "consent_expires_at": r.consent_expires_at.isoformat() if r.consent_expires_at else None,
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
        "result_flag": r.result_flag,
        "result_version": r.result_version,
        "result_correction": r.result_correction or None,
        "on_hold": r.on_hold_at is not None,
        "consent": consent_of(r),
        "identity": shared_identity(r),
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
        "consent": consent_of(r),
        "consent_expires_at": r.consent_expires_at.isoformat() if r.consent_expires_at else None,
        "created_at": r.created_at.isoformat(),
        "result_at": r.result_at.isoformat() if r.result_at else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "values_ready": _patient_may_see_values(r, utc_now()),
        "person": r.person_ref,
    }


PATIENT_VALUES_DELAY_HOURS = 24  # after the doctor's review, as for notes (DEV-332)


def _patient_may_see_values(r: ExchangeRequest, now: dt.datetime) -> bool:
    """A patient sees a result's values only once a doctor has reviewed THIS version, and a day
    after that, so the doctor can speak to them first (DEV-332, LEAD DECISION). A correction sends
    the result back to review, so its values wait again."""
    return (
        r.state == State.REVIEWED
        and r.reviewed_at is not None
        and bool(r.result_values_enc)
        and r.reviewed_at + dt.timedelta(hours=PATIENT_VALUES_DELAY_HOURS) <= now
    )


def patient_values(
    *, request_ref: str, tenant_ref: str, person_refs: list[str], actor_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """The measured values of a reviewed result, for the patient it is about (DEV-332). Not found
    before the review and the delay, or for anyone else. The doctor's summary text is not shown:
    it is written for clinicians. Every read is audited. Call with the platform tenant bound."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref, from_tenant_ref=tenant_ref, person_ref__in=person_refs, **_scope()
    ).first()
    if row is None or not _patient_may_see_values(row, utc_now(clock)):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    _audit(actor_ref, "exchange.values_read_by_patient", row)
    return {
        "id": row.public_id,
        "label": row.label,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "version": row.result_version,
        "values": _open_values(row, row.result_values_enc),
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
    order_code: Any = "",
) -> dict[str, Any]:
    if kind not in Kind.values:
        raise _bad("kind")
    if (
        not isinstance(subject_token, str)
        or not _TOKEN.match(subject_token)
        or contains_nhs_number(subject_token)
    ):  # DEV-222: never a national number
        raise _bad("token")
    if not isinstance(label, str) or not _LABEL.match(label.strip()) or contains_nhs_number(label):
        raise _bad("label")
    if urgency not in Urgency.values:
        raise _bad("urgency")
    if not isinstance(person_ref, str) or not _PERSON.match(person_ref):
        raise _bad("patient")
    code = order_code.strip() if isinstance(order_code, str) else None
    if code is None or (code and not fhir.sctid_ok(code)):
        raise _bad("order_code")
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
            order_code=code,
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


def sent_kind_of(request_ref: str, tenant_ref: str) -> str:
    """The kind of a request this clinic sent."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref, from_tenant_ref=tenant_ref, **_scope()
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


def announce_consent_needed(*, request_ref: str, person_ref: str, kind: str) -> None:
    """Tell the clinic's own space that a request is waiting for the patient's agreement, so the
    patient can be told (DEV-302). Runs with the CLINIC's tenant bound, inside its transaction:
    references only."""
    emit(
        event_type="exchange.consent_needed.v1",
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
    summary: Any = None,
    flag: Any = None,
    clock: Clock | None = None,
    values: Any = None,
) -> dict[str, Any]:
    if step not in PARTNER_STEPS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    allowed_from, target = PARTNER_STEPS[step]
    note = (note or "").strip() if isinstance(note, str) else ""
    if step == "decline" and not 3 <= len(note) <= 200:
        raise _bad("note")
    if step == "deliver" and (not isinstance(result_ref, str) or not _RESULT.match(result_ref)):
        raise _bad("result_ref")
    clean_summary = ""
    if step == "deliver" and summary not in (None, ""):
        clean_summary = _clean_summary(summary)
        if flag not in ResultFlag.values:
            raise _bad("flag")  # a summary always comes with the lab's rating
    elif step == "deliver" and flag not in (None, ""):
        if flag not in ResultFlag.values:
            raise _bad("flag")
    clean_values = _clean_values(values) if step == "deliver" else []
    if clean_values and flag not in ResultFlag.values:
        raise _bad("flag")  # values always come with the lab's overall rating
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
            return {**partner_dict(row), "changed": False}
        if step != "decline" and consent_of(row) != Consent.GRANTED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # no agreement from the patient (DEV-175)
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
            row.result_flag = str(flag or "")
            row.result_version = 1
            row.result_values_enc = _seal_values(row, clean_values)
            if clean_summary:
                row.result_summary_enc = encrypt(
                    clean_summary,
                    tenant_id=require_context().tenant_id,
                    context=f"exchange:{row.public_id}:result",
                )
        row.entity_version += 1
        row.save()
        _event(row, step, "partner", actor_ref)
        _audit(actor_ref, f"exchange.{step}", row)
        return {**partner_dict(row), "changed": True}


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
        "escalated": base.filter(on_hold_escalated_at__isnull=False, state__in=OPEN_STATES).count(),
        "on_hold_mine": base.filter(
            on_hold_at__isnull=False, state__in=OPEN_STATES, on_hold_owner_ref=actor_ref
        ).count()
        if actor_ref
        else 0,
    }


def role_for(kind: str) -> str:
    return KIND_ROLE[Kind(kind)]


OPEN_STATES = (State.REQUESTED, State.ACCEPTED, State.IN_PROGRESS)


def open_count_for(customer_ref: str) -> int:
    """How many requests are still open between this customer and anyone else (H-05, DEV-193).
    Call with the platform tenant bound, where the requests live."""
    return ExchangeRequest.objects.filter(
        Q(from_tenant_ref=customer_ref) | Q(to_tenant_ref=customer_ref),
        state__in=OPEN_STATES,
        **_scope(),
    ).count()


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
        row.on_hold_escalated_at = None
        row.entity_version += 1
        row.save(
            update_fields=[
                "on_hold_at",
                "on_hold_owner_ref",
                "on_hold_escalated_at",
                "entity_version",
                "updated_at",
            ]
        )
        _event(row, "released", "platform", actor_ref)
        _audit(actor_ref, "exchange.released", row)
        freed += 1
    return freed


# ------------------------------------------------ the patient's agreement (DEV-175)
def consent_request(request_ref: str, tenant_ref: str) -> ExchangeRequest:
    row = (
        ExchangeRequest.objects.select_for_update()
        .filter(public_id=request_ref, from_tenant_ref=tenant_ref, **_scope())
        .first()
    )
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def grant_consent(
    *,
    request_ref: str,
    tenant_ref: str,
    actor_ref: str,
    name: str,
    birth_date: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """The patient agrees to tell the partner their name and birth date for THIS order, for a
    limited time. Needs an open request; a lapsed agreement can be given again."""
    if not name:
        raise _bad("profile")
    now = utc_now(clock)
    with transaction.atomic():
        row = consent_request(request_ref, tenant_ref)
        if row.state not in OPEN_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if consent_of(row) == Consent.GRANTED:
            return patient_dict(row)
        if consent_of(row) not in (Consent.PENDING, "expired"):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        ctx = require_context()
        row.shared_identity_enc = encrypt(
            json.dumps({"name": name, "birth_date": birth_date}),
            tenant_id=ctx.tenant_id,
            context=f"exchange:{row.public_id}:identity",
        )
        row.consent_state, row.consent_by_ref = Consent.GRANTED, actor_ref
        row.consent_at, row.consent_expires_at = now, now + timedelta(days=CONSENT_DAYS)
        row.entity_version += 1
        row.save()
        _event(row, "consent_granted", "patient", actor_ref)
        _audit(actor_ref, "exchange.consent_granted", row)
        return patient_dict(row)


def decline_consent(*, request_ref: str, tenant_ref: str, actor_ref: str) -> dict[str, Any]:
    """The patient says no: nothing is shared and the request is cancelled."""
    with transaction.atomic():
        row = consent_request(request_ref, tenant_ref)
        if row.consent_state == Consent.DECLINED:
            return patient_dict(row)
        if row.consent_state != Consent.PENDING or row.state not in OPEN_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.consent_state, row.consent_by_ref = Consent.DECLINED, actor_ref
        row.state = State.CANCELLED
        row.entity_version += 1
        row.save()
        _event(row, "consent_declined", "patient", actor_ref)
        _audit(actor_ref, "exchange.consent_declined", row)
        return patient_dict(row)


def withdraw_consent(*, request_ref: str, tenant_ref: str, actor_ref: str) -> dict[str, Any]:
    """The patient takes the agreement back: what was shared is erased at once, and an order the
    partner has not finished is cancelled."""
    with transaction.atomic():
        row = consent_request(request_ref, tenant_ref)
        if row.consent_state == Consent.WITHDRAWN:
            return patient_dict(row)
        if row.consent_state != Consent.GRANTED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.consent_state = Consent.WITHDRAWN
        row.shared_identity_enc = ""
        if row.state in OPEN_STATES:
            row.state = State.CANCELLED
        row.entity_version += 1
        row.save()
        _event(row, "consent_withdrawn", "patient", actor_ref)
        _audit(actor_ref, "exchange.consent_withdrawn", row)
        return patient_dict(row)


def erase_expired_identities(clock: Clock | None = None) -> int:
    """Erase the name and birth date of every agreement that has run out (DEV-301). Hiding it was
    already immediate; this removes the stored copy. Call with the platform tenant bound."""
    now = utc_now(clock)
    ids = list(
        ExchangeRequest.objects.filter(
            consent_state=Consent.GRANTED, consent_expires_at__lte=now, **_scope()
        )
        .exclude(shared_identity_enc="")
        .values_list("id", flat=True)
    )
    for row_id in ids:
        with transaction.atomic():
            row = ExchangeRequest.objects.select_for_update().get(id=row_id)
            row.shared_identity_enc = ""
            row.entity_version += 1
            row.save(update_fields=["shared_identity_enc", "entity_version", "updated_at"])
            _event(row, "identity_erased", "platform", "system")
            _audit("system", "exchange.identity_erased", row)
    return len(ids)


def escalate_old_holds(clock: Clock | None = None) -> int:
    """A request held for HOLD_ESCALATE_DAYS without being cancelled or released is escalated to
    the clinic's administrator (DEV-303): it shows on the administrator's home screen whoever
    sent it. Call with the platform tenant bound. Each request is escalated once."""
    cutoff = utc_now(clock) - timedelta(days=HOLD_ESCALATE_DAYS)
    ids = list(
        ExchangeRequest.objects.filter(
            on_hold_at__lte=cutoff,
            on_hold_escalated_at__isnull=True,
            state__in=OPEN_STATES,
            **_scope(),
        ).values_list("id", flat=True)
    )
    for row_id in ids:
        with transaction.atomic():
            row = ExchangeRequest.objects.select_for_update().get(id=row_id)
            row.on_hold_escalated_at = utc_now(clock)
            row.entity_version += 1
            row.save(update_fields=["on_hold_escalated_at", "entity_version", "updated_at"])
            _event(row, "hold_escalated", "platform", "system")
            _audit("system", "exchange.hold_escalated", row)
    return len(ids)


def read_result(*, request_ref: str, tenant_ref: str, actor_ref: str) -> dict[str, Any]:
    """The lab's summary and rating for a delivered result, for the ordering clinic. The caller
    has checked that this is a treating doctor of that patient. Every read is audited, without the
    text. A result with no summary has nothing to open."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref, from_tenant_ref=tenant_ref, **_scope()
    ).first()
    if row is None or row.state not in (State.RESULT_AVAILABLE, State.REVIEWED):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if not row.result_summary_enc and not row.result_values_enc:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    try:
        text = decrypt(
            row.result_summary_enc,
            tenant_id=row.tenant_id,
            context=f"exchange:{row.public_id}:result",
        ) if row.result_summary_enc else ""
    except CryptoError:
        text = ""
    _audit(actor_ref, "exchange.result_read", row)
    return {
        "id": row.public_id,
        "flag": row.result_flag,
        "summary": text,
        "result_ref": row.result_ref,
        "delivered_at": row.result_at.isoformat() if row.result_at else None,
        "values": _open_values(row, row.result_values_enc),
        "version": row.result_version,
        "correction": row.result_correction or None,
        "earlier": _earlier_versions(row),
    }


def _earlier_versions(row: ExchangeRequest) -> list[dict[str, Any]]:
    """The versions a correction replaced, newest first, each exactly as delivered (DEV-325)."""
    out = []
    for v in ExchangeResultVersion.objects.filter(request=row, **_scope()).order_by("-version"):
        try:
            text = decrypt(
                v.summary_enc, tenant_id=row.tenant_id, context=f"exchange:{row.public_id}:result"
            ) if v.summary_enc else ""
        except CryptoError:
            text = ""
        out.append({
            "version": v.version,
            "flag": v.result_flag,
            "summary": text,
            "values": _open_values(row, v.values_enc),
            "result_ref": v.result_ref,
            "correction": v.correction or None,
            "delivered_at": v.delivered_at.isoformat() if v.delivered_at else None,
            "reviewed": v.reviewed_at is not None,
            "replaced_at": v.created_at.isoformat(),
        })
    return out


def correct_result(
    *,
    request_ref: str,
    tenant_ref: str,
    actor_ref: str,
    result_ref: Any,
    summary: Any = None,
    flag: Any = None,
    correction: Any = Correction.CORRECTED,
    clock: Clock | None = None,
    values: Any = None,
) -> dict[str, Any]:
    """The lab sends a later version of a result it already delivered (DEV-325, FHIR step 5). The
    current version is kept as it was (ExchangeResultVersion) and the new one replaces it. The
    result goes back to "result available": a clinician must review the new version, even if the
    old one was reviewed. Sending the same version twice changes nothing. It needs no new
    agreement from the patient: a correction to a result already given must always reach the
    doctor. Call with the platform tenant bound."""
    if correction not in Correction.values:
        raise _bad("correction")
    if not isinstance(result_ref, str) or not _RESULT.match(result_ref):
        raise _bad("result_ref")
    clean_summary = _clean_summary(summary) if summary not in (None, "") else ""
    clean_values = _clean_values(values)
    if flag not in ResultFlag.values:
        raise _bad("flag")  # a correction always says how the lab now rates the result
    now = utc_now(clock)
    with transaction.atomic():
        row = (
            ExchangeRequest.objects.select_for_update()
            .filter(public_id=request_ref, to_tenant_ref=tenant_ref, **_scope())
            .first()
        )
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state not in (State.RESULT_AVAILABLE, State.REVIEWED):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # nothing delivered yet to correct
        current = ""
        if row.result_summary_enc:
            try:
                current = decrypt(
                    row.result_summary_enc,
                    tenant_id=row.tenant_id,
                    context=f"exchange:{row.public_id}:result",
                )
            except CryptoError:
                current = ""
        if (row.result_ref, row.result_flag, current, row.result_correction,
                _open_values(row, row.result_values_enc)) == (
            result_ref, flag, clean_summary, correction, clean_values
        ):
            return {**partner_dict(row), "changed": False}  # the same correction again
        ExchangeResultVersion.objects.create(
            request=row,
            version=max(row.result_version, 1),
            result_ref=row.result_ref,
            result_flag=row.result_flag,
            summary_enc=row.result_summary_enc,
            values_enc=row.result_values_enc,
            delivered_at=row.result_at,
            delivered_by_ref=row.responded_by_ref,
            correction=row.result_correction,
            reviewed_by_ref=row.reviewed_by_ref,
            reviewed_at=row.reviewed_at,
            replaced_by_ref=actor_ref,
            **_scope(),
        )
        row.result_version = max(row.result_version, 1) + 1
        row.result_correction = correction
        row.result_ref, row.result_at, row.result_flag = result_ref, now, flag
        row.result_summary_enc = (
            encrypt(
                clean_summary,
                tenant_id=require_context().tenant_id,
                context=f"exchange:{row.public_id}:result",
            )
            if clean_summary
            else ""
        )
        row.result_values_enc = _seal_values(row, clean_values)
        row.state = State.RESULT_AVAILABLE
        row.reviewed_by_ref, row.reviewed_at = "", None
        row.responded_by_ref = actor_ref
        row.entity_version += 1
        row.save()
        _event(row, "correct", "partner", actor_ref)
        _audit(actor_ref, "exchange.result_corrected", row)
        return {**partner_dict(row), "changed": True}


def critical_target(*, request_ref: str, tenant_ref: str) -> dict[str, str] | None:
    """For a request delivered to this partner with a CRITICAL rating: the ordering clinic and the
    person who placed the order, so the clinic can alert them (DEV-310). None if it is not
    critical. Call with the platform tenant bound."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref,
        to_tenant_ref=tenant_ref,
        result_flag=ResultFlag.CRITICAL,
        state=State.RESULT_AVAILABLE,
        **_scope(),
    ).first()
    if row is None:
        return None
    return {"clinic": row.from_tenant_ref, "orderer": row.created_by_ref}


ORDER_OPERATION = "exchange.order"


def order_to_send(*, request_ref: str, tenant_ref: str) -> dict[str, Any] | None:
    """What goes to the lab's own system once the patient has agreed (DEV-323): references only
    (the request, the clinic's order number, the SNOMED CT code, the kind and urgency, and when the
    agreement was given). None when there is nothing to send: no code, or no current agreement.
    The FHIR ServiceRequest itself is built from these by the adapter at send time
    (`foundation.fhir.service_request`). Call with the platform tenant bound."""
    row = ExchangeRequest.objects.filter(
        public_id=request_ref, from_tenant_ref=tenant_ref, **_scope()
    ).first()
    if (
        row is None
        or not row.order_code
        or consent_of(row) != Consent.GRANTED
        or row.consent_at is None
        or row.state not in OPEN_STATES
    ):
        return None
    return {
        "partner": row.to_tenant_ref,
        "sender": row.created_by_ref,          # who at the clinic sent it (not part of the payload)
        "payload": {
            "request_ref": row.public_id,
            "order_no": row.subject_token,
            "order_code": row.order_code,
            "kind": row.kind,
            "urgency": row.urgency,
            "consent_at": row.consent_at.isoformat(),
        },
        "key": f"order-{row.public_id}-{int(row.consent_at.timestamp())}",
    }


def delivery_lookup(request_ref: str) -> dict[str, str] | None:
    """For the FHIR intake (modules/integration/fhir.py): who a request is from and to, and its
    state, so a partner's report can be matched to it. Kept here so no other module reads the
    exchange's tables directly (the import contract). Call with the platform tenant bound: the
    query is scoped to it, so it works under row-level security (DEV-326)."""
    row = ExchangeRequest.objects.filter(public_id=request_ref, **_scope()).first()
    if row is None:
        return None
    return {
        "from_tenant_ref": row.from_tenant_ref,
        "to_tenant_ref": row.to_tenant_ref,
        "state": str(row.state),
    }
