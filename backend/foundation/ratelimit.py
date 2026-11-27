"""How often one caller may call the API (DEV-387, GHE-13 "rate control per partner, tenant,
endpoint ... and risk tier; defined fairness for safety-dependent traffic; Retry-After"; GHE-09
T11 "denial of service / cost exhaustion": rates and quotas; OWASP API4 unrestricted resource use).

Fixed one-minute windows, counted in the database (the one store every server process shares):
one upsert per check. A caller over its limit gets 429 RATE_LIMITED with Retry-After (the seconds
left in the window, never more than 60).

Limits (`RATE_LIMITS` in settings, per minute):
  ip           every API request from one network address
  ip_auth      sign-in, sign-up, password reset and the second step, from one network address
  actor_read   GET by one signed-in login
  actor_write  anything else by one signed-in login
  tenant       every request of one customer (all its logins together), EXCEPT safety-critical
               routes (`SAFETY_ROUTES`), so a flood elsewhere never blocks a doctor reviewing a
               result; those still count towards the login's own limits

Keys are keyed fingerprints (HMAC under SECRET_KEY): no network address or login is stored.

ZOIKO_RATE_LIMITING: enforce (refuse), report (allow, and log one line naming only the kind of
limit) or off. Production must enforce (release check). Unknown counts as enforce.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from .errors import ErrorCode, ZoikoError
from .timeutil import Clock, utc_now

log = logging.getLogger("zoiko.ratelimit")

ENFORCE, REPORT, OFF = "enforce", "report", "off"
WINDOW_SECONDS = 60
KEEP_FOR = dt.timedelta(minutes=10)

DEFAULT_LIMITS = {"ip": 600, "ip_auth": 20, "actor_read": 300, "actor_write": 60,
                  "tenant": 3000}

# Routes a clinician needs whatever else is happening at the clinic: never refused by the
# customer-wide cap (only by the login's own limits). GHE-13: "fairness for safety-dependent
# traffic".
SAFETY_ROUTES = frozenset({
    "exchange/requests/<str:request_id>/<str:step>",          # review a result
    "exchange/requests/<str:request_id>/result",
    "exchange/requests/<str:request_id>/dispute",
    "clinician/notes", "clinician/notes/<str:note_id>",
    "clinician/notes/<str:note_id>/<str:step>",
    "clinician/patients", "clinician/schedule",
    "people/<str:person_id>/death",
})


def mode() -> str:
    value = str(getattr(settings, "ZOIKO_RATE_LIMITING", ENFORCE)).strip().lower()
    return value if value in (ENFORCE, REPORT, OFF) else ENFORCE


def limit_for(kind: str) -> int:
    limits = getattr(settings, "RATE_LIMITS", None) or {}
    value = limits.get(kind, DEFAULT_LIMITS[kind])
    return int(value) if isinstance(value, int) and value > 0 else DEFAULT_LIMITS[kind]


def _fingerprint(kind: str, ident: str) -> str:
    key = hmac.new(settings.SECRET_KEY.encode(), b"zoiko-rate-limit-v1", hashlib.sha256).digest()
    return f"{kind}:" + hmac.new(key, ident.encode(), hashlib.sha256).hexdigest()[:40]


class RateLimited(ZoikoError):
    """429 RATE_LIMITED, carrying how long to wait (the error middleware sets Retry-After)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(ErrorCode.RATE_LIMITED)
        self.retry_after = max(1, min(int(retry_after), WINDOW_SECONDS))


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after: int = 0          # seconds, when refused
    kind: str = ""


def _count(key: str, start: dt.datetime) -> int:
    """Add one to this caller's current window and return the new count (one atomic upsert)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO foundation_ratewindow (key, window_start, count) VALUES (%s, %s, 1) "
            "ON CONFLICT (key, window_start) DO UPDATE "
            "SET count = foundation_ratewindow.count + 1 RETURNING count",
            [key, start])
        row = cursor.fetchone()
    return int(row[0]) if row else 1


def check(kinds: list[tuple[str, str]], clock: Clock | None = None) -> Verdict:
    """Count one request against each (kind, identity) and say whether it may go on. Every limit
    is counted, so a refused caller keeps using up its window."""
    current = mode()
    if current == OFF:
        return Verdict(True)
    now = utc_now(clock)
    start = now.replace(second=0, microsecond=0)
    left = max(1, WINDOW_SECONDS - now.second)
    over = ""
    for kind, ident in kinds:
        if _count(_fingerprint(kind, ident), start) > limit_for(kind) and not over:
            over = kind
    if not over:
        return Verdict(True)
    if current == REPORT:
        log.warning("rate_limit would_refuse kind=%s", over)
        return Verdict(True)
    return Verdict(False, min(left, WINDOW_SECONDS), over)


def forget_old(clock: Clock | None = None) -> int:
    """For the worker: delete windows that ended long ago."""
    from .models import RateWindow

    deleted, _ = RateWindow.objects.filter(window_start__lt=utc_now(clock) - KEEP_FOR).delete()
    return int(deleted)
