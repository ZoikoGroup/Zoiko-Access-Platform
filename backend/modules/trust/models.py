"""Who is who: tenants, organizations, roles, actors and the role assignments between them.

A role alone is NEVER enough to act (docs/schema 07). Assignments only say "this actor may
hold this role at this organization during this period". The policy module decides requests.
"""
from __future__ import annotations

from django.db import models

from foundation.ids import new_internal_id, new_public_id
from foundation.models import CellScopedModel
from foundation.timeutil import utc_now


class TenantType(models.TextChoices):
    PROVIDER_ORG = "provider_org", "Provider organization"
    PAYER = "payer", "Payer"
    EMPLOYER = "employer", "Employer"
    PARTNER = "partner", "Partner"
    PLATFORM = "platform", "Platform"


class Status(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    ENDED = "ended", "Ended"  # tenants, organizations, assignments: no longer in force


class Tenant(models.Model):
    """The isolation boundary. Its `id` IS the tenant_id stamped on every other row.

    Deliberately NOT row-level-secured: login must find the tenant before any tenant is
    bound. Only trust code reads it, and it holds no personal data.
    """

    id = models.UUIDField(primary_key=True, default=new_internal_id, editable=False)
    public_id = models.CharField(max_length=22, unique=True, default=new_public_id, editable=False)
    cell_id = models.CharField(max_length=32, editable=False)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=16, choices=TenantType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    authority_ref = models.CharField(max_length=64, blank=True, default="")
    # Whether patients may create their own account here. Off unless the customer turns it on.
    self_registration = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=utc_now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cell_id", "name"], name="trust_tenant_name_unique"),
        ]

    def __str__(self) -> str:
        return self.public_id


class Organization(CellScopedModel):
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    # How long this clinic aims to take to answer a booking, message or referral (DEV-120). Empty
    # means the service-wide default (RESPONSE_TARGET_HOURS). The specification says there is no
    # universal timeout: each clinic's own target is what "late" means for it.
    response_target_hours = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="org_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "name"], name="trust_org_name_unique"),
        ]


class Role(CellScopedModel):
    """Catalog row (DEV-009): R01..R12 today, more later, without a code change."""

    role_code = models.CharField(max_length=16)
    name = models.CharField(max_length=100)
    workspace = models.CharField(max_length=100, blank=True, default="")  # "" = no workspace
    default_prohibitions = models.JSONField(default=list)  # short plain-language statements

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="role_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "role_code"], name="trust_role_unique"),
        ]


class ActorType(models.TextChoices):
    PATIENT = "patient", "Patient"
    CLINICIAN = "clinician", "Clinician or allied practitioner"
    FACILITY_USER = "facility_user", "Facility or organization user"
    PROXY = "proxy", "Caregiver, guardian or proxy"
    PAYER_USER = "payer_user", "Payer user"
    EMPLOYER_USER = "employer_user", "Employer user"
    WORKLOAD = "workload", "Partner or service workload"
    SUPPORT = "support", "Platform support"


class Actor(CellScopedModel):
    """Any authenticated principal. Login is not identity proof, and not a role."""

    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    issuer = models.CharField(max_length=100)    # the identity provider
    subject = models.CharField(max_length=128)   # its id for this person (never a national id)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="actor_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "issuer", "subject"], name="trust_actor_binding_unique"
            ),
        ]


class RoleAssignment(CellScopedModel):
    actor = models.ForeignKey(Actor, on_delete=models.PROTECT, related_name="assignments")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="+")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="+")
    sponsor = models.ForeignKey(
        Actor, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    removal_reason = models.CharField(max_length=64, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="assign_scope_idx"),
            models.Index(fields=["actor", "status"], name="assign_actor_idx"),
        ]


class IdentityStatus(models.TextChoices):
    UNVERIFIED = "unverified", "Unverified"
    VERIFIED = "verified", "Verified"


class Person(CellScopedModel):
    """The patient inside this cell. NOT the same thing as an actor (a login).

    No name, birth date or other demographics yet: those need field-level encryption, which
    is not built (DEV-024). Until then a person is only an id, a language and a link status.
    """

    identity_status = models.CharField(
        max_length=16, choices=IdentityStatus.choices, default=IdentityStatus.UNVERIFIED
    )
    preferred_language = models.CharField(max_length=16, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    # The login this patient uses, if any (a person may have none). One login per person.
    linked_actor = models.OneToOneField(
        Actor, null=True, blank=True, on_delete=models.PROTECT, related_name="person"
    )

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="person_scope_idx")]


