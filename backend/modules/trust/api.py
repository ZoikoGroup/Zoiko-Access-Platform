"""Public interface of the trust module (D01).

This is the ONLY module other domain modules may import from `modules.trust`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .auth import LoginResult, login, register_patient, revoke_sessions, set_password
from .dev_auth import Authenticated, authenticate
from .directory import actor_labels, counts, patient_registry, staff_directory
from .email_flows import (
    email_status,
    request_password_reset,
    reset_password,
    send_verification,
    verify_email,
)
from .family import (
    approve_family_member,
    get_family_grant,
    list_family,
    pending_family_count,
    pending_family_requests,
    reject_family_member,
    request_family_member,
    withdraw_family_member,
)
from .models import ActorType, CareBasis, Status, TenantType
from .onboarding import onboard_customer
from .people import (
    approve_proxy_grant,
    care_patients,
    care_team_of,
    create_person,
    end_care_relationship,
    ensure_care_relationship,
    link_person_to_actor,
    own_person_of,
    person_exists,
    propose_proxy_grant,
    reachable_persons,
    relationship_verified,
    revoke_proxy_grant,
    set_care_contested,
    start_care_relationship,
)
from .profile import get_person_profiles, profile_summary_for, set_person_profile
from .services import (
    active_roles,
    assign_role,
    context_for,
    create_organization,
    create_tenant,
    end_assignment,
    get_organization,
    is_clinician_at,
    list_tenants,
    organization_names,
    overview,
    register_actor,
    set_actor_status,
    set_self_registration,
)
from .staff_admin import INVITABLE_ROLES, invite_staff, resend_invitation, set_member_status

__all__ = [
    "ActorType", "Authenticated", "CareBasis", "INVITABLE_ROLES", "LoginResult", "Status",
    "TenantType", "active_roles", "actor_labels", "approve_proxy_grant", "assign_role",
    "authenticate", "care_patients", "care_team_of", "context_for", "counts", "create_organization",
    "create_person", "create_tenant", "email_status", "end_assignment", "end_care_relationship",
    "ensure_care_relationship", "get_organization", "invite_staff", "is_clinician_at",
    "link_person_to_actor", "list_tenants", "login", "onboard_customer", "organization_names",
    "overview", "own_person_of", "patient_registry", "person_exists", "profile_summary_for",
    "approve_family_member", "get_family_grant", "list_family", "pending_family_count",
    "pending_family_requests",
    "reject_family_member", "request_family_member", "withdraw_family_member",
    "get_person_profiles",
    "set_person_profile", "propose_proxy_grant", "reachable_persons", "register_actor",
    "register_patient", "relationship_verified", "request_password_reset", "resend_invitation",
    "reset_password", "revoke_proxy_grant", "revoke_sessions", "send_verification",
    "set_actor_status", "set_care_contested", "set_member_status", "set_password",
    "set_self_registration", "staff_directory", "start_care_relationship", "verify_email",
]
