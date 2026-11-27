
"""A clinic's fees, invoices and credit notes (GHE-17 WF-09, WF-10, WF-16).

The rules (proposed, DEV-139):
* Prices come only from the clinic's fee schedule for a service; nobody types a price on an invoice.
  A new price is a new schedule entry from a date, so an old invoice keeps the price it had.
* An invoice has one currency, one to ten lines, each a service of the clinic and a quantity. It
  starts as a draft, is issued, then marked paid (with the reference of a payment the clinic took
  elsewhere) or cancelled. There is no free-text line, so no clinical content can reach a bill.
* Staff (R04) create and issue invoices and mark them paid; only the clinic administrator (R13) sets
  prices, cancels an issued or paid invoice, or writes a credit note. A credit note names a reason
  from a fixed list and can never take the invoice below zero.
* Every step is one chained, append-only event; credit notes cannot be changed or removed.
* A patient sees their own issued, paid and cancelled invoices, never drafts, and a guardian sees
  none (billing is a "records" class, which no guardian has been granted).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.db import IntegrityError, transaction

from foundation import pagination
from foundation.cell_context import require_context, tenant_transaction
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.catalog import api as catalog
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from . import payments
from .models import CreditNote, FeeItem, Invoice, InvoiceEvent, InvoiceStatus, PaymentAttempt
from .payments import FINAL, PaymentStatus

WRITE = "write:billing"
READ = "read:billing"
STAFF_ROLES = frozenset({"R04", "R13"})
ADMIN_ROLE = "R13"
MAX_LINES = 10
MAX_QUANTITY = 20
MAX_FEE_MINOR = 100_000_000                       # a million units of currency, in minor units
CREDIT_REASONS = ("error", "service_not_given", "goodwill", "other")
VOID_REASONS = ("error", "service_not_given", "duplicate", "other")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PAYMENT_REF = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _conflict() -> ZoikoError:
    return ZoikoError(ErrorCode.CONFLICT_VERSION)


def _receipt(decision: policy.Decision, actor: str, org: str, person: str | None = None) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=WRITE,
                         organization_public_id=org, subject_ref=person)
    if decision.role_code not in STAFF_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _admin(decision: policy.Decision) -> None:
    if decision.role_code != ADMIN_ROLE:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


# ------------------------------------------------------------------ the fee schedule
def fee_dict(f: FeeItem) -> dict[str, Any]:
    return {"public_id": f.public_id, "service_ref": f.service_ref, "currency": f.currency,
            "amount_minor": f.amount_minor, "effective_from": f.effective_from.isoformat()}


def set_fee(
    *, organization_ref: str, service_ref: str, currency: str, amount_minor: Any,
    effective_from: Any, actor_public_id: str, decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A clinic administrator sets what a service costs from a date (today or later)."""
    _receipt(decision, actor_public_id, organization_ref)
    _admin(decision)
    if not _CURRENCY.match(currency or ""):
        raise _bad("currency")
    if (isinstance(amount_minor, bool) or not isinstance(amount_minor, int)
            or not 0 <= amount_minor <= MAX_FEE_MINOR):
        raise _bad("amount_minor")
    today = utc_now(clock).date()
    try:
        start = dt.date.fromisoformat(str(effective_from)) if effective_from else today
    except ValueError:
        raise _bad("effective_from") from None
    if start < today or start > today + dt.timedelta(days=366):
        raise _bad("effective_from")
    svc = catalog.get_service(service_ref)
    if svc["organization_ref"] != organization_ref:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with transaction.atomic():
        row = FeeItem.objects.create(
            organization_ref=organization_ref, service_ref=service_ref, currency=currency,
            amount_minor=amount_minor, effective_from=start, created_by_ref=actor_public_id,
            **_scope())
        record(actor_ref=actor_public_id, action="billing.fee_set", object_type="fee",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        return fee_dict(row)


def current_fee(organization_ref: str, service_ref: str, today: dt.date) -> FeeItem | None:
    return (FeeItem.objects.filter(organization_ref=organization_ref, service_ref=service_ref,
                                   effective_from__lte=today, **_scope())
            .order_by("-effective_from", "-created_at").first())


def list_fees(organization_ref: str, clock: Clock | None = None) -> list[dict[str, Any]]:
    """The price in force today for each service that has one, and the next change if any."""
    today = utc_now(clock).date()
    services = {s["public_id"]: s["name"] for s in catalog.list_services(
        active_only=False, organization_ref=organization_ref)}
    out = []
    for ref, name in services.items():
        now = current_fee(organization_ref, ref, today)
        later = (FeeItem.objects.filter(organization_ref=organization_ref, service_ref=ref,
                                        effective_from__gt=today, **_scope())
                 .order_by("effective_from").first())
        out.append({"service_ref": ref, "name": name,
                    "current": fee_dict(now) if now else None,
                    "upcoming": fee_dict(later) if later else None})
    return sorted(out, key=lambda r: r["name"].lower())


# ------------------------------------------------------------------ invoices
def _credited(inv: Invoice) -> int:
    return sum(CreditNote.objects.filter(invoice=inv, **_scope()).values_list(
        "amount_minor", flat=True))


def invoice_dict(inv: Invoice, with_credits: bool = True) -> dict[str, Any]:
    credits = list(CreditNote.objects.filter(invoice=inv, **_scope()).order_by("created_at"))
    credited = sum(c.amount_minor for c in credits)
    return {
        "public_id": inv.public_id, "number": inv.number, "person_ref": inv.person_ref,
        "organization_ref": inv.organization_ref, "booking_ref": inv.booking_ref,
        "currency": inv.currency, "lines": inv.lines, "total_minor": inv.total_minor,
        "credited_minor": credited, "balance_minor": inv.total_minor - credited,
        "status": inv.status, "due_on": inv.due_on.isoformat() if inv.due_on else None,
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
        "payment_ref": inv.payment_ref, "void_reason": inv.void_reason,
        "created_at": inv.created_at.isoformat(),
        "credit_notes": [{"number": c.number, "amount_minor": c.amount_minor,
                          "reason": c.reason, "at": c.created_at.isoformat()}
                         for c in credits] if with_credits else [],
    }


def _event_parts(row: InvoiceEvent) -> list[Any]:
    return [row.invoice.public_id, row.kind, row.status_after, row.actor_ref,
            row.occurred_at.isoformat()]


def _log(inv: Invoice, kind: str, actor: str, now: dt.datetime) -> None:
    last = InvoiceEvent.objects.filter(invoice=inv, **_scope()).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    digest = chain_digest(prev, seq, inv.public_id, kind, inv.status, actor, now.isoformat())
    try:
        with transaction.atomic():
            InvoiceEvent.objects.create(
                invoice=inv, seq=seq, kind=kind, status_after=inv.status, actor_ref=actor,
                occurred_at=now, prev_digest=prev, record_digest=digest, **_scope())
    except IntegrityError:
        raise _conflict() from None


def _next_number(model: Any, prefix: str) -> str:
    last = model.objects.filter(**_scope()).order_by("-number").values_list(
        "number", flat=True).first()
    n = int(last.split("-")[1]) + 1 if last else 1
    return f"{prefix}-{n:06d}"


def _own(ref: str, organization_ref: str) -> Invoice:
    found = Invoice.objects.select_for_update().filter(
        public_id=ref, organization_ref=organization_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def create_invoice(
    *, organization_ref: str, person_ref: str, lines: Any, due_days: Any, booking_ref: str,
    actor_public_id: str, decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """A draft for a patient, priced from the clinic's fee schedule as it stands today."""
    _receipt(decision, actor_public_id, organization_ref, person_ref)
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise _bad("lines")
    if due_days in (None, ""):
        days = 14
    elif isinstance(due_days, bool) or not isinstance(due_days, int) or not 0 <= due_days <= 365:
        raise _bad("due_days")
    else:
        days = due_days
    now = utc_now(clock)
    today = now.date()
    priced: list[dict[str, Any]] = []
    currency = ""
    for item in lines:
        if (not isinstance(item, dict) or set(item) - {"service", "quantity"}
                or not isinstance(item.get("service"), str)):
            raise _bad("lines")
        qty = item.get("quantity", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QUANTITY:
            raise _bad("lines")
        svc = catalog.get_service(item["service"])
        if svc["organization_ref"] != organization_ref:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        fee = current_fee(organization_ref, item["service"], today)
        if fee is None:
            raise _bad("lines")                                  # no price has been set for it
        if currency and fee.currency != currency:
            raise _bad("lines")                                  # one currency to an invoice
        currency = fee.currency
        priced.append({"service_ref": item["service"], "name": svc["name"], "quantity": qty,
                       "unit_minor": fee.amount_minor})
    total = sum(p["quantity"] * p["unit_minor"] for p in priced)
    for _ in range(5):
        try:
            with transaction.atomic():
                inv = Invoice.objects.create(
                    organization_ref=organization_ref, person_ref=person_ref,
                    booking_ref=booking_ref or "", currency=currency, lines=priced,
                    total_minor=total, due_on=today + dt.timedelta(days=days),
                    number=_next_number(Invoice, "INV"), created_by_ref=actor_public_id,
                    **_scope())
                _log(inv, "created", actor_public_id, now)
                record(actor_ref=actor_public_id, action="billing.invoice_created",
                       object_type="invoice", object_ref=inv.public_id,
                       outcome=AuditOutcome.ALLOWED)
                return invoice_dict(inv)
        except IntegrityError:
            continue                                             # another invoice took the number
    raise _conflict()


def _record_paid(inv: Invoice, payment_ref: str, actor: str, now: dt.datetime) -> None:
    """THE one way an invoice becomes paid: by staff with a reference of a payment taken
    elsewhere, or by a payment provider's reference. Call inside a transaction with the invoice
    locked. Paying an invoice already paid is a harmless repeat."""
    if inv.status == InvoiceStatus.PAID:
        return
    if inv.status != InvoiceStatus.ISSUED:
        raise _conflict()
    if not _PAYMENT_REF.match(payment_ref or ""):
        raise _bad("payment_ref")
    if inv.total_minor - _credited(inv) <= 0:
        raise _conflict()                                        # nothing left to pay
    inv.status, inv.paid_at, inv.payment_ref = InvoiceStatus.PAID, now, payment_ref
    inv.entity_version += 1
    inv.save()
    _log(inv, "paid", actor, now)
    record(actor_ref=actor, action="billing.invoice_paid", object_type="invoice",
           object_ref=inv.public_id, outcome=AuditOutcome.ALLOWED)


def step(
    *, invoice_ref: str, organization_ref: str, action: str, payment_ref: str,
    reason: str, actor_public_id: str, decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """issue | pay | void. Issuing and paying are staff acts; cancelling is the administrator's."""
    _receipt(decision, actor_public_id, organization_ref)
    if action not in ("issue", "pay", "void"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    now = utc_now(clock)
    with transaction.atomic():
        inv = _own(invoice_ref, organization_ref)
        if action == "issue":
            if inv.status == InvoiceStatus.ISSUED:
                return invoice_dict(inv)
            if inv.status != InvoiceStatus.DRAFT:
                raise _conflict()
            inv.status, inv.issued_at, kind = InvoiceStatus.ISSUED, now, "issued"
        elif action == "pay":
            _record_paid(inv, payment_ref, actor_public_id, now)
            return invoice_dict(inv)
        else:
            _admin(decision)
            if inv.status == InvoiceStatus.VOID:
                return invoice_dict(inv)
            if reason not in VOID_REASONS:
                raise _bad("reason")
            if CreditNote.objects.filter(invoice=inv, **_scope()).exists():
                raise _conflict()                                # already reduced by a credit note
            inv.status, inv.void_reason, kind = InvoiceStatus.VOID, reason, "cancelled"
        inv.entity_version += 1
        inv.save()
        _log(inv, kind, actor_public_id, now)
        record(actor_ref=actor_public_id, action=f"billing.invoice_{kind}",
               object_type="invoice", object_ref=inv.public_id, outcome=AuditOutcome.ALLOWED)
        return invoice_dict(inv)


def credit_note(
    *, invoice_ref: str, organization_ref: str, amount_minor: Any, reason: str,
    actor_public_id: str, decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """The administrator reduces an issued or paid invoice. Never below zero."""
    _receipt(decision, actor_public_id, organization_ref)
    _admin(decision)
    if reason not in CREDIT_REASONS:
        raise _bad("reason")
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor < 1:
        raise _bad("amount_minor")
    now = utc_now(clock)
    with transaction.atomic():
        inv = _own(invoice_ref, organization_ref)
        if inv.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PAID):
            raise _conflict()
        if amount_minor > inv.total_minor - _credited(inv):
            raise _bad("amount_minor")
        for _ in range(5):
            try:
                with transaction.atomic():
                    CreditNote.objects.create(
                        invoice=inv, number=_next_number(CreditNote, "CN"),
                        amount_minor=amount_minor, reason=reason, created_by_ref=actor_public_id,
                        **_scope())
                break
            except IntegrityError:
                continue
        else:
            raise _conflict()
        inv.entity_version += 1
        inv.save(update_fields=["entity_version", "updated_at"])
        _log(inv, "credit_note", actor_public_id, now)
        record(actor_ref=actor_public_id, action="billing.credit_note", object_type="invoice",
               object_ref=inv.public_id, outcome=AuditOutcome.ALLOWED, reason_code=reason)
        return invoice_dict(inv)


# ------------------------------------------------------------------ reading
def list_invoices(
    organization_ref: str, status: str | None = None, limit: int = 100,
    page: pagination.PageRequest | None = None,
) -> list[dict[str, Any]]:
    rows = Invoice.objects.filter(organization_ref=organization_ref, **_scope())
    if status:
        if status not in InvoiceStatus.values:
            raise _bad("status")
        rows = rows.filter(status=status)
    shown = pagination.apply(rows, page, cap=min(limit, 200))
    return [invoice_dict(i, with_credits=False) for i in shown]


def get_invoice(invoice_ref: str, organization_ref: str) -> dict[str, Any]:
    inv = _own_read(invoice_ref, organization_ref)
    rows = list(InvoiceEvent.objects.select_related("invoice").filter(
        invoice=inv, **_scope()).order_by("seq"))
    ok, bad = verify_chain(rows, _event_parts)
    out = invoice_dict(inv)
    out["history"] = [{"seq": r.seq, "kind": r.kind, "status_after": r.status_after,
                       "actor": r.actor_ref, "at": r.occurred_at.isoformat()} for r in rows]
    out["chain_ok"], out["bad_seq"] = ok, bad
    return out


def _own_read(ref: str, organization_ref: str) -> Invoice:
    found = Invoice.objects.filter(public_id=ref, organization_ref=organization_ref,
                                   **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def patient_invoices(person_ref: str) -> list[dict[str, Any]]:
    """What a patient may see of their own bills: everything but drafts. The caller has authorized
    the read for exactly this person."""
    rows = Invoice.objects.filter(person_ref=person_ref, **_scope()).exclude(
        status=InvoiceStatus.DRAFT).order_by("-created_at")[:200]
    return [invoice_dict(i) for i in rows]


# ------------------------------------------------------------------ paying through a provider
# A switched-off skeleton (no provider has been chosen). The rules:
# * Only the patient who owes an issued invoice may start paying it, for the whole balance.
# * One attempt at a time per invoice: one still pending or unknown must be asked about first.
# * The same idempotency key gives back the same attempt and never asks the provider twice.
# * The provider is called OUTSIDE any database transaction; the attempt is written before and
#   after. If the first answer never arrives, asking again sends the same key, which the provider
#   must honour (payments.PaymentProvider), so there is never a second charge.
# * "succeeded" marks the invoice paid through _record_paid, the same path staff use, with the
#   provider's reference. "failed" and "unknown" leave the invoice open.
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_OPEN = (PaymentStatus.PENDING.value, PaymentStatus.UNKNOWN.value)


def payment_dict(a: PaymentAttempt, inv: Invoice) -> dict[str, Any]:
    return {
        "public_id": a.public_id, "invoice_ref": inv.public_id, "invoice_number": inv.number,
        "provider": a.provider, "provider_ref": a.provider_ref, "amount_minor": a.amount_minor,
        "currency": a.currency, "status": a.status, "attempts": a.attempts,
        "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat(),
    }


def _provider() -> payments.PaymentProvider:
    found = payments.active()
    if found is None:
        raise _bad("payment_provider")                   # payments are switched off here
    return found


def _ask_start(
    provider: payments.PaymentProvider, invoice_ref: str, amount: int, currency: str, key: str
) -> tuple[str, PaymentStatus]:
    try:
        ref, status = provider.start_payment(invoice_ref, amount, currency, key)
        status = PaymentStatus(status)
    except Exception:                                    # no answer we can trust: ask again later
        return "", PaymentStatus.UNKNOWN
    if ref and not _PAYMENT_REF.match(ref):
        return "", PaymentStatus.UNKNOWN
    return ref, status


def _ask_status(provider: payments.PaymentProvider, provider_ref: str) -> PaymentStatus:
    try:
        return PaymentStatus(provider.payment_status(provider_ref))
    except Exception:
        return PaymentStatus.UNKNOWN


def _apply_answer(
    attempt_ref: str, provider_ref: str, status: PaymentStatus, actor: str, now: dt.datetime
) -> dict[str, Any]:
    """Write what the provider said. A final answer (succeeded or failed) is never overwritten."""
    with tenant_transaction():
        invoice_id = PaymentAttempt.objects.filter(
            public_id=attempt_ref, **_scope()).values_list("invoice_id", flat=True).get()
        inv = Invoice.objects.select_for_update().get(pk=invoice_id, **_scope())  # invoice first
        a = PaymentAttempt.objects.select_for_update().get(public_id=attempt_ref, **_scope())
        before = a.status
        a.attempts += 1
        if provider_ref and not a.provider_ref:
            a.provider_ref = provider_ref
        if before not in {s.value for s in FINAL}:
            a.status = status.value
        a.entity_version += 1
        a.save()
        if a.status != before:
            record(actor_ref=actor, action=f"billing.payment_{a.status}", object_type="payment",
                   object_ref=a.public_id, outcome=AuditOutcome.ALLOWED, reason_code=a.provider)
        if a.status == PaymentStatus.SUCCEEDED and before != a.status:
            balance = inv.total_minor - _credited(inv)
            if inv.status == InvoiceStatus.ISSUED and balance == a.amount_minor and a.provider_ref:
                _record_paid(inv, a.provider_ref, actor, now)
            else:
                # The invoice changed while the payment was under way (cancelled, credited or paid
                # another way). It is left as it is for the clinic to put right; nothing is guessed.
                record(actor_ref=actor, action="billing.payment_unmatched", object_type="payment",
                       object_ref=a.public_id, outcome=AuditOutcome.FAILED,
                       reason_code="invoice_changed")
        return payment_dict(a, inv)


def start_payment(
    *, invoice_ref: str, person_ref: str, idempotency_key: str, actor_public_id: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """The patient who owes an issued invoice pays its balance through this deployment's provider.

    Refused (422, field payment_provider) when payments are switched off. The caller has
    authorized the patient's own billing for `person_ref`; an invoice addressed to anyone else, or
    still a draft, is not found. Runs its own transactions: do not call inside one."""
    if not person_ref:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    provider = _provider()
    if not _IDEMPOTENCY_KEY.match(idempotency_key or ""):
        raise _bad("idempotency_key")
    now = utc_now(clock)
    with tenant_transaction():
        inv = Invoice.objects.select_for_update().filter(
            public_id=invoice_ref, person_ref=person_ref, **_scope()).exclude(
            status=InvoiceStatus.DRAFT).first()
        if inv is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        earlier = PaymentAttempt.objects.filter(
            idempotency_key=idempotency_key, **_scope()).first()
        if earlier is not None:
            if earlier.invoice_id != inv.id:
                raise ZoikoError(ErrorCode.DUPLICATE_COMMAND)   # the key was used for another bill
            return {**payment_dict(earlier, inv), "replayed": True}
        balance = inv.total_minor - _credited(inv)
        if inv.status != InvoiceStatus.ISSUED or balance <= 0:
            raise _conflict()                                   # paid, cancelled or nothing owed
        if PaymentAttempt.objects.filter(invoice=inv, status__in=_OPEN, **_scope()).exists():
            raise _conflict()                                   # ask about the open one first
        try:
            with transaction.atomic():
                attempt = PaymentAttempt.objects.create(
                    invoice=inv, provider=provider.key, amount_minor=balance,
                    currency=inv.currency, status=PaymentStatus.PENDING.value,
                    idempotency_key=idempotency_key, started_by_ref=actor_public_id, **_scope())
        except IntegrityError:
            raise ZoikoError(ErrorCode.DUPLICATE_COMMAND) from None
        record(actor_ref=actor_public_id, action="billing.payment_started", object_type="payment",
               object_ref=attempt.public_id, outcome=AuditOutcome.ALLOWED,
               reason_code=provider.key)
    ref, status = _ask_start(provider, inv.public_id, balance, inv.currency, idempotency_key)
    return {**_apply_answer(attempt.public_id, ref, status, actor_public_id, now),
            "replayed": False}


def refresh_payment(
    *, payment_ref: str, person_ref: str, actor_public_id: str, clock: Clock | None = None,
) -> dict[str, Any]:
    """Ask the provider again about an attempt still pending or unknown. A final one is returned as
    it is. Only the patient who owes the invoice may ask. Runs its own transactions."""
    if not person_ref:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    now = utc_now(clock)
    with tenant_transaction():
        a = PaymentAttempt.objects.filter(public_id=payment_ref, **_scope()).first()
        inv = Invoice.objects.filter(pk=a.invoice_id, **_scope()).first() if a else None
        if a is None or inv is None or inv.person_ref != person_ref:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if a.status not in _OPEN:
            return payment_dict(a, inv)
    provider = _provider()
    if provider.key != a.provider:
        raise _bad("payment_provider")                  # started with a provider no longer in use
    if a.provider_ref:
        ref, status = "", _ask_status(provider, a.provider_ref)
    else:                                               # the first answer never came: same key
        ref, status = _ask_start(provider, inv.public_id, a.amount_minor, a.currency,
                                 a.idempotency_key)
    return _apply_answer(a.public_id, ref, status, actor_public_id, now)


def payer_payments(invoice_ref: str, person_ref: str) -> list[dict[str, Any]]:
    """The attempts to pay one of the patient's own invoices, newest first."""
    inv = Invoice.objects.filter(public_id=invoice_ref, person_ref=person_ref, **_scope()).exclude(
        status=InvoiceStatus.DRAFT).first()
    if inv is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    rows = PaymentAttempt.objects.filter(invoice=inv, **_scope()).order_by("-created_at")[:50]
    return [payment_dict(a, inv) for a in rows]


def list_payments(
    organization_ref: str,
    invoice_ref: str | None = None,
    page: pagination.PageRequest | None = None,
) -> list[dict[str, Any]]:
    """For clinic staff: attempts to pay this clinic's invoices (optionally one invoice)."""
    rows = PaymentAttempt.objects.select_related("invoice").filter(
        invoice__organization_ref=organization_ref, **_scope())
    if invoice_ref:
        rows = rows.filter(invoice__public_id=invoice_ref)
    return [payment_dict(a, a.invoice) for a in pagination.apply(rows, page)]
