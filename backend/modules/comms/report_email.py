"""A weekly summary email to a clinic's administrators, if the clinic asked for one.

COUNTS only, for the seven whole days before today on the clinic's own clock: no patient, name or
message. It goes to verified addresses of people holding R13 (clinic administrator), at most once
per clinic per week (the ISO week is part of the subject, and a queued email with the same subject
means it was already sent that week). A clinic that never turned it on is never emailed.
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db import transaction

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


def _percent(share: float | None) -> str:
    return "-" if share is None else f"{round(share * 100)}%"


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
        subject = f"Weekly summary for {name}: {_week(today)}"[:200]
        if QueuedEmail.objects.filter(
                kind=KIND, subject=subject, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).exists():
            continue                                    # already sent this week
        recipients = trust.verified_emails_for_role(org, RECIPIENT_ROLE, clock)
        if not recipients:
            continue
        bookings = scheduling.report(org, since, until, zone)
        waiting = waitlist.report(org, since, until)
        status, attendance = bookings["status"], bookings["attendance"]
        first = since.astimezone(get_zone(zone)).date().isoformat()
        last = (until - dt.timedelta(seconds=1)).astimezone(get_zone(zone)).date().isoformat()
        body = (
            f"{name}: the week {first} to {last}\n\n"
            f"- Bookings made: {bookings['total']} ({status['confirmed']} confirmed, "
            f"{status['cancelled']} cancelled)\n"
            f"- Visits: {attendance['attended']} attended, {attendance['no_show']} missed\n"
            f"- Cancellation rate: {_percent(bookings['cancel_rate'])}; "
            f"missed-visit rate: {_percent(bookings['no_show_rate'])}\n"
            f"- Times taken: {_percent(bookings['utilisation']['rate'])} of "
            f"{bookings['utilisation']['offered']} offered\n"
            f"- Waiting list: {waiting['joined']} joined, {waiting['booked']} booked, "
            f"{waiting['waiting_now']} waiting now\n"
            f"\nOpen the staff app for the full report: {settings.STAFF_APP_URL}\n")
        with transaction.atomic():
            for address in recipients:
                queue_email(kind=KIND, to_address=address, subject=subject, body=body)
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
