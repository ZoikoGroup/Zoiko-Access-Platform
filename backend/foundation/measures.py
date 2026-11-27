"""Report measures: every number a clinic's report shows has an approved definition, small numbers
are suppressed, and each report run is reproducible (DEV-389, GHE-10 sections 10 and 11, DAT-11).

GHE-10: "No KPI may be displayed without an approved measure definition. Metrics are metadata plus
computation"; analytics need "small-cell and differencing protection"; "All report results are
reproducible from versioned code, inputs, policies, periods, denominator logic, suppression rules".

* A MEASURE names what is counted (numerator), out of what (denominator), what is left out
  (exclusions), and which parts of the report it covers. Its VERSION is a fingerprint of all that,
  plus the version of the code that computes it, so a change to either is a new version that must
  be approved again (a safety officer and, separately, privacy: foundation/template_policy.py,
  channel "measure").
* SMALL NUMBERS: a count from 1 to K-1 is shown as None ("fewer than K"); a rate or an average
  built from fewer than K cases is withheld; and within a group whose total is shown, when exactly
  one number is suppressed a second one is suppressed too (the smallest other non-zero one), so the
  hidden one cannot be worked out by subtraction ("differencing"). Zero is shown: it identifies
  nobody.
* ZOIKO_MEASURE_GATING: enforce (a part of the report whose measure is not approved is withheld),
  report (shown, and logged) or off. Production must enforce.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings

log = logging.getLogger("zoiko.measures")

SMALL_CELL_K = 5                 # the approved rule, in every definition; never lower in production
ENFORCE, REPORT, OFF = "enforce", "report", "off"


def small_cell_k() -> int:
    """The threshold in force: REPORT_SMALL_CELL_K, 5 unless set. Only tests of the COUNTING use 1
    (nothing hidden); the release check refuses a real server below 5."""
    value = getattr(settings, "REPORT_SMALL_CELL_K", SMALL_CELL_K)
    usable = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    return int(value) if usable else SMALL_CELL_K


@dataclass(frozen=True)
class Measure:
    key: str
    title: str
    numerator: str
    denominator: str
    exclusions: str
    covers: tuple[str, ...]         # report parts: "section.part"
    code_version: str               # bump when the computing code changes meaning

    @property
    def definition(self) -> str:
        return (f"{self.title}\nCounts: {self.numerator}\nOut of: {self.denominator}\n"
                f"Leaves out: {self.exclusions}\nSmall numbers: counts 1 to {SMALL_CELL_K - 1} "
                f"are not shown; rates and averages need at least {SMALL_CELL_K} cases.\n"
                f"Computed by: {self.code_version}")

    @property
    def version(self) -> str:
        return hashlib.sha256(f"measure\n{self.definition}\n{self.covers}".encode()
                              ).hexdigest()[:16]


MEASURES: dict[str, Measure] = {m.key: m for m in (
    Measure("booking_volume", "Appointments booked",
            "appointments booked at this organization in the period, by their status now, by day, "
            "by weekday and by service", "none (a count)",
            "slots never booked; other organizations",
            ("bookings.total", "bookings.status", "bookings.per_day", "bookings.by_weekday",
             "bookings.busiest_services"), "scheduling.report v1"),
    Measure("booking_cancellations", "Why appointments were cancelled",
            "cancelled appointments of the period, by the reason given", "none (a count)",
            "appointments not cancelled", ("bookings.cancel_reasons",), "scheduling.report v1"),
    Measure("attendance", "Attendance",
            "appointments of the period marked attended, not attended, or not recorded, in total "
            "and by doctor", "none (a count)", "appointments still to come; cancelled ones",
            ("bookings.attendance", "bookings.by_doctor"), "scheduling.report v1"),
    Measure("no_show_rate", "Missed appointment rate by doctor",
            "appointments marked not attended", "appointments marked attended or not attended",
            "appointments with no attendance recorded", ("bookings.by_doctor.no_show_rate",),
            "scheduling.report v1"),
    Measure("hours_to_confirm", "Hours to confirm a booking request",
            "hours from a booking request to its confirmation (median and average)",
            "confirmed requests of the period", "requests never confirmed",
            ("bookings.hours_to_confirm",), "scheduling.report v1"),
    Measure("booking_forecast", "Appointments expected next week",
            "average appointments per weekday over the last eight weeks", "weeks with history",
            "fewer than two weeks or ten bookings of history (no forecast is made)",
            ("forecast",), "scheduling.forecast_week v1"),
    Measure("referrals_received", "Referrals received",
            "referrals sent to this organization in the period, by where they stand now",
            "none (a count)", "referrals this organization sent", ("referrals",),
            "referral.report v1"),
    Measure("waiting_list_flow", "Waiting list",
            "people who joined in the period, how many booked or left since, and how many wait "
            "now", "none (a count)", "other organizations' lists", ("waiting_list",),
            "waitlist.report v1"),
    Measure("message_flow", "Patient messages",
            "conversations started in the period, closed, and waiting for the clinic now",
            "none (a count)", "staff-only notes", ("messages.started", "messages.closed",
                                                   "messages.waiting_for_clinic"),
            "messaging.report v1"),
    Measure("hours_to_first_reply", "Hours to the clinic's first reply",
            "hours from a patient's first message to the clinic's first reply (median, average)",
            "conversations answered", "conversations not answered yet",
            ("messages.hours_to_first_reply",), "messaging.report v1"),
)}


def mode() -> str:
    value = str(getattr(settings, "ZOIKO_MEASURE_GATING", ENFORCE)).strip().lower()
    return value if value in (ENFORCE, REPORT, OFF) else ENFORCE


def catalogue_rows() -> list[dict[str, object]]:
    """The measures, in the shape of the template catalogue, so they are approved the same way."""
    return [{"key": m.key, "channel": "measure", "class": "measure", "audience": "staff",
             "subject": m.title, "body": m.definition, "variables": [], "exposure": "none",
             "version": m.version} for m in MEASURES.values()]


# ------------------------------------------------------------------ small numbers
def _small(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < small_cell_k()


class _Suppressor:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def count(self, value: Any, path: str) -> Any:
        if _small(value):
            self.paths.append(path)
            return None
        return value

    def group(self, values: dict[str, Any], path: str) -> dict[str, Any]:
        """Counts that add up to a shown total: never exactly one hidden (differencing)."""
        out = {k: self.count(v, f"{path}.{k}") for k, v in values.items()}
        hidden = [k for k, v in values.items() if _small(v)]
        if len(hidden) == 1:
            others = sorted((v, k) for k, v in values.items()
                            if k not in hidden and isinstance(v, int) and v > 0)
            if others:
                _, extra = others[0]
                out[extra] = None
                self.paths.append(f"{path}.{extra}")
        return out

    def summary(self, value: dict[str, Any], path: str) -> dict[str, Any]:
        """A median and average from fewer than K cases are withheld."""
        if isinstance(value, dict) and _small(value.get("count")):
            self.paths.append(path)
            return {"count": None, "median": None, "average": None}
        return value


def _protect_bookings(b: dict[str, Any], s: _Suppressor) -> dict[str, Any]:
    out = dict(b)
    out["total"] = s.count(b.get("total"), "bookings.total")
    out["status"] = s.group(b.get("status", {}), "bookings.status")
    out["attendance"] = s.group(b.get("attendance", {}), "bookings.attendance")
    out["cancel_reasons"] = s.group(b.get("cancel_reasons", {}), "bookings.cancel_reasons")
    out["per_day"] = [{**d, "count": s.count(d.get("count"), f"bookings.per_day.{d.get('date')}")}
                      for d in b.get("per_day", [])]
    out["by_weekday"] = [s.count(v, f"bookings.by_weekday.{i}")
                         for i, v in enumerate(b.get("by_weekday", []))]
    out["busiest_services"] = [{**r, "count": s.count(r.get("count"),
                                                       f"bookings.busiest_services.{i}")}
                               for i, r in enumerate(b.get("busiest_services", []))]
    doctors = []
    for i, d in enumerate(b.get("by_doctor", [])):
        row = dict(d)
        counted = {k: d.get(k, 0) for k in ("attended", "no_show", "not_recorded")}
        row.update(s.group(counted, f"bookings.by_doctor.{i}"))
        decided = (d.get("attended") or 0) + (d.get("no_show") or 0)
        if decided < small_cell_k() and d.get("no_show_rate") is not None:
            row["no_show_rate"] = None
            s.paths.append(f"bookings.by_doctor.{i}.no_show_rate")
        doctors.append(row)
    out["by_doctor"] = doctors
    out["hours_to_confirm"] = s.summary(b.get("hours_to_confirm", {}), "bookings.hours_to_confirm")
    return out


def protect(report: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The report with small numbers suppressed, and what was suppressed (paths only)."""
    s = _Suppressor()
    out = dict(report)
    if isinstance(report.get("bookings"), dict):
        out["bookings"] = _protect_bookings(report["bookings"], s)
    if isinstance(report.get("referrals"), dict):
        r = dict(report["referrals"])
        parts = {k: v for k, v in r.items() if k != "received"}
        r = {"received": s.count(r.get("received"), "referrals.received"),
             **s.group(parts, "referrals")}
        out["referrals"] = r
    if isinstance(report.get("waiting_list"), dict):
        w = report["waiting_list"]
        out["waiting_list"] = {k: s.count(v, f"waiting_list.{k}") for k, v in w.items()}
    if isinstance(report.get("messages"), dict):
        m = dict(report["messages"])
        for k in ("started", "closed", "waiting_for_clinic"):
            m[k] = s.count(m.get(k), f"messages.{k}")
        m["hours_to_first_reply"] = s.summary(m.get("hours_to_first_reply", {}),
                                              "messages.hours_to_first_reply")
        out["messages"] = m
    return out, s.paths


