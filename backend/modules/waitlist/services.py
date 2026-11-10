"""Joining and leaving the waiting list, and offering people the times that open up.

Joining and leaving need a policy RECEIPT for exactly this actor, operation, organization and
patient (the same `write:booking` the patient already needs to book). The list is worked by events:
when times open (the scheduling module says so) the oldest people waiting are told, and when a
patient books the service their entry closes. Every change is audited.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.catalog import api as catalog
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .models import OPEN_STATES, CloseReason, WaitEntry, WaitState

# The same right as booking: being on the list is a way of asking to book.
WRITE_BOOKING = "write:booking"
MAX_OFFERS_PER_OPENING = 20


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _audit(by: str, action: str, ref: str) -> None:
    record(actor_ref=by, action=action, object_type="waitlist", object_ref=ref,
           outcome=AuditOutcome.ALLOWED)


def entry_dict(e: WaitEntry) -> dict[str, Any]:
    return {
        "public_id": e.public_id, "person_ref": e.person_ref, "service_ref": e.service_ref,
        "organization_ref": e.organization_ref, "state": e.state,
        "offered_at": e.offered_at.isoformat() if e.offered_at else None,
        "closed_reason": e.closed_reason, "created_at": e.created_at.isoformat(),
    }


def _own(entry_ref: str) -> WaitEntry:
    found = WaitEntry.objects.filter(public_id=entry_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def join(
    *, person_ref: str, service_ref: str, actor_public_id: str, decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Put a patient on the list for a service that has no free time right now."""
    service = catalog.get_service(service_ref)
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
        organization_public_id=service["organization_ref"], subject_ref=person_ref)
    if not catalog.is_offered(service):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["service"])
    if scheduling.list_open_slots(service_ref, limit=1, clock=clock):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["service"])   # just book a time
    try:
        with transaction.atomic():
            entry = WaitEntry.objects.create(
                person_ref=person_ref, service_ref=service_ref,
                organization_ref=service["organization_ref"], joined_by_ref=actor_public_id,
                **_scope())
            trust.link_person_to_site(person_ref, service["organization_ref"], "waitlist")
            _audit(actor_public_id, "waitlist.join", entry.public_id)
            return entry_dict(entry)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None            # already on the list


def leave(
    *, entry_ref: str, actor_public_id: str, decision: policy.Decision
) -> dict[str, Any]:
    with transaction.atomic():
        entry = _own(entry_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=entry.organization_ref, subject_ref=entry.person_ref)
        if entry.state == WaitState.CLOSED:
            return entry_dict(entry)                                       # repeating is harmless
        entry.state = WaitState.CLOSED
        entry.closed_reason = CloseReason.LEFT
        entry.entity_version += 1
        entry.save(update_fields=["state", "closed_reason", "entity_version", "updated_at"])
        _audit(actor_public_id, "waitlist.leave", entry.public_id)
        return entry_dict(entry)


def get_entry(entry_ref: str) -> dict[str, Any]:
    return entry_dict(_own(entry_ref))


def list_entries(
    *, person_refs: list[str] | None = None, organization_ref: str = "", open_only: bool = True,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest first. The caller has authorized each patient or organization it asks for."""
    rows = WaitEntry.objects.filter(**_scope())
    if open_only:
        rows = rows.filter(state__in=OPEN_STATES)
    if person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    return [entry_dict(e) for e in rows.order_by("-created_at")[: max(1, min(limit, 500))]]


def waiting_counts(organization_ref: str = "") -> dict[str, int]:
    """How many people are waiting for each service: {service_ref: n}. Numbers only."""
    rows = WaitEntry.objects.filter(state=WaitState.WAITING, **_scope())
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    out: dict[str, int] = {}
    for service_ref in rows.values_list("service_ref", flat=True):
        out[service_ref] = out.get(service_ref, 0) + 1
    return out


def offer_open_times(service_ref: str, *, clock: Clock | None = None) -> int:
    """Tell the people waiting longest that a time opened up: one person for each free time, so
    nobody is told about a time that is already gone. Each person is told once. Runs in the
    worker, for the bound tenant. Returns how many were told."""
    now = utc_now(clock)
    free = len(scheduling.list_open_slots(
        service_ref, limit=MAX_OFFERS_PER_OPENING, clock=clock))
    already = WaitEntry.objects.filter(
        service_ref=service_ref, state=WaitState.OFFERED, **_scope()).count()
    room = max(0, min(free, MAX_OFFERS_PER_OPENING) - already)   # free times nobody was told about
    if room == 0:
        return 0
    with transaction.atomic():
        queue = list(
            WaitEntry.objects.select_for_update(skip_locked=True)
            .filter(service_ref=service_ref, state=WaitState.WAITING, **_scope())
            .order_by("created_at")[:room])
        for entry in queue:
            entry.state = WaitState.OFFERED
            entry.offered_at = now
            entry.entity_version += 1
            entry.save(update_fields=["state", "offered_at", "entity_version", "updated_at"])
            _audit("system", "waitlist.offer", entry.public_id)
            emit(event_type="waitlist.offered.v1", aggregate_type="waitlist",
                 aggregate_ref=entry.public_id,
                 payload={"entry_ref": entry.public_id, "state": entry.state})
        return len(queue)


def close_for_booking(person_ref: str, service_ref: str) -> int:
    """The patient booked this service: their open entry is done."""
    with transaction.atomic():
        entries = list(WaitEntry.objects.select_for_update().filter(
            person_ref=person_ref, service_ref=service_ref, state__in=OPEN_STATES, **_scope()))
        for entry in entries:
            entry.state = WaitState.CLOSED
            entry.closed_reason = CloseReason.BOOKED
            entry.entity_version += 1
            entry.save(update_fields=["state", "closed_reason", "entity_version", "updated_at"])
            _audit("system", "waitlist.booked", entry.public_id)
        return len(entries)


def expire_old_offers(*, older_than: dt.timedelta | None = None, clock: Clock | None = None) -> int:
    """An offer nobody used goes back to waiting after a while, so the next free time can be
    offered again. Runs in the worker for the bound tenant."""
    now = utc_now(clock)
    limit = (older_than if older_than is not None
             else dt.timedelta(hours=settings.WAITLIST_OFFER_HOURS))
    with transaction.atomic():
        stale = list(WaitEntry.objects.select_for_update(skip_locked=True).filter(
            state=WaitState.OFFERED, offered_at__lte=now - limit, **_scope()))
        for entry in stale:
            entry.state = WaitState.WAITING
            entry.offered_at = None
            entry.entity_version += 1
            entry.save(update_fields=["state", "offered_at", "entity_version", "updated_at"])
        return len(stale)


def report(organization_ref: str, since: dt.datetime, until: dt.datetime) -> dict[str, int]:
    """Counts for this organization's waiting lists: people who joined in the period, how many of
    them booked or left since, and how many are waiting right now (whenever they joined)."""
    rows = WaitEntry.objects.filter(organization_ref=organization_ref, **_scope())
    joined = rows.filter(created_at__gte=since, created_at__lt=until)
    return {
        "joined": joined.count(),
        "booked": joined.filter(closed_reason=CloseReason.BOOKED).count(),
        "left": joined.filter(closed_reason=CloseReason.LEFT).count(),
        "waiting_now": rows.filter(state__in=OPEN_STATES).count(),
    }
