"""Is this exact message wording approved? The decision taken before every email and text is
queued (DEV-386, GHE-16: "permitted template and notification gating"; policy returns ALLOW,
HOLD_FOR_REVIEW or DENY, "every decision versioned and traceable").

  ALLOW            the template version is approved by a safety officer AND, separately, privacy
  HOLD_FOR_REVIEW  it is not (yet): the message is kept with state "held" and sent the moment
                   that version is approved (`release_held`), so nothing is lost
  DENY             an unknown template or a value outside its gap's shape (raised by the renderer)

ZOIKO_TEMPLATE_GATING: enforce (hold unapproved), report (send, and log one line naming only the
template, version and channel) or off. Production must enforce (release check). Unknown counts as
enforce. Every queued message records its template key and version.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import MailState, QueuedEmail, QueuedText, TemplateApproval
from foundation.timeutil import Clock, utc_now

log = logging.getLogger("zoiko.templates")

ENFORCE, REPORT, OFF = "enforce", "report", "off"
ALLOW, HOLD = "allow", "hold"
CLINICAL, PRIVACY = "clinical", "privacy"


def mode() -> str:
    value = str(getattr(settings, "ZOIKO_TEMPLATE_GATING", ENFORCE)).strip().lower()
    return value if value in (ENFORCE, REPORT, OFF) else ENFORCE


def is_approved(channel: str, key: str, version: str) -> bool:
    return TemplateApproval.objects.filter(
        cell_id=settings.ZOIKO_CELL_ID, channel=channel, key=key, version=version,
        approved_at__isnull=False).exists()


def decide(channel: str, key: str, version: str) -> str:
    """ALLOW or HOLD for one message about to be queued."""
    current = mode()
    if current == OFF or is_approved(channel, key, version):
        return ALLOW
    if current == REPORT:
        log.warning("template_gating would_hold channel=%s template=%s version=%s",
                    channel, key, version)
        return ALLOW
    return HOLD


def release_held(channel: str, key: str, version: str, clock: Clock | None = None) -> int:
    """Send what was held for this exact version, now that it is approved. Returns how many."""
    model: Any = QueuedEmail if channel == "email" else QueuedText
    rows = model.objects.filter(state=MailState.HELD, template_version=version)
    if channel == "email":
        rows = rows.filter(template_key=key)
    else:
        rows = rows.filter(template=key)
    return int(rows.update(state=MailState.QUEUED, next_attempt_at=utc_now(clock)))


def approve(*, channel: str, key: str, as_role: str, actor_ref: str,
            clock: Clock | None = None) -> dict[str, Any]:
    """One of the two sign-offs of the CURRENT version of a template. The same person can never
    give both. When the second is given, the version is approved and its held messages go."""
    from foundation.message_templates import catalogue

    found = next((t for t in catalogue() if t["channel"] == channel and t["key"] == key), None)
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if as_role not in (CLINICAL, PRIVACY):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["role"])
    now = utc_now(clock)
    ctx = require_context()
    with transaction.atomic():
        row, _ = TemplateApproval.objects.select_for_update().get_or_create(
            cell_id=settings.ZOIKO_CELL_ID, channel=channel, key=key,
            version=str(found["version"]),
            defaults={"wording": _wording(found), "tenant_id": ctx.tenant_id})
        if row.approved_at is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["approved"])
        other = row.privacy_by_ref if as_role == CLINICAL else row.clinical_by_ref
        mine = row.clinical_by_ref if as_role == CLINICAL else row.privacy_by_ref
        if mine:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=[as_role])   # already given
        if other == actor_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)          # never both sign-offs
        if as_role == CLINICAL:
            row.clinical_by_ref, row.clinical_at = actor_ref, now
        else:
            row.privacy_by_ref, row.privacy_at = actor_ref, now
        released = 0
        if row.clinical_by_ref and row.privacy_by_ref:
            row.approved_at = now
        row.save()
        if row.approved_at is not None:
            released = release_held(channel, key, row.version, clock)
    return {"key": key, "channel": channel, "version": row.version,
            "approved": row.approved_at is not None, "released": released}


def _wording(t: dict[str, Any]) -> str:
    return (f"{t['subject']}\n\n{t['body']}" if t["subject"] else str(t["body"]))


def listing(labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every template, its current version, who signed it off, what is held for it, and the
    wording last approved (so the screen can show what changed)."""
    from foundation.message_templates import catalogue

    names = labels or {}
    rows = {(a.channel, a.key, a.version): a for a in TemplateApproval.objects.filter(
        cell_id=settings.ZOIKO_CELL_ID)}
    out = []
    for t in catalogue():
        here = rows.get((str(t["channel"]), str(t["key"]), str(t["version"])))
        previous = max((a for (c, k, _), a in rows.items()
                        if c == t["channel"] and k == t["key"] and a.approved_at is not None
                        and a.version != t["version"]),
                       key=lambda a: a.approved_at or utc_now(), default=None)
        if t["channel"] == "email":
            held = QueuedEmail.objects.filter(state=MailState.HELD, template_key=t["key"]).count()
        elif t["channel"] == "text":
            held = QueuedText.objects.filter(state=MailState.HELD, template=t["key"]).count()
        else:
            held = 0                       # a report measure (DEV-389): nothing is queued
        out.append({
            **t, "wording": _wording(t),
            "approved": bool(here and here.approved_at),
            "clinical_by": names.get(here.clinical_by_ref, here.clinical_by_ref)
            if here and here.clinical_by_ref else "",
            "privacy_by": names.get(here.privacy_by_ref, here.privacy_by_ref)
            if here and here.privacy_by_ref else "",
            "approved_at": here.approved_at.isoformat() if here and here.approved_at else None,
            "previous_wording": previous.wording if previous else None,
            "held": held,
        })
    return out
