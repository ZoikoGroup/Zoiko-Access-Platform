"""The status exchange between separate customers (TEMPORARY proposal, DEV-163).

Customers are kept strictly apart, so a clinic and a lab cannot see each other's data. This is the
one place they meet, and it holds almost nothing: one row per request, in Zoiko's own platform
space, carrying who it is from and to, what kind of request, an opaque order number the clinic
chose, and where it stands. NO patient name, NO clinical text and NO result: a result stays with
the partner and only its reference and the fact that it is ready travel here. Each side reads only
the rows that name it.

States follow the specification's own wording (GHE-13 T-57): requested, accepted, in progress,
result available, then clinician reviewed. "Result available" is delivery, never review.
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Kind(models.TextChoices):
    LAB_ORDER = "lab_order", "Laboratory or imaging order"
    PHARMACY_REQUEST = "pharmacy_request", "Pharmacy request"
    HOMECARE_REFERRAL = "homecare_referral", "Home-care referral"


# Which partner role may answer which kind of request.
KIND_ROLE = {Kind.LAB_ORDER: "R05", Kind.PHARMACY_REQUEST: "R06", Kind.HOMECARE_REFERRAL: "R09"}


class State(models.TextChoices):
    REQUESTED = "requested", "Sent, waiting for the partner"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    IN_PROGRESS = "in_progress", "In progress"
    RESULT_AVAILABLE = "result_available", "Result available"
    REVIEWED = "reviewed", "Reviewed by a clinician"
    CANCELLED = "cancelled", "Cancelled by the clinic"


class Urgency(models.TextChoices):
    ROUTINE = "routine", "Routine"
    URGENT = "urgent", "Urgent"


class ExchangeRequest(CellScopedModel):
    ref_no = models.CharField(max_length=14)  # XCH-XXXXXXXX
    kind = models.CharField(max_length=20, choices=Kind.choices)
    from_tenant_ref = models.CharField(max_length=22)  # public id of the sending customer
    from_name = models.CharField(max_length=120)
    to_tenant_ref = models.CharField(max_length=22)  # public id of the receiving partner
    to_name = models.CharField(max_length=120)
    subject_token = models.CharField(max_length=40)  # the clinic's own order number
    person_ref = models.CharField(max_length=22)  # the clinic's patient (clinic only)
    label = models.CharField(max_length=80)  # what is asked, in plain words
    urgency = models.CharField(max_length=10, choices=Urgency.choices, default=Urgency.ROUTINE)
    state = models.CharField(max_length=20, choices=State.choices, default=State.REQUESTED)
    created_by_ref = models.CharField(max_length=22)
    responded_by_ref = models.CharField(max_length=22, blank=True, default="")
    reviewed_by_ref = models.CharField(max_length=22, blank=True, default="")
    decision_note = models.CharField(max_length=200, blank=True, default="")  # why declined
    result_ref = models.CharField(max_length=40, blank=True, default="")  # partner's own ref
    accepted_at = models.DateTimeField(null=True, blank=True)
    result_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Set when the other customer was suspended while this was still open (DEV-169): the request
    # waits for a person on the surviving side to cancel it or for the suspension to be lifted.
    on_hold_at = models.DateTimeField(null=True, blank=True)
    # The person who must decide what to do with a held request: whoever sent it (DEV-170).
    on_hold_owner_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="exchange_scope_idx"),
            models.Index(fields=["from_tenant_ref", "state"], name="exchange_from_idx"),
            models.Index(fields=["to_tenant_ref", "state"], name="exchange_to_idx"),
        ]


class ExchangeEvent(CellScopedModel):
    """What happened to a request, who did it and on which side: the history both sides may read."""

    request = models.ForeignKey(ExchangeRequest, on_delete=models.CASCADE, related_name="+")
    seq = models.PositiveIntegerField()
    what = models.CharField(max_length=20)
    side = models.CharField(max_length=8)  # "clinic" or "partner"
    actor_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["request", "seq"], name="exchange_event_seq")
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="exchange_event_scope_idx")]
