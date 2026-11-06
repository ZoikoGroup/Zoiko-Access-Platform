"""AI governance tables (docs: schema `ai` / `gov.ai_use_permit`, file 05 section G). OFF BY
DEFAULT.

No prompt or answer TEXT is ever stored here: only digests (fingerprints), so no patient data
can end up in these tables. A feature may run only while it holds a permit in the AUTHORIZED
(or, for a dry run,
SHADOW) state.
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Lane(models.TextChoices):
    ADMIN_ASSIST = "A", "Admin assist"
    CLINICAL_CONTENT = "B", "Clinical-content assist"
    DECISION_INFORMING = "C", "Decision-informing"
    DEVICE_CONTROLLED = "D", "Device-controlled"
    AGENTIC = "E", "Agentic action"


class Level(models.TextChoices):
    A0 = "A0", "Deterministic automation"
    A1 = "A1", "Non-clinical language tools"
    A2 = "A2", "Clinical documentation assist"
    A3 = "A3", "Clinical decision / device AI"


class PermitState(models.TextChoices):
    PROPOSED = "PROPOSED", "Proposed"
    QUALIFICATION = "QUALIFICATION", "Qualification"
    DESIGN_CONTROL = "DESIGN_CONTROL", "Design control"
    VALIDATION = "VALIDATION", "Validation"
    SANDBOX = "SANDBOX", "Sandbox"
    SHADOW = "SHADOW", "Shadow (acts on nothing)"
    RELEASE_REVIEW = "RELEASE_REVIEW", "Release review"
    AUTHORIZED = "AUTHORIZED", "Authorized"
    SUSPENDED = "SUSPENDED", "Suspended"
    CHANGE_REVIEW = "CHANGE_REVIEW", "Change review"
    CORRECTIVE_ACTION = "CORRECTIVE_ACTION", "Corrective action"
    REVALIDATION = "REVALIDATION", "Revalidation"
    RETIRED = "RETIRED", "Retired"


class Decision(models.TextChoices):
    ALLOWED = "ALLOWED", "Allowed"
    HUMAN_REVIEW = "HUMAN_REVIEW", "Human review"
    REJECTED = "REJECTED", "Rejected"
    QUARANTINED = "QUARANTINED", "Quarantined"


class ApprovalState(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class AiUsePermit(CellScopedModel):
    feature_id = models.CharField(max_length=60)
    purpose = models.CharField(max_length=200)
    lane = models.CharField(max_length=1, choices=Lane.choices)
    level = models.CharField(max_length=2, choices=Level.choices)
    state = models.CharField(
        max_length=20, choices=PermitState.choices, default=PermitState.PROPOSED
    )
    # Fingerprints that pin exactly what was approved: a change to any of them is a new permit.
    model_digest = models.CharField(max_length=64)
    prompt_digest = models.CharField(max_length=64)
    knowledge_manifest_hash = models.CharField(max_length=64, blank=True, default="")
    clinical_owner_ref = models.CharField(max_length=22, blank=True, default="")
    created_by_ref = models.CharField(max_length=22)
    approver_refs = models.JSONField(default=list)  # distinct people who signed the release
    expiry = models.DateTimeField(null=True, blank=True)
    # The tools this feature may use (whitelist only). Write tools need a named human approver.
    allowed_tools = models.JSONField(default=list)
    write_tools = models.JSONField(default=list)
    two_person_tools = models.JSONField(
        default=list
    )  # write tools that need two different approvers
    max_steps = models.PositiveSmallIntegerField(default=1)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(
                fields=["cell_id", "tenant_id", "feature_id"], name="ai_permit_feature_idx"
            )
        ]


class AiPermitEvent(CellScopedModel):
    """Every state change of a permit, with who and why."""

    permit_ref = models.CharField(max_length=22)
    from_state = models.CharField(max_length=20)
    to_state = models.CharField(max_length=20)
    actor_ref = models.CharField(max_length=22)
    reason = models.CharField(max_length=300, blank=True, default="")


class AiRequest(CellScopedModel):
    permit_ref = models.CharField(max_length=22)
    actor_ref = models.CharField(max_length=22)
    purpose_code = models.CharField(max_length=40)
    mode = models.CharField(max_length=10)  # "live" or "shadow"
    input_digest = models.CharField(max_length=64)  # a fingerprint, never the text
    requested_tools = models.JSONField(default=list)
    correlation_id = models.CharField(max_length=64, blank=True, default="")


class AiResponseEvidence(CellScopedModel):
    request_ref = models.CharField(max_length=22)
    output_digest = models.CharField(max_length=64)
    model_digest = models.CharField(max_length=64)
    prompt_digest = models.CharField(max_length=64)
    validator_flags = models.JSONField(default=list)
    decision = models.CharField(max_length=14, choices=Decision.choices)


class AiActionApproval(CellScopedModel):
    """A write the AI proposed. Nothing happens until the named human(s) approve it."""

    request_ref = models.CharField(max_length=22)
    permit_ref = models.CharField(max_length=22)
    tool = models.CharField(max_length=60)
    payload_digest = models.CharField(max_length=64)
    proposed_by_ref = models.CharField(max_length=22)
    needs_two = models.BooleanField(default=False)
    approver_refs = models.JSONField(default=list)
    state = models.CharField(
        max_length=10, choices=ApprovalState.choices, default=ApprovalState.PENDING
    )
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
