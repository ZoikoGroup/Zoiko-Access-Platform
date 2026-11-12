"""Public interface of the trust module (D01).

This is the ONLY module other domain modules may import from `modules.trust`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .auth import (
    LoginResult,
    PasswordChange,
    change_password,
    login,
    register_patient,
    revoke_sessions,
    set_password,
)
from .dev_auth import Authenticated, authenticate
from .directory import actor_labels, counts, patient_registry, staff_directory
from .display_names import (
    clear_display_name,
    display_name_of,
    display_names,
    set_display_name,
)
from .email_flows import (
    EmailChange,
    email_status,
    request_password_reset,
    reset_password,
    send_verification,
    set_email,
    verify_email,
)
from .family import (
    approve_family_member,
    approved_family_grants,
    ask_for_proof,
    get_family_grant,
    list_family,
    mark_proof_received,
    pending_family_count,
    pending_family_requests,
    reject_family_member,
    request_family_member,
    resume_family_member,
    suspend_family_member,
    withdraw_family_member,
)
from .identity_match import decide as decide_match
from .identity_match import (
    linked_people,
    list_cases,
    open_case_people,
    rebuild_match_keys,
)
from .identity_proof import history as identity_history
from .identity_proof import record_check as record_identity_check
from .identity_proof import refresh_expired as refresh_expired_identities
from .identity_proof import status_of as identity_status_of
from .identity_proof import withdraw as withdraw_identity_check
from .incidents import add_incident, get_incident, list_incidents, resolve_incident
from .models import ActorType, CareBasis, Status, TenantType
from .name_checks import BASES as NAME_CHECK_BASES
from .name_checks import states as name_check_states
from .name_checks import verified_providers, verify_name, withdraw_check
from .onboarding import onboard_customer, set_customer_sign_up
from .people import (
    approve_proxy_grant,
    care_patients,
    care_team_of,
    create_person,
    end_care_links,
    end_care_relationship,
    ensure_care_relationship,
    link_person_to_actor,
    link_person_to_site,
    own_person_of,
    person_at_site,
    person_exists,
    propose_proxy_grant,
    reachable_persons,
    relationship_verified,
    revoke_proxy_grant,
    set_care_contested,
    start_care_relationship,
    verified_login_email_of_person,
)
from .profile import get_person_profiles, profile_summary_for, set_person_profile
from .releases import add_release, list_releases
from .services import (
    MAX_RESPONSE_TARGET_HOURS,
    active_roles,
    assign_role,
    booking_system_of,
    clinicians_at,
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
    response_target_hours,
    set_actor_status,
    set_booking_system,
    set_response_target,
    set_self_registration,
    tenant_public_id,
    verified_emails_for_role,
)
from .staff_admin import INVITABLE_ROLES, invite_staff, resend_invitation, set_member_status
from .suspensions import cancel_change, decide_change, list_changes, request_change

__all__ = [
    "ActorType", "Authenticated", "CareBasis", "INVITABLE_ROLES", "LoginResult", "Status",
    "TenantType", "active_roles", "actor_labels", "approve_proxy_grant", "assign_role",
    "authenticate", "care_patients", "care_team_of", "context_for", "counts", "create_organization",
    "create_person", "create_tenant", "email_status", "end_assignment", "end_care_relationship",
    "ensure_care_relationship", "get_organization", "invite_staff", "is_clinician_at",
    "link_person_to_actor", "list_releases", "list_tenants", "login", "add_release",
    "add_incident", "get_incident", "list_incidents", "resolve_incident",
    "cancel_change", "decide_change", "list_changes", "request_change",
    "onboard_customer", "organization_names", "set_customer_sign_up",
    "verified_emails_for_role", "clinicians_at", "tenant_public_id",
    "overview", "own_person_of", "patient_registry", "person_exists", "profile_summary_for",
    "link_person_to_site", "person_at_site", "end_care_links",
    "MAX_RESPONSE_TARGET_HOURS", "response_target_hours", "set_response_target",
    "booking_system_of", "set_booking_system",
    "decide_match", "identity_history", "identity_status_of", "linked_people", "list_cases",
    "open_case_people", "rebuild_match_keys", "record_identity_check",
    "refresh_expired_identities", "withdraw_identity_check",
    "approve_family_member", "approved_family_grants", "ask_for_proof", "mark_proof_received",
    "resume_family_member", "suspend_family_member", "get_family_grant", "list_family",
    "pending_family_count", "pending_family_requests",
    "reject_family_member", "request_family_member", "withdraw_family_member",
    "get_person_profiles",
    "set_person_profile", "propose_proxy_grant", "reachable_persons", "register_actor",
    "register_patient", "relationship_verified", "request_password_reset", "resend_invitation",
    "EmailChange", "PasswordChange", "verified_login_email_of_person", "clear_display_name",
    "display_name_of", "display_names", "NAME_CHECK_BASES", "verified_providers", "verify_name",
    "withdraw_check", "name_check_states",
    "set_display_name", "change_password", "set_email", "reset_password",
    "revoke_proxy_grant",
    "revoke_sessions", "send_verification",
    "set_actor_status", "set_care_contested", "set_member_status", "set_password",
    "set_self_registration", "staff_directory", "start_care_relationship", "verify_email",
]