class PersonProfile(CellScopedModel):
    """A patient's name, birth date and phone, stored ONLY as ciphertext (foundation/crypto.py).

    Kept apart from `Person` so the identifying details sit in their own table. Each value is bound
    to this tenant, this field and this person, so it cannot be moved to another row.
    """

    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="profile")
    legal_name_enc = models.TextField(blank=True, default="")
    birth_date_enc = models.TextField(blank=True, default="")
    phone_enc = models.TextField(blank=True, default="")
    updated_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="profile_scope_idx")]


class ActorName(CellScopedModel):
    """The name a staff member, doctor or platform user chose to be shown by, stored ONLY as
    ciphertext (foundation/crypto.py), bound to this tenant and this person.

    Entered by the person themselves and NOT verified by anyone. Patients and guardians have no
    row here: their names live in PersonProfile."""

    actor = models.OneToOneField(Actor, on_delete=models.PROTECT, related_name="display_name_row")
    name_enc = models.TextField()

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="actorname_scope_idx")]


class CareBasis(models.TextChoices):
    CARE_TEAM = "care_team", "Care team assignment"
    REFERRAL = "referral", "Referral"
    APPOINTMENT = "appointment", "Appointment"


class CareRelationship(CellScopedModel):
    """A clinician is allowed to deal with this person because of a care reason."""

    actor = models.ForeignKey(Actor, on_delete=models.PROTECT, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="care_links")
    basis = models.CharField(max_length=16, choices=CareBasis.choices)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    contested = models.BooleanField(default=False)  # a contested link counts as no link
    # The site (organization) where the care happens. A link with a site counts only while the
    # clinician still holds a care role at that site. "" = an older link tied to no site.
    organization_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="care_scope_idx"),
            models.Index(fields=["actor", "person"], name="care_pair_idx"),
        ]


class PersonSite(CellScopedModel):
    """This patient is connected to this clinic site: they booked, were referred, joined a waiting
    list, wrote to it, or a doctor there treats them. Clinic staff may deal only with patients
    connected to their own site (audit item 1, DEV-107)."""

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="sites")
    organization_ref = models.CharField(max_length=22)
    reason = models.CharField(max_length=16)  # booking, referral, waitlist, message, care

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="personsite_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "person", "organization_ref"], name="personsite_unique"),
        ]


class ProxyStatus(models.TextChoices):
    """The specification's relationship-grant statuses (GHE-06): proposed, proof required, approval
    pending, active, rejected, suspended, revoked, expired. EXPIRED is worked out when a grant is
    read (an active grant past its end date), never stored."""

    PROPOSED = "proposed", "Proposed"
    PROOF_REQUIRED = "proof_required", "Proof required"
    APPROVAL_PENDING = "approval_pending", "Approval pending"
    ACTIVE = "active", "Active"
    REJECTED = "rejected", "Rejected"
    SUSPENDED = "suspended", "Suspended"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"


# A grant is "waiting for the clinic" in any of these. It gives access to nothing.
PENDING_STATES = (ProxyStatus.PROPOSED, ProxyStatus.PROOF_REQUIRED, ProxyStatus.APPROVAL_PENDING)


class ProxyGrant(CellScopedModel):
    """A guardian or caregiver (the delegate) may act for a person; someone else must approve."""

    delegate = models.ForeignKey(Actor, on_delete=models.PROTECT, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="proxy_grants")
    relationship_type = models.CharField(max_length=32)  # parent, guardian, caregiver...
    # What this delegate may do for the person (GHE-06 W05, AT-18): a list of scopes such as
    # "appointments", "messages", "referrals", "insurance". Empty = nothing beyond the name.
    rights = models.JSONField(default=list)
    status = models.CharField(
        max_length=16, choices=ProxyStatus.choices, default=ProxyStatus.PROPOSED
    )
    approved_by = models.ForeignKey(
        Actor, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="proxy_scope_idx"),
            models.Index(fields=["delegate", "person"], name="proxy_pair_idx"),
        ]


class Credential(CellScopedModel):
    """A password login for one actor. A stand-in for the real identity provider (DEV-030).

    Only a salted scrypt hash is stored, never the password. `session_epoch` is put inside every
    signed token; raising it signs the actor out everywhere at once.
    """

    actor = models.OneToOneField(Actor, on_delete=models.PROTECT, related_name="credential")
    login = models.CharField(max_length=128)              # lower-cased; unique per tenant
    password_hash = models.CharField(max_length=255)
    session_epoch = models.PositiveIntegerField(default=1)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(default=utc_now)
    # Where mail for this person goes. Verified only after they click the link we send to it.
    email = models.CharField(max_length=254, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="cred_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "login"], name="trust_login_unique"),
        ]


class TokenPurpose(models.TextChoices):
    VERIFY_EMAIL = "verify_email", "Verify email"
    RESET_PASSWORD = "reset_password", "Reset password"


