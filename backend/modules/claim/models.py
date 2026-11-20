"""Claims from a clinic to a payer, in Zoiko's platform space (DEV-308, LEAD DECISION).

A clinic and a payer are separate customers and cannot see each other's data, so a claim lives here,
where the two meet, like the exchange (DEV-163). The lead decided what the payer may see: the
member id the payer gave us, the service, the amount and the date, and nothing else: no patient
name, no diagnosis, no notes. The clinic's own patient link stays on the row for the clinic's own
checks and is never shown to the payer. The member id is stored sealed (encrypted), never in the
clear.
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class ClaimState(models.TextChoices):
    SUBMITTED = "submitted", "Sent to the payer"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    PAID = "paid", "Paid"
    WITHDRAWN = "withdrawn", "Withdrawn by the clinic"


class DeclineReason(models.TextChoices):
    """A fixed list, so no free text (and no clinical detail) can travel in a decision."""

    NOT_COVERED = "not_covered", "Not covered"
    MEMBER_NOT_FOUND = "member_not_found", "Member not found"
    DUPLICATE = "duplicate", "Duplicate of another claim"
    MISSING_INFORMATION = "missing_information", "Information missing"
    OTHER = "other", "Other"


class ClaimConsent(models.TextChoices):
    """The patient's agreement to this one claim being sent to the payer (DEV-311)."""

    PENDING = "pending", "Waiting for the patient"
    GRANTED = "granted", "The patient agreed"
    DECLINED = "declined", "The patient said no"
    WITHDRAWN = "withdrawn", "The patient took it back"


MAX_AMOUNT_MINOR = 100_000_000  # 1,000,000.00 in minor units: a sanity limit, not a rule
MAX_OPEN_TO_ONE_PAYER = 500


class Claim(CellScopedModel):
    claim_no = models.CharField(max_length=14)  # CLM-XXXXXXXX
    from_tenant_ref = models.CharField(max_length=22)  # the clinic
    from_name = models.CharField(max_length=120)
    to_tenant_ref = models.CharField(max_length=22)  # the payer
    to_name = models.CharField(max_length=120)
    person_ref = models.CharField(max_length=22)  # the clinic's patient (clinic only)
    member_enc = models.TextField()  # the payer's member id, sealed
    member_tail = models.CharField(max_length=4)  # its last four characters
    category = models.CharField(max_length=40)  # kind of service, as plans list it
    label = models.CharField(max_length=80)  # the service in plain words
    amount_minor = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    service_date = models.DateField()
    state = models.CharField(
        max_length=10, choices=ClaimState.choices, default=ClaimState.SUBMITTED
    )
    consent_state = models.CharField(
        max_length=10, choices=ClaimConsent.choices, default=ClaimConsent.PENDING
    )
    consent_by_ref = models.CharField(max_length=22, blank=True, default="")
    consent_at = models.DateTimeField(null=True, blank=True)
    created_by_ref = models.CharField(max_length=22)
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
    decision_reason = models.CharField(
        max_length=20, choices=DeclineReason.choices, blank=True, default=""
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    payment_ref = models.CharField(max_length=40, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="claim_scope_idx"),
            models.Index(fields=["from_tenant_ref", "state"], name="claim_from_idx"),
            models.Index(fields=["to_tenant_ref", "state"], name="claim_to_idx"),
        ]


class ClaimEvent(CellScopedModel):
    """What happened to a claim and on which side: the history both parties may read."""

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="+")
    seq = models.PositiveIntegerField()
    what = models.CharField(max_length=20)
    side = models.CharField(max_length=8)  # "clinic" or "payer"
    actor_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["claim", "seq"], name="claim_event_seq")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="claim_event_scope_idx")]
