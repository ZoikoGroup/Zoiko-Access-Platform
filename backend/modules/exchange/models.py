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


class Consent(models.TextChoices):
    """The patient's agreement to tell the partner who they are, for this one order (DEV-175)."""

    PENDING = "pending", "Waiting for the patient"
    GRANTED = "granted", "Shared with the patient's agreement"
    DECLINED = "declined", "The patient said no"
    WITHDRAWN = "withdrawn", "The patient took it back"


class ResultFlag(models.TextChoices):
    """How the lab rates a result. Critical always stands out to the ordering doctor."""

    NORMAL = "normal", "Normal"
    ABNORMAL = "abnormal", "Abnormal"
    CRITICAL = "critical", "Critical"


MAX_SUMMARY = 2000
CONSENT_DAYS = 30  # how long one agreement lasts
HOLD_ESCALATE_DAYS = 3  # a held request waits this long before the administrator is told


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
    # What is asked, as a SNOMED CT concept, when the clinic gives one (DEV-323). Only a coded
    # order can be sent to a lab's own system; an uncoded one stays in the partner app.
    order_code = models.CharField(
        max_length=18, blank=True, default=""
    )  # what is asked, in plain words
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
    consent_state = models.CharField(
        max_length=10, choices=Consent.choices, default=Consent.PENDING
    )
    consent_by_ref = models.CharField(max_length=22, blank=True, default="")
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_expires_at = models.DateTimeField(null=True, blank=True)
    # Name and birth date the patient agreed to share, ENCRYPTED, cleared when the agreement is
    # withdrawn. Nothing else about the patient is ever held here.
    shared_identity_enc = models.TextField(blank=True, default="")
    # The person who must decide what to do with a held request: whoever sent it (DEV-170).
    # A short plain-text summary of the result and the lab's rating, delivered by the partner. The
    # text is ENCRYPTED and only the ordering clinic's treating doctors may open it (DEV-307).
    result_summary_enc = models.TextField(blank=True, default="")
    result_flag = models.CharField(
        max_length=10, choices=ResultFlag.choices, blank=True, default=""
    )
    on_hold_escalated_at = models.DateTimeField(null=True, blank=True)
    on_hold_owner_ref = models.CharField(max_length=22, blank=True, default="")
    # Which version of the result this is (DEV-325, FHIR step 5): 1 for the first delivery, one
    # higher for each correction. Earlier versions are kept in ExchangeResultVersion.
    result_version = models.PositiveSmallIntegerField(default=0)
    # The measured values of the result (DEV-330, LEAD DECISION): name, code, value, unit, normal
    # range and flag, as JSON, ENCRYPTED; only the ordering clinic's treating doctors open them.
    result_values_enc = models.TextField(blank=True, default="")
    result_correction = models.CharField(max_length=10, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="exchange_scope_idx"),
            models.Index(fields=["from_tenant_ref", "state"], name="exchange_from_idx"),
            models.Index(fields=["to_tenant_ref", "state"], name="exchange_to_idx"),
        ]


class Correction(models.TextChoices):
    """Why a lab sent a later version of a result (the FHIR DiagnosticReport statuses)."""

    AMENDED = "amended", "Amended"
    CORRECTED = "corrected", "Corrected"
    APPENDED = "appended", "Added to"


class ExchangeResultVersion(CellScopedModel):
    """An earlier version of a delivered result, kept exactly as it was when a correction replaced
    it (DEV-325, T-30). Never changed or deleted. The summary stays ENCRYPTED, bound to its
    request as when it was delivered."""

    request = models.ForeignKey(ExchangeRequest, on_delete=models.PROTECT, related_name="+")
    version = models.PositiveSmallIntegerField()
    result_ref = models.CharField(max_length=40)
    result_flag = models.CharField(max_length=10, blank=True, default="")
    summary_enc = models.TextField(blank=True, default="")
    values_enc = models.TextField(blank=True, default="")  # DEV-330, encrypted as delivered
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by_ref = models.CharField(max_length=22, blank=True, default="")
    correction = models.CharField(max_length=10, blank=True, default="")  # how THIS one came
    reviewed_by_ref = models.CharField(max_length=22, blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    replaced_by_ref = models.CharField(max_length=22)  # who sent the correction

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["request", "version"], name="exchange_result_version")
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="exchange_resver_scope_idx")]


class ScanState(models.TextChoices):
    PENDING = "pending", "Waiting for the virus check"
    CLEAN = "clean", "Checked: no virus found"
    INFECTED = "infected", "A virus was found: it can never be opened"


class ResultAttachment(CellScopedModel):
    """A lab's PDF report attached to a result (DEV-345, LEAD DECISION). Stored ENCRYPTED; only the
    ordering clinic's treating doctor may open it, and only once a virus scan found it clean. It
    belongs to the result version it came with; a correction keeps it."""

    request = models.ForeignKey(ExchangeRequest, on_delete=models.PROTECT, related_name="+")
    result_version = models.PositiveSmallIntegerField()
    filename = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    content_enc = models.TextField()  # the file, encrypted, as text
    scan_state = models.CharField(max_length=10, choices=ScanState.choices,
                                  default=ScanState.PENDING)
    scanned_at = models.DateTimeField(null=True, blank=True)
    uploaded_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="exchange_attach_scope_idx"),
            models.Index(fields=["scan_state"], name="exchange_attach_scan_idx"),
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
