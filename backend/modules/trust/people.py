"""Patients, who treats them, and who may act for them.

These facts feed the access decision. Every function that changes something writes an audit
event in the same transaction. Questions return False on anything unknown, ended, expired,
contested or suspended: a link must be provably real right now.
"""
from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.db.models import Q

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now

from .models import (
    Actor,
    ActorType,
    CareBasis,
    CareRelationship,
    Person,
    ProxyGrant,
    ProxyStatus,
    Status,
)
from .services import _audit, _aware, _own, active_roles

# Roles that may be given a care relationship with a patient (R03 clinician, R09 home care).
CARE_ROLES = frozenset({"R03", "R09"})


def create_person(*, preferred_language: str = "", by: str = "system") -> Person:
    with transaction.atomic():
        person = Person.objects.create(preferred_language=preferred_language[:16])
        _audit(by, "person.create", "person", person.public_id)
        return person


def link_person_to_actor(person_public_id: str, actor_public_id: str, *, by: str) -> Person:
    """Connect a patient to the login they use. One login per patient, one patient per login."""
    with transaction.atomic():
        person = _own(Person, person_public_id)
        actor = _own(Actor, actor_public_id)
        if actor.actor_type != ActorType.PATIENT or actor.status != Status.ACTIVE:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["actor"])
        if person.linked_actor_id is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        person.linked_actor = actor
        person.entity_version += 1
        try:
            with transaction.atomic():
                person.save(update_fields=["linked_actor", "entity_version", "updated_at"])
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None  # actor already has a person
        _audit(by, "person.link_actor", "person", person.public_id, actor=actor.public_id)
        return person


def _in_window(queryset, at: dt.datetime, start: str, end: str):
    return queryset.filter(**{f"{start}__lte": at}).filter(
        Q(**{f"{end}__isnull": True}) | Q(**{f"{end}__gt": at})
    )


# ------------------------------------------------------------------ care relationships
def start_care_relationship(
    *,
    actor_public_id: str,
    person_public_id: str,
    basis: CareBasis,
    valid_until: dt.datetime | None = None,
    by: str,
    clock: Clock | None = None,
) -> CareRelationship:
    """A clinician gets a care reason for one patient. They must already hold a care role."""
    now = utc_now(clock)
    if valid_until is not None and _aware(valid_until, "valid_until") <= now:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"])
    with transaction.atomic():
        actor = _own(Actor, actor_public_id)
        person = _own(Person, person_public_id)
        if actor.status != Status.ACTIVE or person.status != Status.ACTIVE:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if not CARE_ROLES & {role for role, _ in active_roles(actor_public_id, at=now)}:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        link = CareRelationship.objects.create(
            actor=actor, person=person, basis=basis, valid_from=now, valid_until=valid_until
        )
        _audit(by, "care.start", "care_relationship", link.public_id,
               actor=actor.public_id, person=person.public_id, basis=basis)
        return link


def ensure_care_relationship(
    *,
    actor_public_id: str,
    person_public_id: str,
    basis: CareBasis,
    valid_until: dt.datetime | None = None,
    by: str,
    clock: Clock | None = None,
) -> CareRelationship:
    """Give a clinician a care reason for a patient, unless an active one already exists (then
    it is kept, and its end date is extended if the new one is later)."""
    now = utc_now(clock)
    with transaction.atomic():
        actor = _own(Actor, actor_public_id)
        person = _own(Person, person_public_id)
        existing = _in_window(
            CareRelationship.objects.filter(
                actor=actor, person=person, status=Status.ACTIVE, contested=False),
            now, "valid_from", "valid_until",
        ).first()
        if existing is None:
            return start_care_relationship(
                actor_public_id=actor_public_id, person_public_id=person_public_id,
                basis=basis, valid_until=valid_until, by=by, clock=clock)
        if (valid_until is not None and existing.valid_until is not None
                and valid_until > existing.valid_until):
            existing.valid_until = valid_until
            existing.entity_version += 1
            existing.save(update_fields=["valid_until", "entity_version", "updated_at"])
        return existing


def care_patients(actor_public_id: str, *, at: dt.datetime | None = None) -> list[dict[str, str]]:
    """The patients this clinician has an active, uncontested care relationship with."""
    when = utc_now() if at is None else _aware(at, "at")
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    rows = _in_window(
        CareRelationship.objects.filter(
            actor__public_id=actor_public_id, actor__status=Status.ACTIVE,
            person__status=Status.ACTIVE, status=Status.ACTIVE, contested=False, **scope),
        when, "valid_from", "valid_until",
    ).select_related("person").order_by("created_at")
    return [
        {"person_ref": r.person.public_id, "basis": r.basis, "since": r.valid_from.isoformat()}
        for r in rows
    ]


def end_care_relationship(
    link_public_id: str, *, by: str, clock: Clock | None = None
) -> CareRelationship:
    with transaction.atomic():
        link = _own(CareRelationship, link_public_id)
        if link.status == Status.ENDED:
            return link
        link.status = Status.ENDED
        link.valid_until = utc_now(clock)
        link.entity_version += 1
        link.save(update_fields=["status", "valid_until", "entity_version", "updated_at"])
        _audit(by, "care.end", "care_relationship", link.public_id)
        return link


