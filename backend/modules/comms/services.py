"""Reading notifications and marking them read.

Who may see a patient's notifications is decided by the policy engine (`read:comms` for that
patient); the HTTP layer asks first and passes the allowed people in. Marking one read needs a
policy receipt for `write:comms`, like every other change.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import cancel_queued
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import EmailPreference, Notification, ReportSchedule

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


def email_notices_enabled(actor_ref: str) -> bool:
    """Has this login asked for emails about its own care? Off by default."""
    return EmailPreference.objects.filter(
        actor_ref=actor_ref, enabled=True, **_scope()).exists()


_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _minutes(value: object, field: str) -> int:
    match = _HHMM.match(value) if isinstance(value, str) else None
    if match is None:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return int(match.group(1)) * 60 + int(match.group(2))


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def quiet_hours_of(actor_ref: str) -> dict[str, str] | None:
    """The login's quiet window as {"start", "end", "zone"}, or None when it has none."""
    row = EmailPreference.objects.filter(actor_ref=actor_ref, **_scope()).first()
    if row is None or row.quiet_start is None or row.quiet_end is None:
        return None
    return {"start": _clock(row.quiet_start), "end": _clock(row.quiet_end), "zone": row.quiet_zone}


def set_quiet_hours(
    actor_ref: str, start: object, end: object, zone: object
) -> dict[str, str] | None:
    """Set the quiet window ("HH:MM" start and end, an IANA time zone) or clear it with all three
    None. Start and end must differ; a window may cross midnight. Audited. TEMPORARY rule
    (DEV-181): only routine reminder emails wait; urgent notices and in-app notices never do."""
    clearing = start is None and end is None and zone is None
    if not clearing:
        first, last = _minutes(start, "start"), _minutes(end, "end")
        if first == last:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["end"])
        if not isinstance(zone, str) or len(zone) > 40 or zone not in available_timezones():
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["zone"])
    with transaction.atomic():
        ctx = require_context()
        values = ({"quiet_start": None, "quiet_end": None, "quiet_zone": ""} if clearing else
                  {"quiet_start": first, "quiet_end": last, "quiet_zone": zone})
        EmailPreference.objects.update_or_create(
            actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, defaults=values)
        record(actor_ref=actor_ref,
               action="comms.quiet_hours_cleared" if clearing else "comms.quiet_hours_set",
               object_type="actor", object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
    return quiet_hours_of(actor_ref)


def next_allowed_send(actor_ref: str, now: dt.datetime) -> dt.datetime:
    """When a routine email may go out: now, or the moment the quiet window ends if `now` falls
    inside it. The end is a wall-clock time in the login's zone, so a clock change cannot move it
    onto the wrong side of the window."""
    window = quiet_hours_of(actor_ref)
    if window is None:
        return now
    zone = ZoneInfo(window["zone"])
    local = now.astimezone(zone)
    first, last = _minutes(window["start"], "start"), _minutes(window["end"], "end")
    at = local.hour * 60 + local.minute
    inside = first <= at < last if first < last else (at >= first or at < last)
    if not inside:
        return now
    day = local.date()
    if first >= last and at >= first:                     # after midnight comes the end
        day += dt.timedelta(days=1)
    end_local = dt.datetime.combine(day, dt.time(last // 60, last % 60), tzinfo=zone)
    return end_local.astimezone(dt.timezone.utc)


def texts_enabled(actor_ref: str) -> bool:
    """Has this login asked for text messages too? Off by default (DEV-194)."""
    return EmailPreference.objects.filter(
        actor_ref=actor_ref, texts_enabled=True, **_scope()).exists()


def set_texts(actor_ref: str, enabled: bool) -> bool:
    """The login turns text messages on or off. Audited."""
    with transaction.atomic():
        ctx = require_context()
        EmailPreference.objects.update_or_create(
            actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
            defaults={"texts_enabled": enabled})
        record(actor_ref=actor_ref, action="comms.texts_on" if enabled else "comms.texts_off",
               object_type="actor", object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
    return enabled


def set_email_notices(actor_ref: str, enabled: bool) -> bool:
    """The login turns emails about its own care on or off. Needs a bound tenant; audited."""
    with transaction.atomic():
        ctx = require_context()
        EmailPreference.objects.update_or_create(
            actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
            defaults={"enabled": enabled})
        record(actor_ref=actor_ref, action="comms.email_notices_on" if enabled
               else "comms.email_notices_off", object_type="actor", object_ref=actor_ref,
               outcome=AuditOutcome.ALLOWED)
        if not enabled:                                # TC37: nothing already queued goes out
            address = trust.login_email_of_actor(actor_ref)
            if address and cancel_queued(kind="patient_update", to_address=address):
                record(actor_ref=actor_ref, action="comms.queued_emails_cancelled",
                       object_type="actor", object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
    return enabled


# ------------------------------------------------------------------ the weekly summary (DEV-392)
def weekly_report_enabled(organization_ref: str) -> bool:
    """Does this clinic get the weekly summary email? Off unless its administrator turned it on."""
    return ReportSchedule.objects.filter(
        organization_ref=organization_ref, weekly=True, **_scope()).exists()


def set_weekly_report(organization_ref: str, enabled: Any, *, by: str) -> bool:
    """Turn the clinic's weekly summary email on or off. Choosing the same thing again is
    harmless. Returns what is now in force."""
    if not isinstance(enabled, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["weekly_report"])
    with transaction.atomic():
        row, _ = ReportSchedule.objects.select_for_update().get_or_create(
            organization_ref=organization_ref, defaults={"weekly": enabled}, **_scope())
        if row.weekly != enabled:
            row.weekly = enabled
            row.entity_version += 1
            row.save(update_fields=["weekly", "entity_version", "updated_at"])
        record(actor_ref=by, action="comms.set_weekly_report", object_type="report_schedule",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED,
               reason_code="on" if enabled else "off")
    return enabled
