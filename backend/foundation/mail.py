"""Reliable email: queue it in the same transaction as the change, send it from the worker.

    queue_email(kind="verify_email", to_address=..., subject=..., body=...)  # inside a transaction
    send_due()                                                                # the worker loop

Why a queue: sending mail inside a web request means a slow or broken mail server breaks
sign-up, and a rolled-back change can still have sent its email. Queued in the same transaction,
the email exists only if the change committed; the worker sends it later and retries on failure.

Safety rules
- Header injection: addresses and subjects containing line breaks are refused.
- The body may hold a one-time link, so it is erased the moment the mail is sent, and the address is
  replaced by a masked form ("a***@example.org").
- Only the exception CLASS of a failure is kept, never its message (it can echo the address).
- Delivery is at least once: a crash after sending but before recording sends it again.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import connection, transaction

from .errors import ErrorCode, ZoikoError
from .models import MailState, QueuedEmail
from .timeutil import Clock, utc_now

MAX_ATTEMPTS = 5
CLAIM_FOR = dt.timedelta(minutes=5)
_ADDRESS = re.compile(r"^[^@\s,;<>]{1,64}@[^@\s,;<>]{1,190}\.[^@\s,;<>]{2,}$")
_KIND = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


class MailError(RuntimeError):
    """The mail queue was used in a way that would break its guarantees."""


def clean_address(address: str) -> str:
    """Lower-case and validate an email address, or raise VALIDATION_FAILED."""
    cleaned = address.strip().lower()
    if len(cleaned) > 254 or not _ADDRESS.match(cleaned):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["email"])
    return cleaned


def mask_address(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def queue_email(*, kind: str, to_address: str, subject: str, body: str) -> QueuedEmail:
    """Queue one email. Call inside the transaction that makes it necessary."""
    if not connection.in_atomic_block:
        raise MailError("queue_email() must run inside the same transaction as the change")
    address = to_address.strip().lower()
    if not _KIND.match(kind) or len(address) > 254 or not _ADDRESS.match(address):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["email"])
    if "\n" in subject or "\r" in subject or not subject.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["subject"])
    if len(subject) > 200 or len(body) > 5000:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["body"])
    return QueuedEmail.objects.create(
        kind=kind, to_address=address, subject=subject.strip(), body=body)


@dataclass(frozen=True)
class SendResult:
    sent: int
    retrying: int
    failed: int


def _backoff(attempts: int) -> dt.timedelta:
    return dt.timedelta(seconds=min(3600, 30 * 2 ** max(attempts - 1, 0)))


def send_due(*, limit: int = 20, clock: Clock | None = None) -> SendResult:
    """Send due emails. Claims rows in a short transaction, sends OUTSIDE it (no database
    transaction is held open during the network call), then records the result."""
    now = utc_now(clock)
    with transaction.atomic():
        due = (
            QueuedEmail.objects.select_for_update(skip_locked=True)
            .filter(next_attempt_at__lte=now)
            .filter(state=MailState.QUEUED)
            .order_by("created_at")[:limit]
        )
        stuck = (
            QueuedEmail.objects.select_for_update(skip_locked=True)
            .filter(state=MailState.SENDING, locked_until__lt=now)[:limit]
        )
        rows = list(due) + list(stuck)
        for row in rows:
            row.state, row.attempts = MailState.SENDING, row.attempts + 1
            row.locked_until = now + CLAIM_FOR
            row.save(update_fields=["state", "attempts", "locked_until", "updated_at"])
        claimed = [(row.pk, row.to_address, row.subject, row.body, row.attempts) for row in rows]

    sent = retrying = failed = 0
    for pk, address, subject, body, attempts in claimed:
        try:
            EmailMessage(subject=subject, body=body, to=[address],
                         from_email=settings.DEFAULT_FROM_EMAIL).send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001 - any failure means "try again later"
            error = type(exc).__name__[:64]
            if attempts >= MAX_ATTEMPTS:
                QueuedEmail.objects.filter(pk=pk).update(
                    state=MailState.FAILED, last_error=error, locked_until=None)
                failed += 1
            else:
                QueuedEmail.objects.filter(pk=pk).update(
                    state=MailState.QUEUED, last_error=error, locked_until=None,
                    next_attempt_at=now + _backoff(attempts))
                retrying += 1
        else:
            # Wipe the body (it may hold a one-time link) and keep only a masked address.
            QueuedEmail.objects.filter(pk=pk).update(
                state=MailState.SENT, sent_at=now, body="", to_address=mask_address(address),
                last_error="", locked_until=None)
            sent += 1
    return SendResult(sent, retrying, failed)


def mail_stats() -> dict[str, int]:
    """Cell-wide numbers only, for the platform console."""
    rows = QueuedEmail.objects
    return {
        "queued": rows.filter(state__in=[MailState.QUEUED, MailState.SENDING]).count(),
        "failed": rows.filter(state=MailState.FAILED).count(),
    }
