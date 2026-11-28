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
from .death import death_dates, death_of, record_death, withdraw_death
from .dev_auth import Authenticated, authenticate
from .directory import actor_labels, counts, inactive_actors, patient_registry, staff_directory
from .display_names import (
    clear_display_name,
    display_name_of,
    display_names,
    set_display_name,
)
from .email_flows import (
    EmailChange,
    email_status,
    login_email_of_actor,
    request_password_reset,
    reset_password,
    send_verification,
    set_email,
    verified_login_email_of_actor,
    verify_email,
)
from .family import (
    adult_proxy_persons,
    adult_reviews_due,
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
    review_adult_grant,
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
from .identity_merge import canonical_person_ref, merge_of
from .identity_merge import merge as merge_people
from .identity_merge import unmerge as unmerge_people
from .identity_proof import history as identity_history
from .identity_proof import record_check as record_identity_check
from .identity_proof import refresh_expired as refresh_expired_identities
from .identity_proof import status_of as identity_status_of
from .identity_proof import withdraw as withdraw_identity_check
from .incidents import (
    add_incident,
    assess_incident,
    get_incident,
    incident_dict,
    list_incidents,
    record_handoff,
    record_system_incident,
    resolve_incident,
    serious_incident_summary,
)
from .models import ActorType, CareBasis, Status, TenantType
from .name_checks import BASES as NAME_CHECK_BASES
from .name_checks import states as name_check_states
from .name_checks import verified_providers, verify_name, withdraw_check
from .onboarding import onboard_customer, set_customer_country, set_customer_sign_up
from .oncall import acknowledge as acknowledge_incident
from .oncall import assign as assign_incident
from .oncall import staff_choices as on_call_staff
from .oncall import timeline as incident_timeline
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
    people_at_site,
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
    actors_with_role,
    assign_role,
    booking_system_of,
    business_hours_of,
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
    role_info,
    set_actor_status,
    set_booking_system,
    set_business_hours,
    set_response_target,
    set_self_registration,
    tenant_country,
    tenant_public_id,
    verified_emails_for_role,
)
from .staff_admin import (
    INVITABLE_ROLES,
    invite_colleague,
    invite_staff,
    resend_invitation,
    set_member_status,
)
from .support_access import MINUTES as SUPPORT_MINUTES
from .support_access import SCOPES as SUPPORT_SCOPES
from .support_access import can_decide as can_decide_support_access
from .support_access import decide as decide_support_access
from .support_access import end as end_support_access
from .support_access import listing as support_access_list
from .support_access import request as request_support_access
from .support_access import requester_label as support_requester_label
from .support_access import use as use_support_access
from .suspensions import cancel_change, decide_change, list_changes, request_change
from .two_step import RECHECK_AFTER as TWO_STEP_RECHECK_AFTER
from .two_step import admin_reset as admin_reset_two_step
from .two_step import finish_set_up as finish_two_step_set_up
from .two_step import finish_sign_in as finish_two_step_sign_in
from .two_step import needs_recheck as two_step_needs_recheck
from .two_step import new_recovery_codes, staff_two_step_required, staff_without_two_step
from .two_step import recheck as recheck_two_step
from .two_step import set_staff_requirement as set_staff_two_step_requirement
from .two_step import start_set_up as start_two_step_set_up
from .two_step import status as two_step_status
from .two_step import switch_off as switch_off_two_step

__all__ = [
    "actors_with_role",
    "acknowledge_incident", "assign_incident", "incident_timeline", "on_call_staff",
    "ActorType", "Authenticated", "CareBasis", "INVITABLE_ROLES", "LoginResult", "Status",
    "TenantType", "active_roles", "actor_labels", "approve_proxy_grant", "assign_role",
    "inactive_actors", "incident_dict",
    "authenticate", "care_patients", "care_team_of", "context_for", "counts", "create_organization",
    "tenant_public_id",
    "create_person", "create_tenant", "email_status", "login_email_of_actor",
    "verified_login_email_of_actor", "end_assignment",
    "end_care_relationship",
    "ensure_care_relationship", "get_organization", "invite_colleague", "invite_staff",
    "is_clinician_at",
    "link_person_to_actor", "list_releases", "list_tenants", "login", "add_release",
    "add_incident", "assess_incident", "get_incident", "list_incidents", "record_handoff",
    "record_system_incident", "resolve_incident", "serious_incident_summary",
    "cancel_change", "decide_change", "list_changes", "request_change",
    "onboard_customer", "organization_names", "set_customer_country", "set_customer_sign_up",
    "tenant_country",
    "verified_emails_for_role", "clinicians_at",
    "overview", "own_person_of", "patient_registry", "person_exists", "profile_summary_for",
    "link_person_to_site", "people_at_site", "person_at_site", "end_care_links",
    "MAX_RESPONSE_TARGET_HOURS", "response_target_hours", "set_response_target",
    "business_hours_of", "set_business_hours",
    "booking_system_of", "set_booking_system",
    "canonical_person_ref", "merge_of", "merge_people", "unmerge_people",
    "decide_match", "identity_history", "identity_status_of", "linked_people", "list_cases",
    "open_case_people", "rebuild_match_keys", "record_identity_check",
    "refresh_expired_identities", "withdraw_identity_check",
    "adult_proxy_persons", "adult_reviews_due", "review_adult_grant",
    "approve_family_member", "approved_family_grants", "ask_for_proof", "mark_proof_received",
    "resume_family_member", "suspend_family_member", "get_family_grant", "list_family",
    "pending_family_count", "pending_family_requests",
    "reject_family_member", "request_family_member", "withdraw_family_member",
    "get_person_profiles",
    "death_dates", "death_of", "record_death", "withdraw_death",
    "set_person_profile", "propose_proxy_grant", "reachable_persons", "register_actor",
    "register_patient", "relationship_verified", "request_password_reset", "resend_invitation",
    "EmailChange", "PasswordChange", "verified_login_email_of_person", "clear_display_name",
    "display_name_of", "display_names", "NAME_CHECK_BASES", "verified_providers", "verify_name",
    "withdraw_check", "name_check_states",
    "set_display_name", "change_password", "set_email", "reset_password",
    "revoke_proxy_grant",
    "revoke_sessions", "send_verification",
    # Short-term support access (DEV-381)
    "SUPPORT_MINUTES", "SUPPORT_SCOPES", "can_decide_support_access", "decide_support_access",
    "end_support_access", "support_access_list", "request_support_access", "use_support_access",
    "support_requester_label",
    # Two-step sign-in (DEV-380)
    "TWO_STEP_RECHECK_AFTER", "admin_reset_two_step", "finish_two_step_set_up",
    "finish_two_step_sign_in",
    "two_step_needs_recheck", "new_recovery_codes", "recheck_two_step",
    "set_staff_two_step_requirement", "staff_without_two_step", "staff_two_step_required",
    "start_two_step_set_up",
    "two_step_status", "switch_off_two_step",
    "role_info", "set_actor_status", "set_care_contested", "set_member_status", "set_password",
    "set_self_registration", "staff_directory", "start_care_relationship", "verify_email",
]
