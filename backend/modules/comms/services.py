"""Reading notifications and marking them read.

Who may see a patient's notifications is decided by the policy engine (`read:comms` for that
patient); the HTTP layer asks first and passes the allowed people in. Marking one read needs a
policy receipt for `write:comms`, like every other change.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import Notification

WRITE_COMMS = "write:comms"


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def notification_dict(n: Notification) -> dict[str, Any]:
    return {
        "public_id": n.public_id, "person_ref": n.person_ref, "kind": n.kind, "title": n.title,
        "body": n.body, "booking_ref": n.booking_ref, "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


def list_for_persons(person_refs: list[str], *, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. The caller has already been authorized for each of these patients."""
    if not person_refs:
        return []
    rows = Notification.objects.filter(person_ref__in=person_refs, **_scope())
    return [notification_dict(n) for n in rows.order_by("-created_at")[: max(1, min(limit, 200))]]


def unread_count(person_refs: list[str]) -> int:
    if not person_refs:
        return 0
    return Notification.objects.filter(
        person_ref__in=person_refs, read_at__isnull=True, **_scope()).count()


def get_notification(notification_ref: str) -> dict[str, Any]:
    n = Notification.objects.filter(public_id=notification_ref, **_scope()).first()
    if n is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return notification_dict(n)


def mark_read(
    *, notification_ref: str, actor_public_id: str, decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Mark one notification read. Needs a receipt for write:comms for that patient. Repeating
    it is harmless and keeps the first read time."""
    with transaction.atomic():
        n = Notification.objects.select_for_update().filter(
            public_id=notification_ref, **_scope()).first()
        if n is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_COMMS,
            organization_public_id=None, subject_ref=n.person_ref,
        )
        if n.read_at is None:
            n.read_at = utc_now(clock)
            n.entity_version += 1
            n.save(update_fields=["read_at", "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action="notification.read",
                   object_type="notification", object_ref=n.public_id,
                   outcome=AuditOutcome.ALLOWED)
        return notification_dict(n)
