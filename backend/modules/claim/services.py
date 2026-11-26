"""Claims between a clinic and a payer. Every function runs with the PLATFORM tenant bound, where
the two customers meet; the caller (an API view) has already checked the caller's rights in their
own space. A payer sees only: member id, service, amount, date. A clinic's patient link is never
shown to the payer."""

from __future__ import annotations

import datetime as dt
import re
import secrets
from typing import Any

from django.db import transaction
from django.db.models import Q

from foundation import pagination
from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.identifiers import contains_nhs_number
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import (
    MAX_AMOUNT_MINOR,
    MAX_OPEN_TO_ONE_PAYER,
    Claim,
    ClaimAttachment,
    ClaimConsent,
    ClaimEvent,
    ClaimScanState,
    ClaimState,
    DeclineReason,
)

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_CLAIM = 3
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_ACTIVE_PDF = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA|RichMedia|XFA)(?![A-Za-z0-9])"
)

_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.()/&+_-]{2,79}$")
_PAYMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
_PERSON = re.compile(r"^[A-Za-z0-9_-]{8,22}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
OPEN = (ClaimState.SUBMITTED, ClaimState.APPROVED, ClaimState.PAID)

# step -> (states it may be taken from, state it leads to)
PAYER_STEPS = {
    "approve": ((ClaimState.SUBMITTED,), ClaimState.APPROVED),
    "decline": ((ClaimState.SUBMITTED,), ClaimState.DECLINED),
    "pay": ((ClaimState.APPROVED,), ClaimState.PAID),
}


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _event(claim: Claim, what: str, side: str, actor: str) -> None:
    last = ClaimEvent.objects.filter(claim=claim, **_scope()).order_by("-seq").first()
    ClaimEvent.objects.create(
        claim=claim,
        seq=(last.seq + 1) if last else 1,
        what=what,
        side=side,
        actor_ref=actor,
        **_scope(),
    )


def _audit(actor: str, action: str, claim: Claim) -> None:
    record(
        actor_ref=actor,
        action=action,
        object_type="claim",
        object_ref=claim.public_id,
        outcome=AuditOutcome.ALLOWED,
    )


def _member_id(claim: Claim) -> str:
    try:
        return decrypt(
            claim.member_enc, tenant_id=claim.tenant_id, context=f"claim:{claim.public_id}:member"
        )
    except CryptoError:
        return ""


def clinic_dict(c: Claim) -> dict[str, Any]:
    """What the sending clinic sees: its own patient link, the member id only by its last four."""
    return {
        "id": c.public_id,
        "claim_no": c.claim_no,
        "payer": c.to_name,
        "payer_ref": c.to_tenant_ref,
        "patient": c.person_ref,
        "member_tail": c.member_tail,
        "category": c.category,
        "label": c.label,
        "amount_minor": c.amount_minor,
        "currency": c.currency,
        "service_date": c.service_date.isoformat(),
        "state": c.state,
        "consent": c.consent_state,
        "decision_reason": c.decision_reason or None,
        "payment_ref": c.payment_ref or None,
        "amount_paid_minor": c.amount_paid_minor,
        "paid_on": c.paid_on.isoformat() if c.paid_on else None,
        "remittance_advice": c.remittance_advice or None,
        "attachments": [attachment_dict(a) for a in ClaimAttachment.objects.filter(claim=c, **_scope()).order_by("created_at")],
        "created_at": c.created_at.isoformat(),
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "paid_at": c.paid_at.isoformat() if c.paid_at else None,
    }


def payer_dict(c: Claim) -> dict[str, Any]:
    """What the payer sees: member id, service, amount and date. Never the clinic's patient."""
    return {
        "id": c.public_id,
        "claim_no": c.claim_no,
        "from": c.from_name,
        "member_id": _member_id(c),
        "category": c.category,
        "label": c.label,
        "amount_minor": c.amount_minor,
        "currency": c.currency,
        "service_date": c.service_date.isoformat(),
        "state": c.state,
        "decision_reason": c.decision_reason or None,
        "payment_ref": c.payment_ref or None,
        "amount_paid_minor": c.amount_paid_minor,
        "paid_on": c.paid_on.isoformat() if c.paid_on else None,
        "remittance_advice": c.remittance_advice or None,
        "attachments": [attachment_dict(a) for a in ClaimAttachment.objects.filter(claim=c, **_scope()).order_by("created_at")],
        "created_at": c.created_at.isoformat(),
    }


def attachment_dict(a: ClaimAttachment) -> dict[str, Any]:
    return {
        "id": a.public_id,
        "filename": a.filename,
        "content_type": a.content_type,
        "size": a.size,
        "sha256": a.sha256,
        "scan": a.scan_state,
        "uploaded_by_side": a.uploaded_by_side,
        "created_at": a.created_at.isoformat(),
    }


def _detect_type(data: bytes, filename: str) -> tuple[str, str]:
    if not isinstance(filename, str) or not filename.strip():
        raise _bad("filename")
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_ATTACHMENT_BYTES:
        raise _bad("attachment")
    if data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]:
        if _ACTIVE_PDF.search(data):
            raise _bad("attachment")
        ctype = "application/pdf"
        ext = ".pdf"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        ctype = "image/png"
        ext = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        ctype = "image/jpeg"
        ext = ".jpg"
    else:
        raise _bad("attachment")

    name = (
        _SAFE_NAME.sub("_", filename.strip())[:120]
        if isinstance(filename, str) and filename.strip()
        else ""
    )
    if not name:
        name = f"attachment{ext}"
    elif not name.lower().endswith(ext):
        name = f"{name[:116]}{ext}"
    return ctype, name