class AuthToken(CellScopedModel):
    """A one-time link sent by email. Only a hash is stored, never the token itself.

    The token a person receives is "<tenant id>.<secret>"; the tenant id lets the server find the
    right tenant, and only sha256(secret) is kept here.
    """

    actor = models.ForeignKey(Actor, on_delete=models.PROTECT, related_name="+")
    purpose = models.CharField(max_length=16, choices=TokenPurpose.choices)
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="authtoken_scope_idx"),
            models.Index(fields=["token_hash"], name="authtoken_hash_idx"),
            models.Index(fields=["actor", "purpose", "created_at"], name="authtoken_actor_idx"),
        ]


class ReleaseNote(CellScopedModel):
    """One line of the platform's release log: what version went live, when, and what changed.
    Written by Zoiko staff by hand. Add-only: a wrong entry is answered by a new one, never edited.
    """

    version = models.CharField(max_length=40)
    summary = models.CharField(max_length=200)
    details = models.TextField(blank=True, default="")
    released_on = models.DateField()
    recorded_by_ref = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="releasenote_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "version"], name="releasenote_one_version"),
        ]


class IncidentSeverity(models.TextChoices):
    """GHE-20's proposed internal scale (DEV-014, DEV-118). The documents disagree on one scale
    (GHE-09, GHE-18 and GHE-20 each differ); GHE-20 is the only one that says what each level
    means, so it is used until the owners choose. Not a legal "notifiable incident" definition."""

    SEV0 = "sev0", "SEV-0: patient safety, a large unlawful disclosure, or a systemic compromise"
    SEV1 = "sev1", "SEV-1: a major regional loss or a critical workflow failure"
    SEV2 = "sev2", "SEV-2: a partial service degradation"
    SEV3 = "sev3", "SEV-3: a minor operational issue"


class IncidentStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class Incident(CellScopedModel):
    """One line of the platform's incident log, written by Zoiko staff by hand.

    It must never hold patient details. An entry can be closed once with a resolution note; it is
    never otherwise edited or deleted.
    """

    title = models.CharField(max_length=120)
    severity = models.CharField(max_length=8, choices=IncidentSeverity.choices)
    happened_on = models.DateField()
    details = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=IncidentStatus.choices, default=IncidentStatus.OPEN)
    resolution = models.TextField(blank=True, default="")
    resolved_on = models.DateField(null=True, blank=True)
    recorded_by_ref = models.CharField(max_length=64)
    resolved_by_ref = models.CharField(max_length=64, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="incident_scope_idx")]


class SuspensionKind(models.TextChoices):
    SUSPEND = "suspend", "Suspend"
    REINSTATE = "reinstate", "Reinstate"


class SuspensionStatus(models.TextChoices):
    PENDING = "pending", "Waiting for a second person"
    APPLIED = "applied", "Approved and applied"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled by the requester"


class Suspension(CellScopedModel):
    """A request to suspend or reinstate a customer. It changes nothing until a DIFFERENT support
    person approves it. Kept in the platform's own tenant; never deleted.
    """

    customer_ref = models.CharField(max_length=22)           # the customer's tenant public id
    kind = models.CharField(max_length=10, choices=SuspensionKind.choices)
    reason = models.CharField(max_length=500)
    status = models.CharField(
        max_length=10, choices=SuspensionStatus.choices, default=SuspensionStatus.PENDING)
    requested_by_ref = models.CharField(max_length=64)
    decided_by_ref = models.CharField(max_length=64, blank=True, default="")
    decision_note = models.CharField(max_length=300, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="suspension_scope_idx")]
        constraints = [
            # One open request for each customer at a time.
            models.UniqueConstraint(
                fields=["tenant_id", "customer_ref"], condition=models.Q(status="pending"),
                name="suspension_one_pending_per_customer"),
        ]


class NameCheckStatus(models.TextChoices):
    VERIFIED = "verified", "Verified"
    WITHDRAWN = "withdrawn", "Withdrawn"


class NameCheck(CellScopedModel):
    """A record that an administrator checked a doctor's display name against something real.

    It stores WHAT was checked and a short note, plus a fingerprint of the name at that moment:
    if the doctor later changes the name, the check no longer applies (nothing is shown as
    verified).
    The name itself stays in ActorName (encrypted). TEMPORARY RULE (DEV-095): one administrator
    can verify; who really may is open question 25.
    """

    actor = models.OneToOneField(Actor, on_delete=models.PROTECT, related_name="name_check_row")
    name_digest = models.CharField(max_length=64)
    basis = models.CharField(max_length=20)
    note = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=10, choices=NameCheckStatus.choices, default=NameCheckStatus.VERIFIED)
    checked_by_ref = models.CharField(max_length=22)
    checked_at = models.DateTimeField(default=utc_now)
    withdrawn_by_ref = models.CharField(max_length=22, blank=True, default="")
    withdrawn_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="namecheck_scope_idx")]
