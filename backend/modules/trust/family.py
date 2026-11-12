"""Family members: a patient asks to act for a child or relative, and the clinic approves.

The steps
1. The patient (a login with its own patient record) says who the person is: name, birth date and
   how they are related. A patient record is created for that person and a guardian grant is
   PROPOSED. Nothing is allowed yet: the patient cannot see or change anything about them.
2. A clinic operations staff member (R04, R13) checks proof of identity and authority at the desk
   and approves, saying so explicitly. Only then does the grant count, and the patient receives
   the guardian role (R02), which allows reading and booking for that person, never editing their
   details.
3. Either side can end it later. A request that is rejected or withdrawn before approval also
   closes the empty patient record that was made for it.

The approver can never be the person who asked (the proxy rules already refuse it).
Audit entries name who did what, never the person's details.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.policy import api as policy

from .models import PENDING_STATES, Actor, Person, ProxyGrant, ProxyStatus, Status
from .people import (
    approve_proxy_grant,
    create_person,
    link_person_to_site,
    propose_proxy_grant,
    resume_proxy_grant,
    revoke_proxy_grant,
    suspend_proxy_grant,
)
from .profile import get_person_profiles, store_profile
from .services import _audit, _own, assign_role

WRITE_PERSON = "write:person"
RELATIONSHIPS = ("parent", "guardian", "caregiver")
APPROVING_ROLES = frozenset({"R04", "R13"})
MAX_PENDING_PER_PERSON = 5


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _grant_dict(g: ProxyGrant, names: dict[str, dict[str, str]]) -> dict[str, Any]:
    ref = g.person.public_id
    profile = names.get(ref, {})
    ran_out = (g.status == ProxyStatus.ACTIVE and g.valid_until is not None
               and g.valid_until <= utc_now())
    return {
        "public_id": g.public_id, "relationship": g.relationship_type,
        "status": ProxyStatus.EXPIRED.value if ran_out else g.status,
        "person_ref": ref, "name": profile.get("name", ""),
        "birth_date": profile.get("birth_date", ""),
        "delegate_ref": g.delegate.public_id, "scopes": list(g.rights or []),
        "proof_kind": g.proof_kind, "proof_ref": g.proof_ref,
        "valid_until": g.valid_until.isoformat() if g.valid_until else None,
        "created_at": g.created_at.isoformat(),
    }


def _close_empty_record(person: Person, by: str) -> None:
    """A record made only for a request that never became real is ended, not left lying around."""
    still_used = ProxyGrant.objects.filter(
        person=person,
        status__in=(*PENDING_STATES, ProxyStatus.ACTIVE, ProxyStatus.SUSPENDED)).exists()
    if not still_used and person.linked_actor_id is None and person.status == Status.ACTIVE:
        person.status = Status.ENDED
        person.entity_version += 1
        person.save(update_fields=["status", "entity_version", "updated_at"])
        _audit(by, "person.close", "person", person.public_id)


def _decided(grant: ProxyGrant, outcome: str) -> None:
    """Tell the requester the clinic decided. The event holds only the request id and the outcome,
    never the family member's name or details."""
    emit(event_type="family.decided.v1", aggregate_type="family", aggregate_ref=grant.public_id,
         payload={"grant_ref": grant.public_id, "outcome": outcome})


