"""Fees, invoices and credit notes (docs GHE-17 WF-09, WF-10, WF-16): a clinic's billing RECORDS.

No money moves here and no card or bank detail is ever held: the specification says funds, cards
and payouts belong to licensed partners. A clinic records that it was paid (with the reference of a
payment it took elsewhere); the system keeps the ledger. Amounts are whole minor units (cents),
never decimals. There is no tax handling (DEV-139).
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    PAID = "paid", "Marked as paid"
    VOID = "void", "Cancelled"


class FeeItem(CellScopedModel):
    """The price of one service at one clinic from a date. A new price is a new row: old invoices
    keep the price they were made with."""

    organization_ref = models.CharField(max_length=22)
    service_ref = models.CharField(max_length=22)
    currency = models.CharField(max_length=3)
    amount_minor = models.PositiveIntegerField()
    effective_from = models.DateField()
    created_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="billing_fee_scope_idx"),
            models.Index(fields=["organization_ref", "service_ref", "effective_from"],
                         name="billing_fee_lookup_idx"),
        ]


class Invoice(CellScopedModel):
    organization_ref = models.CharField(max_length=22)
    person_ref = models.CharField(max_length=22)              # the patient it is addressed to
    booking_ref = models.CharField(max_length=22, blank=True, default="")
    number = models.CharField(max_length=14)                  # INV-000001, per customer
    currency = models.CharField(max_length=3)
    lines = models.JSONField(default=list)                    # service_ref, name, quantity, unit
    total_minor = models.PositiveBigIntegerField()
    status = models.CharField(max_length=8, choices=InvoiceStatus.choices,
                              default=InvoiceStatus.DRAFT)
    due_on = models.DateField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_ref = models.CharField(max_length=64, blank=True, default="")
    void_reason = models.CharField(max_length=24, blank=True, default="")
    created_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "number"], name="billing_number_unique"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="billing_inv_scope_idx"),
            models.Index(fields=["person_ref"], name="billing_inv_person_idx"),
            models.Index(fields=["organization_ref", "status"], name="billing_inv_org_idx"),
        ]


class CreditNote(CellScopedModel):
    """A reduction of an invoice, for a reason from a fixed list (never free text). Append-only. It
    records that the clinic reduced the bill; a refund is made by the clinic elsewhere."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=14)                  # CN-000001, per customer
    amount_minor = models.PositiveIntegerField()
    reason = models.CharField(max_length=24)
    created_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "number"], name="billing_credit_unique"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="billing_cn_scope_idx")]


class InvoiceEvent(CellScopedModel):
    """One step in an invoice's life, chained and append-only: created, issued, paid, cancelled,
    credit note. It holds no patient detail and no amount of money beyond what the invoice has."""

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="events")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=12)
    status_after = models.CharField(max_length=8)
    actor_ref = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["invoice", "seq"], name="billing_event_one_per_seq"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="billing_ev_scope_idx")]
