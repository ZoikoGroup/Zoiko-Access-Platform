"""Creating and reading the catalog. Every change is audited in the same transaction."""
from __future__ import annotations

import re
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Q

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import TimeError, get_zone
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import ListingState, Location, Mode, Service, Status

_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _audit(by: str, action: str, object_type: str, object_ref: str) -> None:
    record(actor_ref=by, action=action, object_type=object_type, object_ref=object_ref,
           outcome=AuditOutcome.ALLOWED)


WRITE_CATALOG = "write:catalog"


def _receipt(decision: policy.Decision, actor_public_id: str, organization_ref: str) -> None:
    """Prove this write was authorized for this actor at this organization (see policy)."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_CATALOG,
        organization_public_id=organization_ref, subject_ref=None,
    )


def _active_org(organization_ref: str) -> None:
    org = trust.get_organization(organization_ref)
    if org is None or org["status"] != "active":
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["organization"])


_PHONE = re.compile(r"^\+?[0-9(][0-9 ()\-]{3,22}[0-9]$")


def clean_phone(value: str) -> str:
    """A phone number as people write it: digits with optional + at the start, spaces, brackets and
    dashes. Empty is allowed (no number shown). Anything else is refused."""
    cleaned = (value or "").strip()
    if cleaned and (not _PHONE.match(cleaned) or sum(c.isdigit() for c in cleaned) < 5):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["phone"])
    return cleaned


def create_location(
    *,
    organization_ref: str,
    name: str,
    time_zone: str,
    actor_public_id: str,
    decision: policy.Decision,
    address: str = "",
    phone: str = "",
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, organization_ref)
    phone = clean_phone(phone)
    if not name.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    try:
        get_zone(time_zone)
    except TimeError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["time_zone"]) from None
    _active_org(organization_ref)
    with transaction.atomic():
        try:
            with transaction.atomic():
                row = Location.objects.create(
                    organization_ref=organization_ref, name=name.strip(),
                    address=address.strip()[:300], phone=phone, time_zone=time_zone,
                )
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(actor_public_id, "location.create", "location", row.public_id)
        return location_dict(row)


def update_location_contact(
    *, location_ref: str, phone: str, address: str, actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Change how patients reach an existing site: its phone number and address."""
    with transaction.atomic():
        row = _own(Location, location_ref)
        _receipt(decision, actor_public_id, row.organization_ref)
        row.phone = clean_phone(phone)
        row.address = address.strip()[:300]
        row.entity_version += 1
        row.save(update_fields=["phone", "address", "entity_version", "updated_at"])
        _audit(actor_public_id, "location.contact", "location", row.public_id)
        return location_dict(row)


def create_service(
    *,
    organization_ref: str,
    name: str,
    category: str,
    duration_minutes: int,
    mode: str = Mode.IN_PERSON,
    description: str = "",
    listing_state: str = ListingState.DRAFT,
    actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Make a service. It starts as a DRAFT: nobody can book it until it has been requested,
    reviewed by a second person and published. Only trusted set-up code (the development console)
    passes another `listing_state`."""
    _receipt(decision, actor_public_id, organization_ref)
    if listing_state not in ListingState.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["listing_state"])
    if not name.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    if not _CATEGORY.match(category):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["category"])
    if not 5 <= duration_minutes <= 480:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["duration_minutes"])
    if mode not in Mode.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["mode"])
    _active_org(organization_ref)
    with transaction.atomic():
        try:
            with transaction.atomic():
                row = Service.objects.create(
                    organization_ref=organization_ref, name=name.strip(), category=category,
                    duration_minutes=duration_minutes, mode=mode,
                    description=description.strip()[:300], listing_state=listing_state,
                )
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(actor_public_id, "service.create", "service", row.public_id)
        return service_dict(row)


LISTING_ROLES = frozenset({"R04", "R13"})       # may ask for a listing
LISTING_APPROVERS = frozenset({"R13"})          # may review, publish, suspend
_ACTIONS = {"request", "return", "review", "publish", "suspend", "restart"}


def change_listing(
    service_ref: str, action: str, *, note: str = "", actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Move a service along DRAFT, EVIDENCE_PENDING, REVIEWED, PUBLISHED (GHE-14 WF-04).

    request: staff ask for a listing and give an evidence reference (DRAFT to EVIDENCE_PENDING).
    review / return: a clinic administrator who is NOT the one who asked reviews it, or sends it
    back with a reason. publish: a clinic administrator who did not ask publishes a reviewed
    service. suspend: a clinic administrator withdraws a listing with a reason; restart makes it
    a draft again."""
    if action not in _ACTIONS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["action"])
    note = (note or "").strip()
    with transaction.atomic():
        row = _own(Service, service_ref)
        _receipt(decision, actor_public_id, row.organization_ref)
        role = decision.role_code
        state = row.listing_state
        if action == "request":
            if role not in LISTING_ROLES or state != ListingState.DRAFT:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION if role in LISTING_ROLES
                                 else ErrorCode.POLICY_DENIED)
            if not 5 <= len(note) <= 200:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
            row.evidence_note, row.requested_by_ref = note, actor_public_id
            row.reviewed_by_ref = row.published_by_ref = ""
            row.listing_state = ListingState.EVIDENCE_PENDING
        else:
            if role not in LISTING_APPROVERS:
                raise ZoikoError(ErrorCode.POLICY_DENIED)
            own_request = actor_public_id == row.requested_by_ref
            if action in ("review", "return", "publish") and own_request:
                raise ZoikoError(ErrorCode.POLICY_DENIED)      # nobody approves their own request
            if action in ("return", "suspend") and not 3 <= len(note) <= 200:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
            if action in ("review", "return"):
                if state != ListingState.EVIDENCE_PENDING:
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION)
                row.listing_state = (ListingState.REVIEWED if action == "review"
                                     else ListingState.DRAFT)
                row.reviewed_by_ref = actor_public_id if action == "review" else ""
            elif action == "publish":
                if state != ListingState.REVIEWED:
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION)
                row.listing_state, row.published_by_ref = ListingState.PUBLISHED, actor_public_id
            elif action == "suspend":
                if state not in (ListingState.PUBLISHED, ListingState.REVIEWED):
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION)
                row.listing_state = ListingState.SUSPENDED
            else:                                                  # restart
                if state != ListingState.SUSPENDED:
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION)
                row.listing_state = ListingState.DRAFT
                row.requested_by_ref = row.reviewed_by_ref = row.published_by_ref = ""
        row.entity_version += 1
        row.save()
        _audit(actor_public_id, f"service.listing_{action}", "service", row.public_id)
        return service_dict(row)


