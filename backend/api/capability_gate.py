"""Country-pack capabilities checked on EVERY customer endpoint, from one table (DEV-383).

GHE-08 SAF-01: "No function activates for a market until the intended-purpose version and active
market pack match"; SAF-10: "country and feature flags fail closed". A signed, current country pack
for the customer's country switches capabilities on (`jurisdiction.customer_capabilities`). Until
this, only workflow starts, partner booking systems and real texts read them (DEV-156, DEV-224,
DEV-228); booking, messages, referrals, the waiting list and the provider directory ran whatever the
packs said.

How it works: `GATED` maps each API route to the capability it needs; `UNGATED` lists every other
route with the reason it needs none. `test_capability_gate.py` fails when a route is in neither, so
a new endpoint cannot slip past. The `endpoint` wrapper (support.py) calls `check()` after sign-in.

ZOIKO_PACK_GATING decides what happens when the capability is off:
  enforce  refuse: 403 JURISDICTION_INACTIVE with the capability's name (production must use this:
           the release check refuses to start otherwise)
  report   allow, and log one warning line naming the route and the capability (no person, no
           data): development and tests, where demo customers have no signed pack
  off      do nothing
Zoiko's own platform tenant is never gated: it is not a market.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest

from foundation.errors import ErrorCode, ZoikoError
from modules.jurisdiction import api as jurisdiction
from modules.trust import api as trust

log = logging.getLogger("zoiko.pack_gating")

ENFORCE, REPORT, OFF = "enforce", "report", "off"
MODES = (ENFORCE, REPORT, OFF)

BOOKING, MESSAGING, REFERRALS = "booking", "messaging", "referrals"
WAITING_LIST, DIRECTORY = "waiting_list", "provider_directory"

# route (as in api/urls.py) -> the capability it needs
GATED: dict[str, str] = {
    # booking: appointments and the times they are booked into
    "bookings": BOOKING,
    "bookings/<str:booking_id>/ask-provider": BOOKING,
    "bookings/<str:booking_id>/attendance": BOOKING,
    "bookings/<str:booking_id>/calendar": BOOKING,
    "bookings/<str:booking_id>/cancel": BOOKING,
    "bookings/<str:booking_id>/keep": BOOKING,
    "bookings/<str:booking_id>/confirm": BOOKING,
    "bookings/<str:booking_id>/history": BOOKING,
    "bookings/<str:booking_id>/provider-stage": BOOKING,
    "bookings/<str:booking_id>/reschedule": BOOKING,
    "services/<str:service_id>/slots": BOOKING,
    "services/<str:service_id>/slots/withdraw": BOOKING,
    "slots/<str:slot_id>/withdraw": BOOKING,
    "clinician/schedule": BOOKING,
    "staff/booking-system": BOOKING,
    # messaging: message threads between patients and the clinic
    "messages/threads": MESSAGING,
    "messages/threads/<str:thread_id>": MESSAGING,
    "messages/threads/<str:thread_id>/close": MESSAGING,
    "messages/threads/<str:thread_id>/reply": MESSAGING,
    "clinician/messages": MESSAGING,
    # referrals
    "referrals": REFERRALS,
    "referrals/<str:referral_id>/acknowledge": REFERRALS,
    "referrals/<str:referral_id>/decide": REFERRALS,
    "referrals/incoming": REFERRALS,
    "clinician/referrals": REFERRALS,
    "clinician/referral-inbox": REFERRALS,
    "staff/referral-inbox": REFERRALS,
    # waiting list
    "waitlist": WAITING_LIST,
    "waitlist/<str:entry_id>/leave": WAITING_LIST,
    "staff/waitlist": WAITING_LIST,
    "staff/waitlist/<str:entry_id>/priority": WAITING_LIST,
    # provider directory: the services and places patients look up
    "services": DIRECTORY,
    "services/<str:service_id>/listing": DIRECTORY,
    "services/<str:service_id>/status": DIRECTORY,
    "locations": DIRECTORY,
    "locations/<str:location_id>/contact": DIRECTORY,
    "staff/providers": DIRECTORY,
    "staff/providers/<str:provider_id>/unverify": DIRECTORY,
    "staff/providers/<str:provider_id>/verify": DIRECTORY,
}

# Every other route, and why no capability gates it.
_ACCOUNT = "signing in and the person's own account"
_PUBLIC = "a public description of the API, no data"
_PLATFORM = "Zoiko's own platform console (not a market)"
_RIGHTS = "data-protection rights and the person's own record: never switched off by a market"
_SAFETY = "clinic safety, records and administration that every market needs"
_ADMIN = "the clinic's own team, settings and audit"
_MONEY = "billing, claims, coverage and programmes (governed by their own contracts)"
_PARTNER = "partners' own portal and inbound feeds (governed by partner agreements and permits)"
_UNDECIDED = ("lab results and record exchange: the pack names (diagnostics, record_exchange) are "
              "still marked not built, so gating them would switch them off for good; for the lead")
_CLINICAL = "consultation notes and the care team (clinical records, not a market feature)"

UNGATED: dict[str, str] = {
    **{r: _ACCOUNT for r in (
        "me", "profile", "notifications", "notifications/<str:notification_id>/read", "family",
        "family/<str:grant_id>/withdraw", "auth/change-password", "auth/display-name",
        "auth/email", "auth/forgot-password", "auth/login", "auth/login/second-step",
        "auth/logout", "auth/providers", "auth/register", "auth/resend-verification",
        "auth/reset-password", "auth/two-step", "auth/two-step/confirm", "auth/two-step/off",
        "auth/two-step/recheck", "auth/two-step/recovery-codes", "auth/two-step/set-up",
        "auth/verify-email", "my/email-notices", "my/phone-check", "my/phone-check/confirm",
        "my/emergency")},
    **{r: _PUBLIC for r in ("openapi.json", "asyncapi.json", "version")},
    **{r: _RIGHTS for r in (
        "rights", "rights/<str:case_id>/withdraw", "my/export", "access-log", "staff/rights",
        "staff/rights/<str:case_id>/<str:step>", "staff/retention",
        "staff/retention/<str:rule_id>/<str:step>")},
    **{r: _CLINICAL for r in (
        "care-team", "clinician/notes", "clinician/notes/<str:note_id>",
        "clinician/notes/<str:note_id>/<str:step>", "clinician/notes/<str:note_id>/attachments",
        "clinician/notes/<str:note_id>/attachments/<str:attachment_id>", "clinician/patients",
        "clinician/patients/<str:person_id>/references",
        "clinician/references/<str:reference_id>/withdraw", "my/notes",
        "my/notes/<str:note_id>", "my/notes/<str:note_id>/attachments/<str:attachment_id>",
        "my/references")},
    **{r: _UNDECIDED for r in (
        "exchange/incoming", "exchange/incoming/<str:request_id>/<str:step>",
        "exchange/incoming/<str:request_id>/attachment", "exchange/mine",
        "exchange/mine/<str:request_id>/<str:step>", "exchange/mine/<str:request_id>/trend",
        "exchange/mine/<str:request_id>/values", "exchange/partners", "exchange/requests",
        "exchange/mine/<str:request_id>/dispute", "exchange/requests/<str:request_id>/dispute",
        "exchange/requests/<str:request_id>/<str:step>",
        "exchange/requests/<str:request_id>/attachments",
        "exchange/requests/<str:request_id>/attachments/<str:attachment_id>",
        "exchange/requests/<str:request_id>/history", "exchange/requests/<str:request_id>/result",
        "exchange/requests/<str:request_id>/trend", "fhir", "hl7", "staff/fhir/quarantine",
        "staff/fhir/quarantine/<str:quarantine_id>/<str:step>")},
    **{r: _SAFETY for r in (
        "people/<str:person_id>/coverage", "people/<str:person_id>/death",
        "people/<str:person_id>/death/withdraw", "people/<str:person_id>/identity",
        "people/<str:person_id>/identity/check", "people/<str:person_id>/identity/withdraw",
        "people/<str:person_id>/profile", "staff/patients", "staff/matches",
        "staff/matches/<str:case_id>/decide", "staff/matches/<str:case_id>/merge",
        "staff/matches/<str:case_id>/unmerge", "staff/family-requests",
        "staff/family-requests/<str:grant_id>/approve",
        "staff/family-requests/<str:grant_id>/proof-needed",
        "staff/family-requests/<str:grant_id>/proof-received",
        "staff/family-requests/<str:grant_id>/reject",
        "staff/family-requests/<str:grant_id>/resume",
        "staff/family-requests/<str:grant_id>/review",
        "staff/family-requests/<str:grant_id>/suspend", "staff/alert-phone",
        "staff/alert-phone/confirm", "staff/work", "staff/work/<str:item_id>/claim",
        "staff/work/<str:item_id>/history", "staff/work/<str:item_id>/release",
        "staff/workflows", "staff/workflows/<str:instance_id>",
        "staff/workflows/<str:instance_id>/<str:step>", "staff/workflows/definitions",
        "staff/workflows/escalations", "staff/workflows/definitions/<str:definition_id>/<str:step>",
        "staff/workflows/escalations/<str:escalation_id>/record", "staff/ai/permits",
        "staff/ai/permits/<str:permit_id>/advance", "staff/ai/permits/<str:permit_id>/sign")},
    **{r: _ADMIN for r in (
        "staff/dashboard", "staff/directory", "staff/members",
        "staff/members/<str:member_id>/resend-invite",
        "staff/members/<str:member_id>/reset-two-step",
        "staff/members/<str:member_id>/status",
        "staff/settings", "staff/business-hours", "staff/compliance", "staff/reports",
        "staff/two-step-rule", "staff/support-access",
        "staff/support-access/<str:access_id>/<str:step>", "staff/insurance-status")},
    **{r: _MONEY for r in (
        "billing/invoices", "billing/invoices/<str:invoice_id>/payments",
        "billing/payments/<str:payment_id>/refresh", "claims/<str:claim_id>/history",
        "claims/incoming", "claims/incoming/<str:claim_id>/<str:step>",
        "claims/incoming/<str:claim_id>/check", "claims/mine",
        "claims/mine/<str:claim_id>/<str:step>", "claims/payers", "claims/sent",
        "claims/sent/<str:claim_id>/withdraw", "coverage", "coverage/<str:coverage_id>/end",
        "claims/sent/<str:claim_id>/attachments",
        "claims/sent/<str:claim_id>/attachments/<str:attachment_id>",
        "claims/incoming/<str:claim_id>/attachments/<str:attachment_id>",
        "coverage/sharing", "staff/billing/fees", "staff/billing/invoices",
        "staff/billing/invoices/<str:invoice_id>",
        "staff/billing/invoices/<str:invoice_id>/<str:action>", "staff/billing/payments",
        "programme/activity", "programme/codes", "programme/codes/<str:member_ref>/revoke",
        "programme/mine", "programme/mine/<str:link_id>/leave", "programme/redeem")},
    **{r: _PARTNER for r in (
        "partner-events", "portal/dashboard", "portal/entitlement", "portal/members",
        "portal/members/<str:member_id>/end", "portal/order-system", "portal/overview",
        "portal/plans", "portal/plans/<str:plan_id>", "portal/program",
        "portal/program/<str:step>", "portal/sandbox", "portal/sandbox/check", "portal/team",
        "portal/team/<str:member_id>/<str:step>", "staff/connector",
        "staff/connector/<str:operation_id>/history",
        "staff/connector/<str:operation_id>/redrive",
        "staff/connector/<str:operation_id>/resolve", "staff/connector/circuits",
        "staff/connector/circuits/<str:connector_key>/reset", "staff/integrations/deliveries",
        "staff/integrations/deliveries/<str:delivery_id>/redrive",
        "staff/integrations/inbound", "staff/integrations/partners",
        "staff/integrations/partners/<str:client_id>/<str:step>",
        "staff/integrations/webhooks", "staff/integrations/webhooks/<str:endpoint_id>/<str:step>",
        "webhooks/sendgrid/events", "webhooks/twilio/status")},
}

PLATFORM_PREFIX = "platform/"          # every platform/... route: Zoiko's own console


def mode() -> str:
    value = str(getattr(settings, "ZOIKO_PACK_GATING", ENFORCE)).strip().lower()
    return value if value in MODES else ENFORCE          # anything unknown fails closed


def capability_for(route: str) -> str | None:
    return GATED.get(route)


def all_capabilities() -> frozenset[str]:
    return frozenset(GATED.values())


def switched_off(auth: trust.Authenticated) -> list[str]:
    """The capabilities this customer's market has NOT switched on, for the apps to hide those
    screens. Empty unless gating is enforced (in report or off mode nothing is refused)."""
    if mode() != ENFORCE or auth.tenant_type == "platform":
        return []
    on = jurisdiction.customer_capabilities(auth.context.tenant_id)
    return sorted(set(GATED.values()) - on)


def check(request: HttpRequest, auth: trust.Authenticated) -> None:
    """Refuse (or report) a request whose capability the customer's market has not switched on."""
    current = mode()
    if current == OFF or auth.tenant_type == "platform":
        return
    match = getattr(request, "resolver_match", None)
    route = (match.route if match is not None else "").removeprefix("api/v1/")
    needed = capability_for(route)
    if needed is None:
        return
    if needed in jurisdiction.customer_capabilities(auth.context.tenant_id):
        return
    if current == REPORT:
        log.warning("pack_gating would_refuse capability=%s route=%s", needed, route)
        return
    raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE, fields=[needed])
