"""Text messages (SMS): queued in the change's own transaction, sent later by the worker (DEV-194).

A text can be read over a shoulder, on a lock screen, or by whoever holds the phone, so a text NEVER
carries care details. That rule is enforced by construction: a text is built only from one of the
approved templates below, with only that template's named values, each checked against a strict
pattern (GHE-16 TC05 and TC49). There is no way to put free text into a text message.

Providers: "twilio" sends through Twilio's REST API (account values come from the environment and
are never logged); "console" is the development stand-in, which sends nothing and logs a masked
number and the template name; "" (the default) sends nothing at all and leaves texts waiting.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.db import connection, transaction

from foundation.errors import ErrorCode, ZoikoError
from foundation.models import MailState, ProviderCircuit, QueuedText
from foundation.timeutil import Clock, utc_now

log = logging.getLogger("zoiko.texts")

# The ONLY texts that can be sent. Changing this list is a reviewed code change, not data.
TEMPLATES: dict[str, tuple[str, dict[str, re.Pattern[str]]]] = {
    "update": ("There is an update for you on Zoiko Access. Sign in to see it.", {}),
    "code": ("Your Zoiko Access code is {code}. It expires in 10 minutes. Never share it.",
             {"code": re.compile(r"^[0-9]{4,8}$")}),
    # For a clinician, about a critical result they ordered (DEV-334): never what, never whom.
    "urgent": ("A result needs your attention on Zoiko Access. Please sign in now.", {}),
    # For a clinic administrator: held partner requests need a decision (DEV-336).
    "urgent_staff": ("Something at your clinic needs a decision on Zoiko Access. Please sign in.",
                     {}),
}
# What kind of message each text is (DEV-386, the approval screen): see message_templates.py.
TEXT_CLASSES = {"update": "service", "code": "security", "urgent": "clinical_alert",
                "urgent_staff": "admin"}
_NUMBER = re.compile(r"^\+[1-9][0-9]{7,14}$")          # international (E.164) only
MAX_ATTEMPTS = 5
BREAK_AFTER = 5                                        # failures in a row (DEV-200)
BREAK_FOR = dt.timedelta(minutes=5)
CIRCUIT = "texts"
CLAIM_FOR = dt.timedelta(minutes=5)

# (to number, body) -> the provider's own id for the message, if it gives one; raises on failure
Provider = Callable[[str, str], str | None]


class TextError(RuntimeError):
    pass


def mask_number(number: str) -> str:
    return number[:3] + "*" * max(len(number) - 5, 0) + number[-2:] if len(number) > 5 else "***"


def queue_text(*, kind: str, to_number: str, template: str,
               values: dict[str, Any] | None = None) -> QueuedText:
    """Queue one text built from an approved template. Call inside the change's transaction.
    Refuses an unknown template, a missing or extra value, or a value of the wrong shape."""
    if not connection.in_atomic_block:
        raise TextError("queue_text() must run inside the same transaction as the change")
    if template not in TEMPLATES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["template"])
    wording, allowed = TEMPLATES[template]
    given = values or {}
    if set(given) != set(allowed):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["values"])
    for name, pattern in allowed.items():
        if not isinstance(given[name], str) or not pattern.match(given[name]):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[f"values.{name}"])
    number = (to_number or "").replace(" ", "")
    if not _NUMBER.match(number):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["to_number"])
    body = wording.format(**given)
    from .message_templates import version_of
    from .template_policy import HOLD, decide

    version = version_of("text", wording, allowed)
    held = decide("text", template, version) == HOLD     # DEV-386: approved wording only
    return QueuedText.objects.create(
        kind=kind[:32], to_number=number, template=template, body=body,
        template_version=version, state=MailState.HELD if held else MailState.QUEUED,
        content_digest=hashlib.sha256(f"{template}\n{body}".encode()).hexdigest())


# ------------------------------------------------------------------ providers
def _console(number: str, body: str) -> str | None:
    log.info("text (not sent, development) to %s: %d characters", mask_number(number), len(body))
    return None


def _twilio(number: str, body: str) -> str | None:
    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    sender = getattr(settings, "TWILIO_FROM", "")
    if not (sid and token and sender):
        raise TextError("twilio is not configured")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(sid)}/Messages.json"
    field = "MessagingServiceSid" if sender.startswith("MG") else "From"
    fields = {"To": number, field: sender, "Body": body}
    callback = getattr(settings, "TWILIO_STATUS_URL", "")
    if callback:
        fields["StatusCallback"] = callback          # delivery receipts come back here (DEV-199)
    data = urllib.parse.urlencode(fields).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    request = urllib.request.Request(  # noqa: S310 - a fixed https address
        url, data=data, method="POST", headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
        if answer.status >= 300:
            raise TextError(f"twilio answered {answer.status}")
        sid = json.load(answer).get("sid")
        return str(sid)[:64] if sid else None


def provider() -> Provider | None:
    name = getattr(settings, "TEXT_PROVIDER", "")
    return {"twilio": _twilio, "console": _console}.get(name)


# ------------------------------------------------------------------ the circuit breaker (DEV-200)
def _breaker_open(now: dt.datetime) -> bool:
    row = ProviderCircuit.objects.filter(name=CIRCUIT).first()
    return bool(row and row.open_until and row.open_until > now)


def _note(ok: bool, now: dt.datetime) -> bool:
    """Record one outcome. Returns True when the breaker is (now) open."""
    with transaction.atomic():
        row, _ = ProviderCircuit.objects.select_for_update().get_or_create(name=CIRCUIT)
        if ok:
            if row.failures or row.open_until:
                row.failures, row.open_until, row.last_change = 0, None, now
                row.save(update_fields=["failures", "open_until", "last_change"])
            return False
        row.failures += 1
        if row.failures >= BREAK_AFTER and not (row.open_until and row.open_until > now):
            row.open_until, row.last_change = now + BREAK_FOR, now
            row.opened_count += 1
            log.warning("text provider breaker opened after %d failures", row.failures)
        row.save(update_fields=["failures", "open_until", "opened_count", "last_change"])
        return bool(row.open_until and row.open_until > now)


def circuit_state(clock: Clock | None = None) -> dict[str, Any]:
    """For the platform screen: the breaker, and how many texts are in each state. Counts only."""
    now = utc_now(clock)
    row = ProviderCircuit.objects.filter(name=CIRCUIT).first()
    counts = {state: QueuedText.objects.filter(state=state).count()
              for state in (MailState.QUEUED, MailState.SENT, MailState.FAILED)}
    delivery = {status: QueuedText.objects.filter(delivery_status=status).count()
                for status in ("delivered", "undelivered", "failed")}
    return {
        "provider": getattr(settings, "TEXT_PROVIDER", "") or "none",
        "breaker": "open" if row and row.open_until and row.open_until > now else "closed",
        "failures_in_a_row": row.failures if row else 0,
        "open_until": row.open_until.isoformat() if row and row.open_until and
        row.open_until > now else None,
        "times_opened": row.opened_count if row else 0,
        "queue": {str(k): v for k, v in counts.items()},
        "receipts": delivery,
    }


# ------------------------------------------------------------------ the worker
def send_due_texts(*, limit: int = 20, clock: Clock | None = None,
                   send: Provider | None = None) -> int:
    """Send due texts. With no provider configured nothing is sent and texts keep waiting. Claims
    rows briefly, sends outside the transaction, then records the result. Returns how many went."""
    deliver = send or provider()
    if deliver is None:
        return 0
    now = utc_now(clock)
    if _breaker_open(now):
        return 0                                   # the provider is resting: texts wait (DEV-200)
    with transaction.atomic():
        rows = list(QueuedText.objects.select_for_update(skip_locked=True).filter(
            state=MailState.QUEUED, next_attempt_at__lte=now).order_by("created_at")[:limit])
        for row in rows:
            row.state, row.attempts = MailState.SENDING, row.attempts + 1
            row.locked_until = now + CLAIM_FOR
            row.save(update_fields=["state", "attempts", "locked_until", "updated_at"])
        claimed = [(r.pk, r.to_number, r.body, r.attempts) for r in rows]
    sent = 0
    tripped = False
    for pk, number, body, attempts in claimed:
        if tripped:
            # The breaker opened during this batch: put the rest back untouched, try not used up.
            QueuedText.objects.filter(pk=pk).update(
                state=MailState.QUEUED, attempts=attempts - 1, locked_until=None)
            continue
        try:
            ref = deliver(number, body)
        except Exception as exc:  # noqa: BLE001 - any failure means "try again later"
            tripped = _note(False, now)
            error = type(exc).__name__[:64]
            if attempts >= MAX_ATTEMPTS:
                QueuedText.objects.filter(pk=pk).update(
                    state=MailState.FAILED, last_error=error, locked_until=None,
                    body="", to_number=mask_number(number))
            else:
                QueuedText.objects.filter(pk=pk).update(
                    state=MailState.QUEUED, last_error=error, locked_until=None,
                    next_attempt_at=now + dt.timedelta(seconds=min(3600, 30 * 2 ** attempts)))
        else:
            _note(True, now)
            QueuedText.objects.filter(pk=pk).update(
                state=MailState.SENT, sent_at=now, body="", to_number=mask_number(number),
                last_error="", locked_until=None, provider_ref=ref or "")
            sent += 1
    return sent


# ------------------------------------------------------------------ delivery receipts (DEV-199)
# How far a text has got. A receipt only ever moves it FORWARD: a late "sent" arriving after
# "delivered" changes nothing, and a repeat changes nothing (GHE-16 TC21).
_RANK = {"accepted": 1, "queued": 1, "scheduled": 1, "sending": 2, "sent": 3,
         "delivered": 4, "undelivered": 4, "failed": 4, "read": 5}


def twilio_signature(url: str, params: dict[str, str], token: str) -> str:
    """Twilio's request signature: HMAC-SHA1 over the URL and the sorted form fields, base64."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def record_receipt(*, url: str, params: dict[str, str], signature: str,
                   clock: Clock | None = None) -> int:
    """Check a Twilio status callback and apply it. Returns the HTTP status to answer with:
    403 when the signature is wrong (nothing is read), 200 otherwise, including for a message we do
    not know (so nothing is learned by guessing ids)."""
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    if not token or not signature or not hmac.compare_digest(
            twilio_signature(url, params, token), signature):
        return 403
    sid = params.get("MessageSid", "")[:64]
    status = params.get("MessageStatus", "")
    if not sid or status not in _RANK:
        return 200
    with transaction.atomic():
        row = QueuedText.objects.select_for_update().filter(provider_ref=sid).first()
        if row is None:
            return 200
        if _RANK[status] > _RANK.get(row.delivery_status, 0):
            row.delivery_status, row.delivery_updated_at = status, utc_now(clock)
            row.save(update_fields=["delivery_status", "delivery_updated_at", "updated_at"])
    return 200
