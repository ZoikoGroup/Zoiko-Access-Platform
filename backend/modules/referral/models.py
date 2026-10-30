"""A doctor's referral of a patient to a service, and the receiving clinic's decision.

Services, organizations, doctors and patients belong to other modules: referenced by public id.
The docs keep every receiving decision as its own append-only row; here the row holds the LATEST
decision and the audit chain keeps the history (DEV-039).
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class ReferralState(models.TextChoices):
    ISSUED = "issued", "Sent"
    ACCEPTED = "accepted", "Accepted"
    NEEDS_INFO = "needs_info", "More information needed"
    DECLINED = "declined", "Declined"


OPEN_STATES = (ReferralState.ISSUED, ReferralState.NEEDS_INFO)


class Urgency(models.TextChoices):
    ROUTINE = "routine", "Routine"
    URGENT = "urgent", "Urgent"


class Referral(CellScopedModel):
    person_ref = models.CharField(max_length=22)          # the patient
    referrer_ref = models.CharField(max_length=22)        # the doctor (trust Actor.public_id)
    from_organization_ref = models.CharField(max_length=22)
    service_ref = models.CharField(max_length=22)         # where the patient is sent
    destination_organization_ref = models.CharField(max_length=22)   # copied from the service
    reason = models.CharField(max_length=500)
    urgency = models.CharField(max_length=16, choices=Urgency.choices, default=Urgency.ROUTINE)
    state = models.CharField(
        max_length=16, choices=ReferralState.choices, default=ReferralState.ISSUED)
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
    decision_reason = models.CharField(max_length=500, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="referral_scope_idx"),
            models.Index(fields=["person_ref"], name="referral_person_idx"),
            models.Index(fields=["referrer_ref"], name="referral_referrer_idx"),
            models.Index(fields=["destination_organization_ref"], name="referral_dest_idx"),
        ]
        constraints = [
            # The database refuses a second open referral of the same patient to the same service.
            models.UniqueConstraint(
                fields=["tenant_id", "person_ref", "service_ref"],
                condition=Q(state__in=["issued", "needs_info"]),
                name="referral_one_open_per_person_service",
            ),
        ]
