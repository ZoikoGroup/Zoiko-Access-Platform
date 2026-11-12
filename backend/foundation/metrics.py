"""Service measurements and the two service-level indicators (docs GHE-18 section 05, WF-19).

What is measured: how many requests each ROUTE served, whether they succeeded, and how long they
took. A route is the URL pattern (`api/v1/bookings/<str:id>/confirm`), never the real address, so no
patient, booking or customer reference can enter a label. There are no tenant labels and no query
strings: this is the "safe aggregate" the specification allows to leave a region.

The two indicators (the targets are the specification's PROPOSED starting numbers, DEV-130):
  availability  share of requests that did NOT fail with a server error (4xx is a valid answer)
  latency       share of requests answered within 750 ms (target 95%, i.e. "p95 under 750 ms")
Health probes and the metrics endpoint itself are excluded.

Known limits, stated plainly: counts live in the memory of ONE process (several web workers each
report their own numbers; a restart resets them) and the longest window is one hour. The
specification's 30-day budget needs a metrics store, which does not exist yet.
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Any

AVAILABILITY_TARGET = 0.999          # proposed (GHE-18): 99.9%
LATENCY_THRESHOLD = 0.75             # proposed (GHE-18): p95 under 750 ms ...
LATENCY_TARGET = 0.95                # ... which is "95% of requests within 750 ms"
BUCKETS = (0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0)
KEEP_MINUTES = 61
MAX_ROUTES = 200
EXCLUDED = ("healthz", "readyz", "metrics")

_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_/<>:.\-]")
_lock = threading.Lock()
_minutes: dict[int, dict[str, int]] = {}                       # minute -> totals
_routes: dict[str, dict[str, Any]] = {}                        # route -> cumulative numbers


def reset() -> None:
    """For tests only."""
    with _lock:
        _minutes.clear()
        _routes.clear()


def _route_label(route: str) -> str:
    clean = _SAFE_LABEL.sub("?", (route or "unmatched")[:80])
    if clean in _routes or len(_routes) < MAX_ROUTES:
        return clean
    return "other"                                              # a guard against label explosion


def record(route: str, status: int, seconds: float, now: float | None = None) -> None:
    if any(route.strip("/").endswith(name) for name in EXCLUDED):
        return
    stamp = int((now if now is not None else time.time()) // 60)
    fast = seconds <= LATENCY_THRESHOLD
    with _lock:
        label = _route_label(route)
        bucket = _minutes.setdefault(stamp, {"total": 0, "server_errors": 0, "fast": 0})
        bucket["total"] += 1
        bucket["server_errors"] += 1 if status >= 500 else 0
        bucket["fast"] += 1 if fast else 0
        for old in [m for m in _minutes if m < stamp - KEEP_MINUTES]:
            del _minutes[old]
        entry = _routes.setdefault(label, {
            "classes": defaultdict(int), "buckets": [0] * len(BUCKETS), "count": 0, "sum": 0.0})
        entry["classes"][f"{status // 100}xx"] += 1
        entry["count"] += 1
        entry["sum"] += seconds
        for i, edge in enumerate(BUCKETS):
            if seconds <= edge:
                entry["buckets"][i] += 1


def _indicator(good: int, total: int, target: float) -> dict[str, Any]:
    if total == 0:
        return {"value": None, "target": target, "burn_rate": None, "meeting": None}
    value = good / total
    allowed = 1 - target
    burn = (1 - value) / allowed if allowed > 0 else None
    return {"value": round(value, 5), "target": target,
            "burn_rate": None if burn is None else round(burn, 2), "meeting": value >= target}


def slo(minutes: int, now: float | None = None) -> dict[str, Any]:
    """The two indicators over the last `minutes` (at most 60) as seen by this process."""
    stamp = int((now if now is not None else time.time()) // 60)
    window = min(max(minutes, 1), 60)
    total = errors = fast = 0
    with _lock:
        for m, numbers in _minutes.items():
            if stamp - window < m <= stamp:
                total += numbers["total"]
                errors += numbers["server_errors"]
                fast += numbers["fast"]
    return {"window_minutes": window, "requests": total,
            "availability": _indicator(total - errors, total, AVAILABILITY_TARGET),
            "latency": _indicator(fast, total, LATENCY_TARGET)}


def routes_summary() -> list[dict[str, Any]]:
    """The busiest routes, with how many failed (a route name only, never a real address)."""
    with _lock:
        rows = [{"route": r, "requests": e["count"], "server_errors": e["classes"].get("5xx", 0),
                 "mean_seconds": round(e["sum"] / e["count"], 4) if e["count"] else 0.0}
                for r, e in _routes.items()]
    return sorted(rows, key=lambda r: -r["requests"])[:25]


def prometheus_text() -> str:
    """The cumulative numbers in the standard text format for a monitoring system to collect."""
    lines = ["# HELP zoiko_http_requests_total Requests served, by route and status class.",
             "# TYPE zoiko_http_requests_total counter"]
    with _lock:
        for route, e in sorted(_routes.items()):
            for cls, n in sorted(e["classes"].items()):
                lines.append(f'zoiko_http_requests_total{{route="{route}",class="{cls}"}} {n}')
        lines += ["# HELP zoiko_http_request_seconds Time to answer, by route.",
                  "# TYPE zoiko_http_request_seconds histogram"]
        for route, e in sorted(_routes.items()):
            for edge, n in zip(BUCKETS, e["buckets"], strict=True):
                lines.append(
                    f'zoiko_http_request_seconds_bucket{{route="{route}",le="{edge}"}} {n}')
            lines.append(
                f'zoiko_http_request_seconds_bucket{{route="{route}",le="+Inf"}} {e["count"]}')
            lines.append(f'zoiko_http_request_seconds_sum{{route="{route}"}} {round(e["sum"], 4)}')
            lines.append(f'zoiko_http_request_seconds_count{{route="{route}"}} {e["count"]}')
    return "\n".join(lines) + "\n"
