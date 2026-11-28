"""Paging the on-call person through PagerDuty (decided 27 Nov 2026, DEV-400: PagerDuty for alerts).

What pages: an error-budget alert (DEV-388: fast burn, slow burn, budget used up), checked every
minute by the worker, and a serious incident nobody acknowledged in time (DEV-202). A page says
WHAT is wrong and which runbook to open; never a patient, a customer's name, a login or any free
text. PagerDuty's Events API v2 over HTTPS; the routing key comes only from the environment
(`PAGERDUTY_ROUTING_KEY`) and is never logged. With `PAGER_PROVIDER` empty (development), nothing
is sent and one line naming only the alert is logged.

Each alert has a stable dedup key per cell and day, so a repeated trigger is the same page; when
an alert clears, it is resolved.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from .timeutil import Clock, utc_now

log = logging.getLogger("zoiko.paging")
ENDPOINT = "https://events.pagerduty.com/v2/enqueue"
_open: set[str] = set()          # dedup keys this process triggered and has not resolved


def enabled() -> bool:
    return getattr(settings, "PAGER_PROVIDER", "") == "pagerduty"


def _send(body: dict[str, Any]) -> bool:
    key = getattr(settings, "PAGERDUTY_ROUTING_KEY", "")
    if not key:
        return False
    request = urllib.request.Request(  # noqa: S310 - a fixed https address
        ENDPOINT, data=json.dumps({**body, "routing_key": key}).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
            return bool(answer.status < 300)
    except (urllib.error.URLError, TimeoutError, OSError):
        log.warning("paging failed kind=%s", body.get("event_action", ""))
        return False


def trigger(dedup_key: str, summary: str, *, severity: str = "error",
            runbook: str = "") -> bool:
    """Open (or repeat) a page. `summary` must be fixed wording, never anything personal."""
    if not enabled():
        log.warning("page (not sent, development) key=%s", dedup_key)
        return False
    details: dict[str, Any] = {"runbook": runbook} if runbook else {}
    ok = _send({"event_action": "trigger", "dedup_key": dedup_key, "payload": {
        "summary": summary[:200], "source": f"zoiko-{settings.ZOIKO_CELL_ID}",
        "severity": severity, "custom_details": details}})
    if ok:
        _open.add(dedup_key)
    return ok


def resolve(dedup_key: str) -> bool:
    if not enabled():
        return False
    ok = _send({"event_action": "resolve", "dedup_key": dedup_key})
    if ok:
        _open.discard(dedup_key)
    return ok


def page_budget_alerts(clock: Clock | None = None) -> int:
    """For the worker, every minute: page each error-budget alert that is on, resolve the ones that
    cleared. Returns how many are on."""
    from . import error_budget

    day = utc_now(clock).date().isoformat()
    found = error_budget.summary(clock)
    on = set()
    for alert in found["alerts"]:
        key = f"zoiko-{settings.ZOIKO_CELL_ID}-{alert['alert']}-{day}"
        on.add(key)
        urgent = alert["alert"].endswith(("_exhausted", "_fast_burn"))
        severity = "critical" if urgent else "warning"
        trigger(key, f"Error budget alert: {alert['alert']}", severity=severity,
                runbook=alert["runbook"])
    for key in sorted(_open - on):
        if "-incident-" not in key:
            resolve(key)
    return len(on)
