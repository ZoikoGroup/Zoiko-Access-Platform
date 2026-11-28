"""A weekly summary email to a clinic's administrators, if the clinic asked for one (DEV-392, ported
from the maruthi branch).

COUNTS only, for the seven whole days before today on the clinic's own clock: no patient, name or
message. It follows the same rules as the Reports page (DEV-389): a count under the small-number
threshold says "fewer than 5", a rate needs at least 5 cases, and a part whose measure is not
approved says "not shown". The wording is an approved template (`weekly_report`, DEV-386), so it is
held until a safety officer and privacy have approved it.

It goes to verified addresses of the clinic administrators (R13), at most once per clinic per week
(the ISO week is in the subject; a queued email with the same subject means it was already sent).
A clinic that never turned it on is never emailed.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation import measures
from foundation.cell_context import require_context, tenant_transaction
from foundation.mail import queue_email
from foundation.models import QueuedEmail
from foundation.timeutil import UTC, Clock, get_zone, utc_now
from modules.catalog import api as catalog
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.waitlist import api as waitlist

from .services import weekly_report_enabled

KIND = "weekly_report"
REPORT_EVERY_SECONDS = 3600.0
RECIPIENT_ROLE = "R13"
DAYS = 7


def _week(today: dt.date) -> str:
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def report_period(
    zone_name: str, clock: Clock | None = None
) -> tuple[dt.date, dt.datetime, dt.datetime]:
    """(today on the clinic's clock, start, end): the seven whole days before today, as instants."""
    zone = get_zone(zone_name)
    today = utc_now(clock).astimezone(zone).date()
    since = dt.datetime.combine(today - dt.timedelta(days=DAYS), dt.time.min, tzinfo=zone)
    until = dt.datetime.combine(today, dt.time.min, tzinfo=zone)
    return today, since.astimezone(UTC), until.astimezone(UTC)


def _n(value: Any) -> str:
    return f"fewer than {measures.small_cell_k()}" if value is None else str(value)


def summary_lines(report: dict[str, Any]) -> str:
    """The summary's lines, from the report AFTER suppression and withholding (DEV-389)."""
    b = report.get("bookings") or {}
    lines = []
    status = b.get("status")
    if b.get("total", 0) is None and status is None:
        lines.append("- Bookings: not shown (measure not approved)")
    else:
        made = _n(b.get("total"))
        if status:
            lines.append(f"- Bookings made: {made} ({_n(status.get('confirmed'))} confirmed, "
                         f"{_n(status.get('cancelled'))} cancelled)")
        else:
            lines.append(f"- Bookings made: {made}")
    attendance = b.get("attendance")
    if attendance is None:
        lines.append("- Visits: not shown (measure not approved)")
    else:
        attended, missed = attendance.get("attended"), attendance.get("no_show")
        lines.append(f"- Visits: {_n(attended)} attended, {_n(missed)} missed")
        decided = (attended or 0) + (missed or 0)
        if attended is not None and missed is not None and decided >= measures.small_cell_k():
            lines.append(f"- Missed-visit rate: {round(missed * 100 / decided)}%")
    waiting = report.get("waiting_list")
    if waiting is None:
        lines.append("- Waiting list: not shown (measure not approved)")
    else:
        lines.append(f"- Waiting list: {_n(waiting.get('joined'))} joined, "
                     f"{_n(waiting.get('booked'))} booked, {_n(waiting.get('waiting_now'))} "
                     "waiting now")
    return "\n".join(lines)


def send_weekly_report(clock: Clock | None = None) -> int:
    """For the bound customer: queue one summary per clinic that asked for it and has not had this
    week's. Returns how many emails were queued."""
    ctx = require_context()
    queued = 0
    for org, name in trust.organization_names().items():
        if not weekly_report_enabled(org):
            continue
        zone = catalog.organization_zone(org)["zone"]
        today, since, until = report_period(zone, clock)
        week = _week(today)
        subject = f"Weekly summary for {name}: {week}"[:200]
        if QueuedEmail.objects.filter(
                kind=KIND, subject=subject, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).exists():
            continue                                    # already sent this week
        recipients = trust.verified_emails_for_role(org, RECIPIENT_ROLE, clock)
        if not recipients:
            continue
        raw = {"bookings": scheduling.report(org, since, until, zone),
               "waiting_list": waitlist.report(org, since, until)}
        protected, _ = measures.protect(raw)
        shown, _ = measures.withhold_unapproved(protected)
        local = get_zone(zone)
        values = {
            "name": name, "week": week,
            "first": since.astimezone(local).date().isoformat(),
            "last": (until - dt.timedelta(seconds=1)).astimezone(local).date().isoformat(),
            "summary": summary_lines(shown), "app_url": str(settings.STAFF_APP_URL),
        }
        with transaction.atomic():
            for address in recipients:
                queue_email(kind=KIND, to_address=address, values=values)
                queued += 1
    return queued


def send_weekly_reports() -> int:
    """The worker job: every active customer."""
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += send_weekly_report()
    return total