def is_offered(service: dict[str, Any]) -> bool:
    """Can patients see and book this service? Active (the clinic's switch) and published."""
    return service["status"] == Status.ACTIVE and service["listing_state"] == ListingState.PUBLISHED


def set_service_status(
    service_ref: str, status: str, *, actor_public_id: str, decision: policy.Decision
) -> dict[str, Any]:
    with transaction.atomic():
        row = _own(Service, service_ref)
        _receipt(decision, actor_public_id, row.organization_ref)
        if status not in Status.values:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["status"])
        row.status = status
        row.entity_version += 1
        row.save(update_fields=["status", "entity_version", "updated_at"])
        _audit(actor_public_id, "service.set_status", "service", row.public_id)
        return service_dict(row)


def _own(model: Any, public_id: str) -> Any:
    ctx = require_context()
    row = model._default_manager.filter(
        public_id=public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
    ).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def location_dict(row: Location) -> dict[str, Any]:
    return {
        "public_id": row.public_id, "organization_ref": row.organization_ref, "name": row.name,
        "address": row.address, "phone": row.phone, "time_zone": row.time_zone,
        "status": row.status,
    }


def service_dict(row: Service) -> dict[str, Any]:
    return {
        "public_id": row.public_id, "organization_ref": row.organization_ref, "name": row.name,
        "category": row.category, "description": row.description,
        "duration_minutes": row.duration_minutes, "mode": row.mode, "status": row.status,
        "listing_state": row.listing_state, "evidence_note": row.evidence_note,
        "requested_by_ref": row.requested_by_ref, "reviewed_by_ref": row.reviewed_by_ref,
        "published_by_ref": row.published_by_ref,
    }


def get_service(service_ref: str) -> dict[str, Any]:
    return service_dict(_own(Service, service_ref))


def get_location(location_ref: str) -> dict[str, Any]:
    return location_dict(_own(Location, location_ref))


def _category_key(text: str) -> str:
    """Categories are stored like `general_physician`; people type "General physician"."""
    return re.sub(r"\s+", "_", text.strip().lower())


def list_services(
    *, query: str = "", active_only: bool = True, category: str = "", mode: str = "",
    organization_ref: str = "",
) -> list[dict[str, Any]]:
    """Services of the bound tenant. `query` looks in the name, description and category;
    `category`, `mode` and `organization_ref` narrow the result exactly."""
    ctx = require_context()
    rows = Service.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    if active_only:                  # what patients may see: switched on AND published
        rows = rows.filter(status=Status.ACTIVE, listing_state=ListingState.PUBLISHED)
    word = query.strip()
    if word:
        rows = rows.filter(
            Q(name__icontains=word) | Q(description__icontains=word)
            | Q(category__icontains=_category_key(word)))
    if category.strip():
        rows = rows.filter(category=_category_key(category))
    if mode:
        rows = rows.filter(mode=mode)
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    return [service_dict(r) for r in rows.order_by("name")]


def list_categories() -> list[dict[str, Any]]:
    """The categories that have at least one ACTIVE service, with how many, for filter menus."""
    ctx = require_context()
    rows = (Service.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                   status=Status.ACTIVE, listing_state=ListingState.PUBLISHED)
            .values("category").annotate(count=Count("id")).order_by("category"))
    return [{"category": r["category"], "count": r["count"]} for r in rows]


def list_locations(*, organization_ref: str = "") -> list[dict[str, Any]]:
    ctx = require_context()
    rows = Location.objects.filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, status=Status.ACTIVE)
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    return [location_dict(r) for r in rows.order_by("name")]


def service_counts() -> dict[str, int]:
    ctx = require_context()
    rows = Service.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    return {"active": rows.filter(status=Status.ACTIVE).count(),
            "inactive": rows.filter(status=Status.INACTIVE).count()}