# ------------------------------------------------------------------ approval
def withhold_unapproved(report: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Parts of the report whose measure version is not approved: withheld (enforce), or shown and
    logged (report). Returns the report and the measure keys withheld."""
    from .template_policy import is_approved

    current = mode()
    if current == OFF:
        return report, []
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in report.items()}
    withheld = []
    for m in MEASURES.values():
        if is_approved("measure", m.key, m.version):
            continue
        if current == REPORT:
            log.warning("measure_gating would_withhold measure=%s version=%s", m.key, m.version)
            continue
        withheld.append(m.key)
        for part in m.covers:
            section, _, field = part.partition(".")
            if not field:
                out[section] = None
            elif isinstance(out.get(section), dict):
                if field.endswith(".no_show_rate"):
                    out[section]["by_doctor"] = [{**d, "no_show_rate": None}
                                                 for d in (out[section].get("by_doctor") or [])]
                else:
                    out[section][field] = None
    return out, withheld


def described() -> list[dict[str, Any]]:
    """What the report's numbers mean, for the screen."""
    from .template_policy import is_approved

    return [{"key": m.key, "title": m.title, "counts": m.numerator, "out_of": m.denominator,
             "leaves_out": m.exclusions, "covers": list(m.covers), "version": m.version,
             "approved": is_approved("measure", m.key, m.version)} for m in MEASURES.values()]