def set_care_contested(link_public_id: str, contested: bool, *, by: str) -> CareRelationship:
    """Flag a link as disputed (for example by the patient). A contested link grants nothing."""
    with transaction.atomic():
        link = _own(CareRelationship, link_public_id)
        link.contested = contested
        link.entity_version += 1
        link.save(update_fields=["contested", "entity_version", "updated_at"])
        _audit(by, "care.contest" if contested else "care.uncontest",
               "care_relationship", link.public_id)
        return link


# ------------------------------------------------------------------ proxy grants
def propose_proxy_grant(
    *, delegate_public_id: str, person_public_id: str, relationship_type: str, by: str
) -> ProxyGrant:
    if not relationship_type.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["relationship_type"])
    with transaction.atomic():
        delegate = _own(Actor, delegate_public_id)
        person = _own(Person, person_public_id)
        if delegate.status != Status.ACTIVE or person.status != Status.ACTIVE:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if person.linked_actor_id == delegate.pk:  # you do not need a proxy for yourself
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["delegate"])
        grant = ProxyGrant.objects.create(
            delegate=delegate, person=person, relationship_type=relationship_type.strip()[:32]
        )
        _audit(by, "proxy.propose", "proxy_grant", grant.public_id,
               delegate=delegate.public_id, person=person.public_id)
        return grant


def approve_proxy_grant(
    grant_public_id: str,
    approver_public_id: str,
    *,
    valid_until: dt.datetime | None = None,
    clock: Clock | None = None,
) -> ProxyGrant:
    """Someone other than the delegate approves. Only then does the grant count."""
    now = utc_now(clock)
    if valid_until is not None and _aware(valid_until, "valid_until") <= now:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"])
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_public_id)
        approver = _own(Actor, approver_public_id)
        if grant.status != ProxyStatus.PROPOSED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if approver.pk == grant.delegate_id or approver.status != Status.ACTIVE:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        grant.status = ProxyStatus.ACTIVE
        grant.approved_by = approver
        grant.valid_from = now
        grant.valid_until = valid_until
        grant.entity_version += 1
        grant.save(update_fields=[
            "status", "approved_by", "valid_from", "valid_until", "entity_version", "updated_at"])
        _audit(approver.public_id, "proxy.approve", "proxy_grant", grant.public_id)
        return grant


def revoke_proxy_grant(
    grant_public_id: str, *, by: str, clock: Clock | None = None
) -> ProxyGrant:
    """From now on only; the history stays. Repeating it changes nothing."""
    with transaction.atomic():
        grant = _own(ProxyGrant, grant_public_id)
        if grant.status == ProxyStatus.REVOKED:
            return grant
        grant.status = ProxyStatus.REVOKED
        grant.valid_until = utc_now(clock)
        grant.entity_version += 1
        grant.save(update_fields=["status", "valid_until", "entity_version", "updated_at"])
        _audit(by, "proxy.revoke", "proxy_grant", grant.public_id)
        return grant


# ------------------------------------------------------------------ the question policy asks
def relationship_verified(
    *, actor_public_id: str, condition: str, subject_ref: str, at: dt.datetime | None = None
) -> bool:
    """Is the OWN / REL / PROXY link between this actor and this patient real right now?"""
    when = utc_now() if at is None else _aware(at, "at")
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    actor = Actor.objects.filter(public_id=actor_public_id, status=Status.ACTIVE, **scope).first()
    person = Person.objects.filter(public_id=subject_ref, status=Status.ACTIVE, **scope).first()
    if actor is None or person is None:
        return False
    if condition == "OWN":
        return person.linked_actor_id == actor.pk
    if condition == "REL":
        return _in_window(
            CareRelationship.objects.filter(
                actor=actor, person=person, status=Status.ACTIVE, contested=False, **scope),
            when, "valid_from", "valid_until",
        ).exists()
    if condition == "PROXY":
        return _in_window(
            ProxyGrant.objects.filter(
                delegate=actor, person=person, status=ProxyStatus.ACTIVE, **scope),
            when, "valid_from", "valid_until",
        ).exists()
    return False


def reachable_persons(actor_public_id: str, *, at: dt.datetime | None = None) -> list[str]:
    """Patients this login may act as: their own record plus active proxy grants."""
    when = utc_now() if at is None else _aware(at, "at")
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    actor = Actor.objects.filter(public_id=actor_public_id, status=Status.ACTIVE, **scope).first()
    if actor is None:
        return []
    found = set(
        Person.objects.filter(linked_actor=actor, status=Status.ACTIVE, **scope)
        .values_list("public_id", flat=True)
    )
    grants = _in_window(
        ProxyGrant.objects.filter(
            delegate=actor, status=ProxyStatus.ACTIVE, person__status=Status.ACTIVE, **scope),
        when, "valid_from", "valid_until",
    ).select_related("person")
    found.update(g.person.public_id for g in grants)
    return sorted(found)