def _apply_scan(a: ClaimAttachment, data: bytes, clock: Clock | None = None) -> None:
    from foundation import scanner

    verdict = scanner.scan(data)
    if verdict == "pending":
        return
    a.scan_state = ClaimScanState.CLEAN if verdict == "clean" else ClaimScanState.INFECTED
    a.scanned_at = utc_now(clock)
    if a.scan_state == ClaimScanState.INFECTED:
        a.content_enc = ""
    a.entity_version += 1
    a.save(
        update_fields=[
            "scan_state",
            "scanned_at",
            "content_enc",
            "entity_version",
            "updated_at",
        ]
    )
    _audit("system", f"claim_attachment_{a.scan_state}", a.claim)


def add_attachment(
    *,
    claim_ref: str,
    tenant_ref: str,
    filename: str,
    data: bytes,
    actor_ref: str,
    side: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Attach a document or image to a claim (DEV-378).
    Clinic attaches supporting documents to submitted claims.
    Payer attaches remittance advice documents when approving/paying.
    """
    import base64
    import hashlib

    ctype, name = _detect_type(data, filename)
    with transaction.atomic():
        claim = Claim.objects.select_for_update().filter(public_id=claim_ref, **_scope()).first()
        if claim is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if side == "clinic":
            if claim.from_tenant_ref != tenant_ref:
                raise ZoikoError(ErrorCode.NOT_FOUND)
            if claim.state != ClaimState.SUBMITTED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        elif side == "payer":
            if claim.to_tenant_ref != tenant_ref:
                raise ZoikoError(ErrorCode.NOT_FOUND)
            if claim.consent_state != ClaimConsent.GRANTED:
                raise ZoikoError(ErrorCode.NOT_FOUND)
            if claim.state not in (ClaimState.APPROVED, ClaimState.PAID):
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        else:
            raise ZoikoError(ErrorCode.POLICY_DENIED)

        if ClaimAttachment.objects.filter(claim=claim, **_scope()).count() >= MAX_ATTACHMENTS_PER_CLAIM:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)

        a = ClaimAttachment(
            claim=claim,
            filename=name,
            content_type=ctype,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_enc="",
            uploaded_by_side=side,
            uploaded_by_ref=actor_ref,
            **_scope(),
        )
        a.content_enc = encrypt(
            base64.b64encode(data).decode("ascii"),
            tenant_id=require_context().tenant_id,
            context=f"claim:{claim.public_id}:attachment:{a.public_id}",
        )
        a.save()
        _event(claim, "attachment_added", side, actor_ref)
        _audit(actor_ref, "claim.attachment_added", claim)
        _apply_scan(a, data, clock)
        return attachment_dict(a)


def remove_attachment(
    *, claim_ref: str, attachment_ref: str, tenant_ref: str, actor_ref: str
) -> None:
    """The clinic removes a supporting attachment while the claim is still submitted."""
    with transaction.atomic():
        claim = Claim.objects.select_for_update().filter(public_id=claim_ref, **_scope()).first()
        if claim is None or claim.from_tenant_ref != tenant_ref:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if claim.state != ClaimState.SUBMITTED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        a = ClaimAttachment.objects.filter(
            public_id=attachment_ref, claim=claim, **_scope()
        ).first()
        if a is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if a.uploaded_by_side != "clinic":
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        a.delete()
        _event(claim, "attachment_removed", "clinic", actor_ref)
        _audit(actor_ref, "claim.attachment_removed", claim)


def open_attachment(
    *, claim_ref: str, attachment_ref: str, tenant_ref: str, actor_ref: str
) -> tuple[str, str, bytes]:
    """Download a clean attachment for either the sending clinic or the adjudicating payer."""
    import base64

    claim = Claim.objects.filter(public_id=claim_ref, **_scope()).first()
    if claim is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if claim.from_tenant_ref != tenant_ref:
        if claim.to_tenant_ref != tenant_ref or claim.consent_state != ClaimConsent.GRANTED:
            raise ZoikoError(ErrorCode.NOT_FOUND)

    a = ClaimAttachment.objects.filter(
        public_id=attachment_ref, claim=claim, **_scope()
    ).first()
    if a is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if a.scan_state != ClaimScanState.CLEAN:
        if a.scan_state == ClaimScanState.INFECTED:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED)
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)

    try:
        data = base64.b64decode(
            decrypt(
                a.content_enc,
                tenant_id=require_context().tenant_id,
                context=f"claim:{claim.public_id}:attachment:{a.public_id}",
            )
        )
    except (CryptoError, ValueError):
        raise ZoikoError(ErrorCode.NOT_FOUND) from None
    _audit(actor_ref, "claim.attachment_opened", claim)
    return a.filename, a.content_type, data


def scan_pending_attachments(limit: int = 20, clock: Clock | None = None) -> int:
    import base64

    done = 0
    for a in (
        ClaimAttachment.objects.select_related("claim")
        .filter(scan_state=ClaimScanState.PENDING, **_scope())
        .order_by("created_at")[:limit]
    ):
        try:
            data = base64.b64decode(
                decrypt(
                    a.content_enc,
                    tenant_id=require_context().tenant_id,
                    context=f"claim:{a.claim.public_id}:attachment:{a.public_id}",
                )
            )
        except (CryptoError, ValueError):
            continue
        with transaction.atomic():
            _apply_scan(a, data, clock)
        done += a.scan_state != ClaimScanState.PENDING
    return done


def send(
    *,
    from_tenant_ref: str,
    from_name: str,
    to_tenant_ref: str,
    to_name: str,
    person_ref: Any,
    member_id: Any,
    category: Any,
    label: Any,
    amount_minor: Any,
    currency: Any,
    service_date: Any,
    actor_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    if not isinstance(person_ref, str) or not _PERSON.match(person_ref):
        raise _bad("patient")
    if not isinstance(member_id, str) or not _MEMBER.match(member_id):
        raise _bad("member_id")
    if not isinstance(category, str) or not _CATEGORY.match(category):
        raise _bad("category")
    if not isinstance(label, str) or not _LABEL.match(label) or contains_nhs_number(label):
        raise _bad("label")  # a short plain description; never a patient number
    if (
        not isinstance(amount_minor, int)
        or isinstance(amount_minor, bool)
        or not 1 <= amount_minor <= MAX_AMOUNT_MINOR
    ):
        raise _bad("amount")
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise _bad("currency")
    if not isinstance(service_date, str) or len(service_date) != 10:
        raise _bad("service_date")
    try:
        day = dt.date.fromisoformat(service_date)
    except ValueError:
        raise _bad("service_date") from None
    if not dt.date(1900, 1, 1) <= day <= utc_now(clock).date() + dt.timedelta(days=1):
        raise _bad("service_date")
    with transaction.atomic():
        base = Claim.objects.filter(from_tenant_ref=from_tenant_ref, **_scope())
        if (
            base.filter(to_tenant_ref=to_tenant_ref, state=ClaimState.SUBMITTED).count()
            >= MAX_OPEN_TO_ONE_PAYER
        ):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if base.filter(
            person_ref=person_ref, category=category, service_date=day, state__in=OPEN
        ).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # the same claim is already open
        claim = Claim(
            claim_no="CLM-" + secrets.token_hex(4).upper(),
            from_tenant_ref=from_tenant_ref,
            from_name=from_name,
            to_tenant_ref=to_tenant_ref,
            to_name=to_name,
            person_ref=person_ref,
            member_enc="",
            member_tail=member_id[-4:],
            category=category,
            label=label,
            amount_minor=amount_minor,
            currency=currency,
            service_date=day,
            created_by_ref=actor_ref,
            **_scope(),
        )
        claim.member_enc = encrypt(
            member_id,
            tenant_id=require_context().tenant_id,
            context=f"claim:{claim.public_id}:member",
        )
        claim.save()
        _event(claim, "sent", "clinic", actor_ref)
        _audit(actor_ref, "claim.send", claim)
        return clinic_dict(claim)


def list_sent(*, tenant_ref: str) -> list[dict[str, Any]]:
    rows = Claim.objects.filter(from_tenant_ref=tenant_ref, **_scope()).order_by("-created_at")
    return [clinic_dict(c) for c in rows[:200]]


def list_incoming(*, tenant_ref: str, state: str | None = None) -> list[dict[str, Any]]:
    rows = Claim.objects.filter(
        to_tenant_ref=tenant_ref, consent_state=ClaimConsent.GRANTED, **_scope()
    )
    if state:
        if state not in ClaimState.values:
            raise _bad("state")
        rows = rows.filter(state=state)
    return [payer_dict(c) for c in rows.order_by("-created_at")[:200]]


def page_sent(
    *, tenant_ref: str, limit: int, cursor: str | None, actor_ref: str
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the clinic's claims, newest first, and the cursor for the next (DEV-314)."""
    found, nxt = pagination.page(
        Claim.objects.filter(from_tenant_ref=tenant_ref, **_scope()),
        limit=limit,
        cursor=cursor,
        actor=actor_ref,
        lst="claim.sent",
    )
    return [clinic_dict(c) for c in found], nxt


def page_incoming(
    *, tenant_ref: str, state: str | None, limit: int, cursor: str | None, actor_ref: str
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the claims the patient has agreed to send to this payer (DEV-314)."""
    rows = Claim.objects.filter(
        to_tenant_ref=tenant_ref, consent_state=ClaimConsent.GRANTED, **_scope()
    )
    if state:
        if state not in ClaimState.values:
            raise _bad("state")
        rows = rows.filter(state=state)
    found, nxt = pagination.page(
        rows, limit=limit, cursor=cursor, actor=actor_ref, lst="claim.incoming"
    )
    return [payer_dict(c) for c in found], nxt


def person_of(claim_ref: str, tenant_ref: str) -> str:
    """The clinic's patient on a claim it sent, so the clinic can check its own policy."""
    row = Claim.objects.filter(public_id=claim_ref, from_tenant_ref=tenant_ref, **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row.person_ref


def withdraw(*, claim_ref: str, tenant_ref: str, actor_ref: str) -> dict[str, Any]:
    """The clinic takes back a claim the payer has not yet decided."""
    with transaction.atomic():
        row = (
            Claim.objects.select_for_update()
            .filter(public_id=claim_ref, from_tenant_ref=tenant_ref, **_scope())
            .first()
        )
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state == ClaimState.WITHDRAWN:
            return clinic_dict(row)  # repeating it is harmless
        if row.state != ClaimState.SUBMITTED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.state = ClaimState.WITHDRAWN
        row.entity_version += 1
        row.save()
        _event(row, "withdraw", "clinic", actor_ref)
        _audit(actor_ref, "claim.withdraw", row)
        return clinic_dict(row)


def member_of(claim_ref: str, tenant_ref: str) -> dict[str, str]:
    """The member id and kind of service on a claim sent to this payer, so the payer can run its
    own cover check. Only the payer it was sent to can ask."""
    row = Claim.objects.filter(
        public_id=claim_ref,
        to_tenant_ref=tenant_ref,
        consent_state=ClaimConsent.GRANTED,
        **_scope(),
    ).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return {
        "member_id": _member_id(row),
        "category": row.category,
        "service_date": row.service_date.isoformat(),
    }


def payer_step(
    *,
    claim_ref: str,
    tenant_ref: str,
    step: str,
    actor_ref: str,
    reason: Any = "",
    payment_ref: Any = "",
    clock: Clock | None = None,
    amount_paid_minor: Any = None,
    paid_on: Any = None,
    remittance_advice: Any = None,
) -> dict[str, Any]:
    if step not in PAYER_STEPS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    allowed_from, target = PAYER_STEPS[step]
    if step == "decline" and reason not in DeclineReason.values:
        raise _bad("reason")
    if step == "pay" and (not isinstance(payment_ref, str) or not _PAYMENT.match(payment_ref)):
        raise _bad("payment_ref")
    now = utc_now(clock)
    paid_day = None
    if step == "pay":
        # Payment advice (DEV-347): the day paid, never in the future; today when not given.
        if paid_on in (None, ""):
            paid_day = now.date()
        else:
            try:
                paid_day = dt.date.fromisoformat(paid_on) if isinstance(paid_on, str) else None
            except ValueError:
                paid_day = None
            if paid_day is None or paid_day > now.date():
                raise _bad("paid_on")
        if amount_paid_minor is not None and (
                isinstance(amount_paid_minor, bool) or not isinstance(amount_paid_minor, int)
                or amount_paid_minor < 0):
            raise _bad("amount_paid_minor")
        if remittance_advice is not None and (
                not isinstance(remittance_advice, str) or len(remittance_advice) > 500):
            raise _bad("remittance_advice")
    with transaction.atomic():
        row = (
            Claim.objects.select_for_update()
            .filter(public_id=claim_ref, to_tenant_ref=tenant_ref, **_scope())
            .first()
        )
        if row is None or row.consent_state != ClaimConsent.GRANTED:
            raise ZoikoError(ErrorCode.NOT_FOUND)  # until the patient agrees, it is not theirs
        if row.state == target:
            return payer_dict(row)  # repeating a step is harmless
        if row.state not in allowed_from:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.state = target
        if step in ("approve", "decline"):
            row.decided_by_ref, row.decided_at = actor_ref, now
        if step == "decline":
            row.decision_reason = reason
        if step == "pay":
            paid = row.amount_minor if amount_paid_minor is None else amount_paid_minor
            if paid > row.amount_minor:
                raise _bad("amount_paid_minor")  # never more than claimed
            row.payment_ref, row.paid_at = payment_ref, now
            row.amount_paid_minor, row.paid_on = paid, paid_day
            if remittance_advice is not None:
                row.remittance_advice = remittance_advice.strip()
        row.entity_version += 1
        row.save()
        _event(row, step, "payer", actor_ref)
        _audit(actor_ref, f"claim.{step}", row)
        return payer_dict(row)


def history(*, claim_ref: str, tenant_ref: str) -> list[dict[str, Any]]:
    """The steps of a claim, for either party. Who did it is shown as a side, not a name."""
    row = Claim.objects.filter(public_id=claim_ref, **_scope()).first()
    if row is None or tenant_ref not in (row.from_tenant_ref, row.to_tenant_ref):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if tenant_ref == row.to_tenant_ref and row.consent_state != ClaimConsent.GRANTED:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    events = ClaimEvent.objects.filter(claim=row, **_scope()).order_by("seq")
    return [{"what": e.what, "side": e.side, "at": e.created_at.isoformat()} for e in events]


def payer_counts(tenant_ref: str) -> dict[str, int]:
    base = Claim.objects.filter(
        to_tenant_ref=tenant_ref, consent_state=ClaimConsent.GRANTED, **_scope()
    )
    return {
        "to_decide": base.filter(state=ClaimState.SUBMITTED).count(),
        "to_pay": base.filter(state=ClaimState.APPROVED).count(),
    }


def clinic_counts(tenant_ref: str) -> dict[str, int]:
    base = Claim.objects.filter(from_tenant_ref=tenant_ref, **_scope())
    return {
        "waiting": base.filter(state=ClaimState.SUBMITTED).count(),
        "declined": base.filter(state=ClaimState.DECLINED).count(),
    }


def involving(customer_ref: str) -> int:
    """How many claims are still open between this customer and anyone else."""
    return Claim.objects.filter(
        Q(from_tenant_ref=customer_ref) | Q(to_tenant_ref=customer_ref),
        state__in=(ClaimState.SUBMITTED, ClaimState.APPROVED),
        **_scope(),
    ).count()


# ------------------------------------------------ the patient's agreement, claim by claim (DEV-311)
def patient_dict(c: Claim) -> dict[str, Any]:
    """What the patient sees about a claim made for them: what is being claimed, from whom and
    for how much, so they know exactly what the payer would be told."""
    return {
        "id": c.public_id,
        "payer": c.to_name,
        "label": c.label,
        "category": c.category,
        "amount_minor": c.amount_minor,
        "currency": c.currency,
        "service_date": c.service_date.isoformat(),
        "state": c.state,
        "consent": c.consent_state,
        "person": c.person_ref,
        "created_at": c.created_at.isoformat(),
    }


def list_for_patients(
    *, tenant_ref: str, person_refs: list[str], page: pagination.PageRequest | None = None
) -> list[dict[str, Any]]:
    rows = Claim.objects.filter(
        from_tenant_ref=tenant_ref, person_ref__in=person_refs, **_scope()
    )
    return [patient_dict(c) for c in pagination.apply(rows, page)]


def answer(
    *,
    claim_ref: str,
    tenant_ref: str,
    person_refs: list[str],
    step: str,
    actor_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """The patient (or a guardian) answers: agree (the payer may then see member id, service,
    amount and date), decline (the claim is cancelled), or withdraw an agreement (the claim is
    cancelled unless the payer has already decided it)."""
    if step not in ("agree", "decline", "withdraw"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with transaction.atomic():
        row = (
            Claim.objects.select_for_update()
            .filter(
                public_id=claim_ref,
                from_tenant_ref=tenant_ref,
                person_ref__in=person_refs,
                **_scope(),
            )
            .first()
        )
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        now = utc_now(clock)
        if step == "agree":
            if row.consent_state == ClaimConsent.GRANTED:
                return patient_dict(row)
            if row.consent_state != ClaimConsent.PENDING or row.state != ClaimState.SUBMITTED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            row.consent_state, row.consent_by_ref, row.consent_at = (
                ClaimConsent.GRANTED,
                actor_ref,
                now,
            )
        elif step == "decline":
            if row.consent_state == ClaimConsent.DECLINED:
                return patient_dict(row)
            if row.consent_state != ClaimConsent.PENDING or row.state != ClaimState.SUBMITTED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            row.consent_state, row.consent_by_ref = ClaimConsent.DECLINED, actor_ref
            row.state = ClaimState.WITHDRAWN
        else:
            if row.consent_state == ClaimConsent.WITHDRAWN:
                return patient_dict(row)
            if row.consent_state != ClaimConsent.GRANTED or row.state != ClaimState.SUBMITTED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # decided already: too late
            row.consent_state, row.consent_by_ref = ClaimConsent.WITHDRAWN, actor_ref
            row.state = ClaimState.WITHDRAWN
        row.entity_version += 1
        row.save()
        _event(row, f"consent_{step}", "patient", actor_ref)
        _audit(actor_ref, f"claim.consent_{step}", row)
        return patient_dict(row)


def announce_consent_needed(*, claim_ref: str, person_ref: str) -> None:
    """Tell the clinic's own space that a claim is waiting for the patient's agreement, so the
    patient can be told (DEV-313). Runs with the CLINIC's tenant bound and inside its
    transaction: references only."""
    emit(
        event_type="claim.consent_needed.v1",
        aggregate_type="claim",
        aggregate_ref=claim_ref,
        payload={"claim_ref": claim_ref, "person_ref": person_ref},
    )