def request_family_member(
    *,
    requester_public_id: str,
    own_person_ref: str,
    relationship: str,
    name: str,
    birth_date: str,
    decision: policy.Decision,
    organization_public_id: str | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Step 1. The receipt proves the requester may act on THEIR OWN patient record."""
    policy.check_receipt(
        decision, actor_public_id=requester_public_id, operation=WRITE_PERSON,
        organization_public_id=organization_public_id, subject_ref=own_person_ref)
    today = utc_now(clock).date()
    if relationship not in RELATIONSHIPS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["relationship"])
    if not (birth_date or "").strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["birth_date"])
    with transaction.atomic():
        own = _own(Person, own_person_ref)
        requester = _own(Actor, requester_public_id)
        if own.linked_actor_id != requester.pk:
            raise ZoikoError(ErrorCode.POLICY_DENIED)         # only for your OWN record
        pending = ProxyGrant.objects.filter(
            delegate=requester, status__in=PENDING_STATES, **_scope()).count()
        if pending >= MAX_PENDING_PER_PERSON:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        child = create_person(by=requester_public_id)
        if organization_public_id:
            # The request goes to this clinic, so it may now deal with both records (DEV-107).
            link_person_to_site(own_person_ref, organization_public_id, "family")
            link_person_to_site(child.public_id, organization_public_id, "family")
        store_profile(child, legal_name=name, birth_date=birth_date, phone="",
                      by=requester_public_id, today=today)
        grant = propose_proxy_grant(
            delegate_public_id=requester_public_id, person_public_id=child.public_id,
            relationship_type=relationship, by=requester_public_id)
        _audit(requester_public_id, "family.request", "proxy_grant", grant.public_id)
        return _grant_dict(grant, get_person_profiles([child.public_id]))


def list_family(requester_public_id: str) -> list[dict[str, Any]]:
    """The people this login has asked to act for, waiting or approved (not ended or refused).
    It is the requester's own data, so their own request shows the details they entered."""
    now = utc_now()
    rows = [
        g for g in ProxyGrant.objects.filter(
            delegate__public_id=requester_public_id, person__status=Status.ACTIVE,
            status__in=(*PENDING_STATES, ProxyStatus.ACTIVE, ProxyStatus.SUSPENDED), **_scope()
        ).select_related("person", "delegate").order_by("-created_at")
        if g.valid_until is None or g.valid_until > now
    ]
    names = get_person_profiles([g.person.public_id for g in rows])
    return [_grant_dict(g, names) for g in rows]


def get_family_grant(grant_ref: str) -> dict[str, Any]:
    g = _own(ProxyGrant, grant_ref)
    return _grant_dict(g, {})


def withdraw_family_member(*, grant_ref: str, requester_public_id: str) -> dict[str, Any]:
    """The requester withdraws a waiting request or ends an approved link. Repeating is harmless."""
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_ref)
        if grant.delegate.public_id != requester_public_id:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        was_waiting = grant.status in PENDING_STATES
        revoke_proxy_grant(grant_ref, by=requester_public_id)
        if was_waiting:
            _close_empty_record(grant.person, requester_public_id)
        _audit(requester_public_id, "family.withdraw", "proxy_grant", grant.public_id)
        grant.refresh_from_db()
        return _grant_dict(grant, {})


def pending_family_requests() -> list[dict[str, Any]]:
    """Waiting requests of the bound customer, oldest first, for clinic staff to decide. The
    caller (staff) has been allowed to read patients."""
    rows = list(ProxyGrant.objects.filter(
        status__in=PENDING_STATES, person__status=Status.ACTIVE, **_scope()
    ).select_related("person", "delegate").order_by("created_at"))
    names = get_person_profiles([g.person.public_id for g in rows])
    return [_grant_dict(g, names) for g in rows]


def approved_family_grants() -> list[dict[str, Any]]:
    """Guardian links that are active or paused, newest first, so staff can pause or resume one."""
    rows = list(ProxyGrant.objects.filter(
        status__in=(ProxyStatus.ACTIVE, ProxyStatus.SUSPENDED), person__status=Status.ACTIVE,
        **_scope()).select_related("person", "delegate").order_by("-created_at"))
    names = get_person_profiles([g.person.public_id for g in rows])
    return [_grant_dict(g, names) for g in rows]


def pending_family_count() -> int:
    """How many requests wait for the clinic, as a bare number (no details are read)."""
    return ProxyGrant.objects.filter(
        status__in=PENDING_STATES, person__status=Status.ACTIVE, **_scope()).count()


def _staff_check(
    grant: ProxyGrant, approver_public_id: str, decision: policy.Decision,
    organization_public_id: str | None,
) -> None:
    policy.check_receipt(
        decision, actor_public_id=approver_public_id, operation=WRITE_PERSON,
        organization_public_id=organization_public_id, subject_ref=grant.person.public_id)
    if decision.role_code not in APPROVING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


# What staff may say they saw to establish a guardian's legal basis (DEV-144). The list is ours, not
# the specification's: it names kinds of document, never which one a given relationship needs.
PROOF_KINDS = ("birth_record", "adoption_record", "court_order", "power_of_attorney",
               "carer_registration", "patient_written_consent", "other_document")
_PROOF_REF = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")


