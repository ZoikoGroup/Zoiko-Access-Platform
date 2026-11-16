"""A daily email to clinic operations staff when work is running late.

The email holds COUNTS and a link only: no patient name, message text or booking detail. It goes
to verified addresses of people holding R04 (clinic operations) at the organization, at most once
per organization per UTC day (the day is part of the subject, and a queued email with the same
subject means it was already sent today).
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.mail import queue_email
from foundation.models import QueuedEmail
from foundation.timeutil import Clock, utc_now
from modules.messaging import api as messaging
from modules.rights import api as rights
from modules.scheduling import api as scheduling
from modules.trust import api as trust

KIND = "late_digest"
DIGEST_EVERY_SECONDS = 3600.0
RECIPIENT_ROLE = "R04"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def send_late_digest(clock: Clock | None = None) -> int:
    """For the bound customer: queue one digest per organization that has late work and has not
    been told today. Returns how many emails were queued."""
    ctx = require_context()
    day = utc_now(clock).date().isoformat()
    queued = 0
    for org, name in trust.organization_names().items():
        hours = trust.response_target_hours(org)        # this clinic's own target
        bookings = scheduling.overdue_requests(hours, clock, organization_ref=org)
        threads = messaging.overdue_threads_for_staff(org, hours, clock)
        requests = rights.overdue_count(org, hours, clock)
        if bookings + threads + requests == 0:
            continue
        subject = f"Running late at {name}: {day}"[:200]
        if QueuedEmail.objects.filter(
                kind=KIND, subject=subject, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).exists():
            continue                                    # already told them today
        recipients = trust.verified_emails_for_role(org, RECIPIENT_ROLE, clock)
        if not recipients:
            continue
        items = []
        if bookings:
            items.append(
                f"- {_plural(bookings, 'appointment', 'appointments')} waiting for confirmation")
        if threads:
            items.append(
                f"- {_plural(threads, 'conversation', 'conversations')} with an unopened "
                "patient message")
        if requests:
            items.append(f"- {_plural(requests, 'data request', 'data requests')} "
                         "past the date to answer")
        body = (f"{name} has work waiting longer than {hours} hours:\n\n" + "\n".join(items)
                + f"\n\nOpen the staff app: {settings.STAFF_APP_URL}\n")
        with transaction.atomic():
            for address in recipients:
                queue_email(kind=KIND, to_address=address, subject=subject, body=body)
                queued += 1
    return queued


def send_late_digests() -> int:
    """The worker job: every active customer."""
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += send_late_digest()
    return total
