"""Every email Zoiko sends, as an approved template (DEV-386, GHE-16 TemplateDefinition and WF-15).

A template is fixed wording with named gaps. Only the gaps vary, and each gap has a strict shape
(the "variable whitelist"), so nothing else can be slipped into a message: no care details, no
free text from a request. Its VERSION is a fingerprint of the wording and the gaps, so any change
to the words is a new version, which must be approved again before it is sent (foundation/mail.py).

Texts have their own list (foundation/texts.py TEMPLATES); `catalogue()` lists both, so the
approval screen covers every message.

Changing this list is a reviewed code change, never data.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from foundation.errors import ErrorCode, ZoikoError

# Classes (GHE-16 policy order): security notices and clinical alerts can never be turned off by a
# preference; service updates follow the person's choices; admin goes to staff only.
SECURITY, SERVICE, CLINICAL_ALERT, ADMIN = "security", "service", "clinical_alert", "admin"

_URL = re.compile(r"^https?://[A-Za-z0-9.:/?=&_%#-]{1,300}$")
_NAME = re.compile(r"^[^\x00-\x1f<>{}]{1,120}$")          # a clinic, role or organization name
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER = re.compile(r"^\d{1,4}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
_MASKED = re.compile(r"^[^\s]{1,64}@[^\s]{1,190}$")
_SENTENCE = re.compile(r"^[A-Za-z0-9 ,.'()-]{3,160}[.!]$")   # a fixed headline from the code
_TICKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._#/-]{1,39}$")
_SCOPES = re.compile(r"^[a-z_]+(, [a-z_]+){0,4}$")
_DIGEST = re.compile(r"^(- \d{1,4} [a-z ]{3,60}(\n|$)){1,3}$")
_REQUESTS = re.compile(r"^\d{1,4} partner requests? ha(s|ve)$")
_SWITCHED = re.compile(r"^switched (on|OFF)$")


@dataclass(frozen=True)
class EmailTemplate:
    key: str
    klass: str
    audience: str                      # patient | staff | any
    subject: str
    body: str
    variables: dict[str, re.Pattern[str]] = field(default_factory=dict)
    exposure: str = "none"             # health information it may carry: none, by design

    @property
    def version(self) -> str:
        return version_of("email", self.subject + "\n" + self.body, self.variables)


def version_of(channel: str, wording: str, variables: dict[str, re.Pattern[str]]) -> str:
    gaps = ";".join(f"{k}={v.pattern}" for k, v in sorted(variables.items()))
    return hashlib.sha256(f"{channel}\n{wording}\n{gaps}".encode()).hexdigest()[:16]


_SIGN = "\n\nZoiko Access\n"

EMAIL_TEMPLATES: dict[str, EmailTemplate] = {t.key: t for t in (
    EmailTemplate(
        "verify_email", SECURITY, "any", "Verify your email for Zoiko Access",
        "Hello,\n\nPlease confirm this email address for your Zoiko Access account by opening "
        "this link (it works once and expires in 48 hours):\n\n{link}\n\n"
        "If you did not create an account, you can ignore this email." + _SIGN,
        {"link": _URL}),
    EmailTemplate(
        "reset_password", SECURITY, "any", "Reset your Zoiko Access password",
        "Hello,\n\nSomeone asked to reset the password for your Zoiko Access account. "
        "To choose a new one, open this link (it works once and expires in 1 hour):"
        "\n\n{link}\n\nIf this was not you, ignore this email: your password "
        "has not changed." + _SIGN,
        {"link": _URL}),
    EmailTemplate(
        "password_changed", SECURITY, "any", "Your Zoiko Access password was changed",
        "Hello,\n\nThe password of your Zoiko Access account was just changed and "
        "every other device was signed out.\n\nIf this was not you, reset your "
        "password now from the sign-in page and contact your clinic." + _SIGN),
    EmailTemplate(
        "email_changed", SECURITY, "any", "The email of your Zoiko Access account changed",
        "Hello,\n\nThe email address of your Zoiko Access account was just changed to "
        "{masked_address}.\n\nIf this was not you, contact your clinic and "
        "reset your password now." + _SIGN,
        {"masked_address": _MASKED}),
    EmailTemplate(
        "invite_staff", SECURITY, "staff",
        "You have been invited to {organization_name} on Zoiko Access",
        "Hello,\n\nYou have been added to {organization_name} on Zoiko Access as "
        "{role_label}. To choose your password and sign in, open this link (it works "
        "once and expires in 7 days):\n\n{link}\n\n"
        "To sign in, use organization '{tenant_name}' and login {login}."
        "\n\nIf you were not expecting this, ignore this email." + _SIGN,
        {"organization_name": _NAME, "role_label": _NAME, "link": _URL, "tenant_name": _NAME,
         "login": _EMAIL}),
    EmailTemplate(
        "two_step_changed", SECURITY, "any",
        "Two-step sign-in was {what} for your Zoiko Access account",
        "Hello,\n\nTwo-step sign-in was just {what} for your Zoiko Access account, and "
        "every other device was signed out.\n\nIf this was not you, contact your "
        "administrator now." + _SIGN,
        {"what": _SWITCHED}),
    EmailTemplate(
        "recovery_code_used", SECURITY, "any",
        "A recovery code was used on your Zoiko Access account",
        "Hello,\n\nOne of your two-step sign-in recovery codes was just used. "
        "{left} are left.\n\nIf this was not you, contact your administrator now." + _SIGN,
        {"left": _NUMBER}),
    EmailTemplate(
        "patient_update", SERVICE, "patient", "An update on Zoiko Access",
        "Hello,\n\n{headline}\n\nSign in to see the details: {app_url}/?page=notifications\n\n"
        "You are getting this because you turned on email updates. You can turn them off "
        "on the Notifications page." + _SIGN,
        {"headline": _SENTENCE, "app_url": _URL}),
    EmailTemplate(
        "critical_result", CLINICAL_ALERT, "staff", "A critical result is waiting for you",
        "A result you ordered has come back rated critical.\n\n"
        "Open the doctor app: {doctor_app_url}\n(or the staff app: {staff_app_url})\n",
        {"doctor_app_url": _URL, "staff_app_url": _URL}),
    EmailTemplate(
        "late_digest", ADMIN, "staff", "Running late at {name}: {day}",
        "{name} has work waiting longer than {hours} hours:\n\n{items}"
        "\n\nOpen the staff app: {app_url}\n",
        {"name": _NAME, "day": _DAY, "hours": _NUMBER, "items": _DIGEST, "app_url": _URL}),
    EmailTemplate(
        "hold_escalation", ADMIN, "staff",
        "Held partner requests need a decision at {name}: {day}",
        "{requests} been on hold for more than 3 days and need a decision (cancel, or keep "
        "waiting).\n\nOpen the staff app: {app_url}\n",
        {"name": _NAME, "day": _DAY, "requests": _REQUESTS, "app_url": _URL}),
    EmailTemplate(
        "incident_escalation", ADMIN, "staff", "A Zoiko incident needs you",
        "Hello,\n\nAn incident you are deputy for was not acknowledged in time. "
        "Sign in to see it: {app_url}/?page=incidents" + _SIGN,
        {"app_url": re.compile(r"^(https?://[A-Za-z0-9.:/_-]{1,200})?$")}),
    EmailTemplate(
        "support_access_requested", ADMIN, "staff",
        "Zoiko support asks for short access to your organization",
        "Hello,\n\nZoiko support ({requester}) asks for {minutes} minutes of access, for ticket "
        "{ticket}.\n\nWhat they would see: {scopes} (setup and status only, never patients).\n\n"
        "Approve or refuse it in the staff console, under Settings > Support access. If you did "
        "not expect this, refuse it." + _SIGN,
        {"requester": _NAME, "minutes": _NUMBER, "ticket": _TICKET, "scopes": _SCOPES}),
)}


def render_email(key: str, values: dict[str, str] | None = None) -> tuple[EmailTemplate, str, str]:
    """(template, subject, body). Refuses an unknown template, a missing or extra value, or a value
    that does not have its gap's shape."""
    template = EMAIL_TEMPLATES.get(key)
    if template is None:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["template"])
    given = values or {}
    if set(given) != set(template.variables):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["values"])
    for name, pattern in template.variables.items():
        if not isinstance(given[name], str) or not pattern.match(given[name]):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[f"values.{name}"])
    # str.format would read "{x.y}" or "{0}" in a value; values are inserted literally instead.
    subject, body = template.subject, template.body
    for name, value in given.items():
        subject = subject.replace("{" + name + "}", value)
        body = body.replace("{" + name + "}", value)
    return template, subject, body


def catalogue() -> list[dict[str, object]]:
    """Every message Zoiko can send, emails and texts, with its current version and wording."""
    from foundation.texts import TEMPLATES as TEXTS
    from foundation.texts import TEXT_CLASSES

    out: list[dict[str, object]] = [
        {"key": t.key, "channel": "email", "class": t.klass, "audience": t.audience,
         "subject": t.subject, "body": t.body, "variables": sorted(t.variables),
         "exposure": t.exposure, "version": t.version}
        for t in EMAIL_TEMPLATES.values()]
    for key, (wording, variables) in TEXTS.items():
        out.append({"key": key, "channel": "text", "class": TEXT_CLASSES.get(key, SERVICE),
                    "audience": "any", "subject": "", "body": wording,
                    "variables": sorted(variables), "exposure": "none",
                    "version": version_of("text", wording, variables)})
    from foundation.measures import catalogue_rows  # report measures (DEV-389)

    out.extend(catalogue_rows())
    return out


def current_version(channel: str, key: str) -> str | None:
    for row in catalogue():
        if row["channel"] == channel and row["key"] == key:
            return str(row["version"])
    return None