def approve_family_member(
    *,
    grant_ref: str,
    approver_public_id: str,
    decision: policy.Decision,
    organization_public_id: str,
    verified: bool,
    proof_kind: str = "",
    proof_ref: str = "",
    scopes: list[str] | None = None,
    valid_until: str = "",
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Step 2. Staff confirm they checked proof of identity and authority (`verified`) and choose
    what the guardian may do (`scopes`, at least one; the minimum necessary, GHE-06)."""
    now = utc_now(clock)
    if verified is not True:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["verified"])
    if proof_kind not in PROOF_KINDS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["proof_kind"])
    if not _PROOF_REF.match(proof_ref or ""):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["proof_ref"])
    if not scopes:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])
    until: dt.datetime | None = None
    if (valid_until or "").strip():
        try:
            day = dt.date.fromisoformat(valid_until.strip())
        except ValueError:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"]) from None
        until = dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=dt.timezone.utc)
        if until <= now:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"])
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_ref)
        _staff_check(grant, approver_public_id, decision, organization_public_id)
        approve_proxy_grant(
            grant_ref, approver_public_id, valid_until=until, rights=scopes,
            proof_kind=proof_kind, proof_ref=proof_ref, clock=clock)
        try:
            with transaction.atomic():
                assign_role(
                    actor_public_id=grant.delegate.public_id,
                    organization_public_id=organization_public_id, role_code="R02",
                    by=approver_public_id, clock=clock)
        except ZoikoError as exc:
            if exc.code != ErrorCode.CONFLICT_VERSION:       # already holds it: fine
                raise
        _audit(approver_public_id, "family.approve", "proxy_grant", grant.public_id)
        _decided(grant, "approved")
        grant.refresh_from_db()
        return _grant_dict(grant, {})


def reject_family_member(
    *,
    grant_ref: str,
    approver_public_id: str,
    decision: policy.Decision,
    organization_public_id: str,
) -> dict[str, Any]:
    """Staff refuse a waiting request. The empty patient record made for it is closed."""
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_ref)
        _staff_check(grant, approver_public_id, decision, organization_public_id)
        if grant.status not in PENDING_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        grant.status = ProxyStatus.REJECTED            # never active: not "revoked"
        grant.valid_until = utc_now()
        grant.entity_version += 1
        grant.save(update_fields=["status", "valid_until", "entity_version", "updated_at"])
        _close_empty_record(grant.person, approver_public_id)
        _audit(approver_public_id, "family.reject", "proxy_grant", grant.public_id)
        _decided(grant, "rejected")
        grant.refresh_from_db()
        return _grant_dict(grant, {})


# ---------------------------------------------------------- the steps between asking and deciding
def _step(
    grant_ref: str, approver_public_id: str, decision: policy.Decision,
    organization_public_id: str | None, *, from_states: tuple[ProxyStatus, ...],
    to_state: ProxyStatus, action: str,
) -> dict[str, Any]:
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_ref)
        _staff_check(grant, approver_public_id, decision, organization_public_id)
        if grant.status not in from_states:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        grant.status = to_state
        grant.entity_version += 1
        grant.save(update_fields=["status", "entity_version", "updated_at"])
        _audit(approver_public_id, action, "proxy_grant", grant.public_id)
        grant.refresh_from_db()
        return _grant_dict(grant, {})


def ask_for_proof(
    *, grant_ref: str, approver_public_id: str, decision: policy.Decision,
    organization_public_id: str | None,
) -> dict[str, Any]:
    """Staff need to see proof of identity and authority first. Still no access."""
    return _step(grant_ref, approver_public_id, decision, organization_public_id,
                 from_states=(ProxyStatus.PROPOSED,), to_state=ProxyStatus.PROOF_REQUIRED,
                 action="family.proof_required")


def mark_proof_received(
    *, grant_ref: str, approver_public_id: str, decision: policy.Decision,
    organization_public_id: str | None,
) -> dict[str, Any]:
    """The proof has arrived and the request waits for the clinic's decision."""
    return _step(grant_ref, approver_public_id, decision, organization_public_id,
                 from_states=(ProxyStatus.PROPOSED, ProxyStatus.PROOF_REQUIRED),
                 to_state=ProxyStatus.APPROVAL_PENDING, action="family.proof_received")


def suspend_family_member(
    *, grant_ref: str, approver_public_id: str, decision: policy.Decision,
    organization_public_id: str | None, reason: str,
) -> dict[str, Any]:
    """Pause a guardian's access, with a reason (the audit keeps it). Nothing is shown to them."""
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 200:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_ref)
        _staff_check(grant, approver_public_id, decision, organization_public_id)
        suspend_proxy_grant(grant_ref, by=approver_public_id, reason=reason)
        grant.refresh_from_db()
        return _grant_dict(grant, {})


def resume_family_member(
    *, grant_ref: str, approver_public_id: str, decision: policy.Decision,
    organization_public_id: str | None,
) -> dict[str, Any]:
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_ref)
        _staff_check(grant, approver_public_id, decision, organization_public_id)
        resume_proxy_grant(grant_ref, by=approver_public_id)
        grant.refresh_from_db()
        return _grant_dict(grant, {})
