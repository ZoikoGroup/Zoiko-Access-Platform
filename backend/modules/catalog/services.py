"""Creating and reading the catalog. Every change is audited in the same transaction."""
from __future__ import annotations

import re
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import TimeError, get_zone
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import Location, Mode, Service, Status

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


def create_location(
    *,
    organization_ref: str,
    name: str,
    time_zone: str,
    actor_public_id: str,
    decision: policy.Decision,
    address: str = "",
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, organization_ref)
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
                    address=address.strip()[:300], time_zone=time_zone,
                )
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(actor_public_id, "location.create", "location", row.public_id)
        return location_dict(row)


def create_service(
    *,
    organization_ref: str,
    name: str,
    category: str,
    duration_minutes: int,
    mode: str = Mode.IN_PERSON,
    description: str = "",
    actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, organization_ref)
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
                    description=description.strip()[:300],
                )
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(actor_public_id, "service.create", "service", row.public_id)
        return service_dict(row)


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
        "address": row.address, "time_zone": row.time_zone, "status": row.status,
    }


def service_dict(row: Service) -> dict[str, Any]:
    return {
        "public_id": row.public_id, "organization_ref": row.organization_ref, "name": row.name,
        "category": row.category, "description": row.description,
        "duration_minutes": row.duration_minutes, "mode": row.mode, "status": row.status,
    }


def get_service(service_ref: str) -> dict[str, Any]:
    return service_dict(_own(Service, service_ref))


def get_location(location_ref: str) -> dict[str, Any]:
    return location_dict(_own(Location, location_ref))


def list_services(*, query: str = "", active_only: bool = True) -> list[dict[str, Any]]:
    ctx = require_context()
    rows = Service.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    if active_only:
        rows = rows.filter(status=Status.ACTIVE)
    if query.strip():
        rows = rows.filter(name__icontains=query.strip()) | rows.filter(
            category__icontains=query.strip().replace(" ", "_"))
    return [service_dict(r) for r in rows.order_by("name")]


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
