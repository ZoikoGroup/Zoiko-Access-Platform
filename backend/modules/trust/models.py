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

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="care_scope_idx"),
            models.Index(fields=["actor", "person"], name="care_pair_idx"),
        ]


class ProxyStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"


class ProxyGrant(CellScopedModel):
    """A guardian or caregiver (the delegate) may act for a person; someone else must approve."""

    delegate = models.ForeignKey(Actor, on_delete=models.PROTECT, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="proxy_grants")
    relationship_type = models.CharField(max_length=32)  # parent, guardian, caregiver...
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
