"""The 30-day error budget, burn-rate alerts and the runbook each alert points to (DEV-388,
GHE-18 section 05 and the error-budget policy; GHE-20 operations readiness).

GHE-18: "availability = (eligible requests completed with valid result within defined threshold) /
(all eligible valid requests) over a rolling 30-day window. Count controlled 5xx, timeouts and bad
degraded responses as failures ... Error-budget policy must suspend risky releases when approved
thresholds are exhausted, with a safety-critical override controlled by Clinical Safety/SRE."

The two indicators and their PROPOSED targets are those of foundation/metrics.py (DEV-130):
availability 99.9% (no server error) and latency 95% within 750 ms. Every request is counted per
minute in the database (`SloMinute`), so the budget covers every web process and survives
restarts. A budget is "used" by the failures beyond what the target allows:

  allowed failures = (1 - target) x requests in the window
  used             = failures / allowed failures            (1.0 = exhausted)

With fewer than MIN_REQUESTS requests in 30 days there is not enough traffic to judge: the state
is "not_enough_traffic", never "exhausted" (a handful of errors on a quiet day is not an outage).

Burn rate = the failure rate over a short window / the rate the target allows. Two alerts, the
usual multi-window pair: fast (1 hour, burn >= 14.4: 2% of the month's budget in an hour) and slow
(6 hours, burn >= 6). Counting can be switched off (ZOIKO_SLO_RECORDING, tests do), never in
production.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Sum

from . import metrics
from .models import SloMinute
from .timeutil import Clock, utc_now

WINDOW = dt.timedelta(days=30)
KEEP_FOR = dt.timedelta(days=35)
MIN_REQUESTS = 1000
FAST_BURN = (dt.timedelta(hours=1), 14.4)
SLOW_BURN = (dt.timedelta(hours=6), 6.0)
WARNING_AT = 0.75                                  # three quarters of the budget used

# Each alert, and the runbook (docs/runbooks/) that says what to do.
RUNBOOKS = {
    "availability_fast_burn": "docs/runbooks/01-server-errors.md",
    "availability_slow_burn": "docs/runbooks/01-server-errors.md",
    "availability_exhausted": "docs/runbooks/08-error-budget-exhausted.md",
    "latency_fast_burn": "docs/runbooks/02-slow-responses.md",
    "latency_slow_burn": "docs/runbooks/02-slow-responses.md",
    "latency_exhausted": "docs/runbooks/08-error-budget-exhausted.md",
}


def recording() -> bool:
    return bool(getattr(settings, "ZOIKO_SLO_RECORDING", True))


def count(route: str, status: int, seconds: float, clock: Clock | None = None) -> None:
    """Add one request to this minute (the same exclusions as foundation/metrics.py)."""
    if not recording() or any(route.strip("/").endswith(n) for n in metrics.EXCLUDED):
        return
    minute = utc_now(clock).replace(second=0, microsecond=0)
    error = 1 if status >= 500 else 0
    fast = 1 if seconds <= metrics.LATENCY_THRESHOLD else 0
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO foundation_slominute (minute, total, server_errors, fast) "
            "VALUES (%s, 1, %s, %s) ON CONFLICT (minute) DO UPDATE SET "
            "total = foundation_slominute.total + 1, "
            "server_errors = foundation_slominute.server_errors + EXCLUDED.server_errors, "
            "fast = foundation_slominute.fast + EXCLUDED.fast", [minute, error, fast])


def _sums(since: dt.datetime) -> tuple[int, int, int]:
    got = SloMinute.objects.filter(minute__gte=since).aggregate(
        total=Sum("total"), errors=Sum("server_errors"), fast=Sum("fast"))
    return int(got["total"] or 0), int(got["errors"] or 0), int(got["fast"] or 0)


def _budget(failures: int, total: int, target: float) -> dict[str, Any]:
    allowed = (1 - target) * total
    used = failures / allowed if allowed > 0 else 0.0
    if total < MIN_REQUESTS:
        state = "not_enough_traffic"
    elif used >= 1:
        state = "exhausted"
    elif used >= WARNING_AT:
        state = "warning"
    else:
        state = "healthy"
    return {"target": target, "requests": total, "failures": failures,
            "allowed_failures": round(allowed, 1), "used": round(used, 3),
            "remaining": round(max(0.0, 1 - used), 3), "state": state}


def _burn(window: dt.timedelta, now: dt.datetime) -> dict[str, float]:
    total, errors, fast = _sums(now - window)
    if total == 0:
        return {"availability": 0.0, "latency": 0.0, "requests": 0}
    return {
        "availability": round((errors / total) / (1 - metrics.AVAILABILITY_TARGET), 2),
        "latency": round(((total - fast) / total) / (1 - metrics.LATENCY_TARGET), 2),
        "requests": total,
    }


def summary(clock: Clock | None = None) -> dict[str, Any]:
    """Both budgets over 30 days, the burn rates, and the alerts that are on with their runbooks."""
    now = utc_now(clock)
    total, errors, fast = _sums(now - WINDOW)
    budgets = {
        "availability": _budget(errors, total, metrics.AVAILABILITY_TARGET),
        "latency": _budget(total - fast, total, metrics.LATENCY_TARGET),
    }
    fast_burn, slow_burn = _burn(FAST_BURN[0], now), _burn(SLOW_BURN[0], now)
    alerts = []
    for name, numbers in budgets.items():
        if numbers["state"] == "exhausted":
            alerts.append(f"{name}_exhausted")
        if fast_burn[name] >= FAST_BURN[1] and fast_burn["requests"] >= 100:
            alerts.append(f"{name}_fast_burn")
        elif slow_burn[name] >= SLOW_BURN[1] and slow_burn["requests"] >= 300:
            alerts.append(f"{name}_slow_burn")
    return {
        "window_days": WINDOW.days, "budgets": budgets,
        "burn": {"last_hour": fast_burn, "last_6_hours": slow_burn},
        "alerts": [{"alert": a, "runbook": RUNBOOKS[a]} for a in alerts],
        "exhausted": any(b["state"] == "exhausted" for b in budgets.values()),
        "recording": recording(),
    }


def forget_old(clock: Clock | None = None) -> int:
    """For the worker: minutes older than 35 days."""
    deleted, _ = SloMinute.objects.filter(minute__lt=utc_now(clock) - KEEP_FOR).delete()
    return int(deleted)
