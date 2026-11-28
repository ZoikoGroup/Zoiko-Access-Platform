"""Email alerts about partner requests (DEV-310, LEAD DECISION): to the person who ordered a
result that came back CRITICAL, and to the clinic administrator when a held request has waited
too long. The emails hold NO patient, no result and no partner: only that something needs a
person, and a link. They go to confirmed addresses only."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.mail import queue_email
from foundation.models import QueuedEmail
from foundation.timeutil import Clock, utc_now
from modules.exchange import api as exchange
from modules.trust import api as trust

from .alert_phone import text_critical, text_hold

CRITICAL_KIND = "critical_result"
ESCALATION_KIND = "hold_escalation"
ESCALATION_EVERY_SECONDS = 3600.0
ADMIN_ROLE = "R13"


def alert_critical_result(orderer_ref: str) -> int:
    """Tell the person who placed an order that its result came back critical. Runs with the
    CLINIC's tenant bound, inside a transaction. Returns 1 if an email was queued."""
    texted = text_critical(orderer_ref)  # DEV-334: a text too, if they asked and proved the number
    address = trust.verified_login_email_of_actor(orderer_ref)
    if address is None:
        return texted  # no confirmed address: the red mark on their page remains
    queue_email(kind=CRITICAL_KIND, to_address=address, values={
        "doctor_app_url": str(settings.CLINICIAN_APP_URL),
        "staff_app_url": str(settings.STAFF_APP_URL)})
    return 1


def send_escalation_alerts(clock: Clock | None = None) -> int:
    """The worker job: for every clinic with a partner request held for more than 3 days, email its
    administrators once a day. Returns how many emails were queued."""
    day = utc_now(clock).date().isoformat()
    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    if platform is None:
        return 0
    queued = 0
    for tenant in trust.list_tenants():
        if tenant["type"] != "provider_org" or tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(platform["public_id"])):
            waiting = exchange.clinic_counts(tenant["public_id"])["escalated"]
        if waiting == 0:
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            ctx = require_context()
            today = utc_now(clock).date()
            for org in trust.organization_names():
                for admin in trust.actors_with_role(org, ADMIN_ROLE, clock):
                    queued += text_hold(admin, today)  # DEV-336: once a day, if they asked
            for org, name in trust.organization_names().items():
                subject = f"Held partner requests need a decision at {name}: {day}"[:200]
                if QueuedEmail.objects.filter(
                    kind=ESCALATION_KIND,
                    subject=subject,
                    cell_id=ctx.cell_id,
                    tenant_id=ctx.tenant_id,
                ).exists():
                    continue  # already told them today
                recipients = trust.verified_emails_for_role(org, ADMIN_ROLE, clock)
                values = {
                    "name": name, "day": day, "app_url": str(settings.STAFF_APP_URL),
                    "requests": f"{waiting} partner request{' has' if waiting == 1 else 's have'}",
                }
                with transaction.atomic():
                    for address in recipients:
                        queue_email(kind=ESCALATION_KIND, to_address=address, values=values)
                        queued += 1
    return queued
