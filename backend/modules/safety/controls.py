"""Risk controls of the hazard register, each tied to the test that proves it (DEV-382).

GHE-08 SAF-12: "All clinical-risk controls map to one hazard, measurable acceptance tests, a
versioned release build and a named signatory." GHE-08 W05 and GHE-19 draw the chain
hazard -> control -> config/code -> test -> pass evidence -> residual-risk sign-off.

Rules
- A safety officer PROPOSES a control: what it does (preventive, detection, fallback or
  monitoring), where it lives (code, configuration or procedure) and the automated test that proves
  it, named as `tests/test_x.py::test_y`. The test must exist in this build.
- A DIFFERENT safety officer VERIFIES it: the test still exists, and their note says what they
  checked. The build they checked is recorded.
- A control can be WITHDRAWN with a reason. Withdrawn controls stay on the record.
- Proposing or withdrawing a control on a verified or accepted hazard sends the hazard back to
  open (its review is cleared): a changed set of controls needs a new sign-off (GHE-19: a change
  "must reopen the affected links and prior signoffs").
- The remaining risk of a hazard is accepted only when it has at least one verified control and
  none still waiting (services.accept).
- The trace (`trace`) shows, for every hazard, its controls and whether each named test is still
  there, so a deleted or renamed test shows up at once.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import utc_now
from modules.evidence.api import AuditOutcome, record

from .models import ControlKind, ControlState, Hazard, HazardControl, HazardStatus
from .services import MAX_NOTE, _own, _scope, _step, _text

_TEST_REF = re.compile(r"^tests/(test_[a-z0-9_]+)\.py::(test_[A-Za-z0-9_]+)$")

FOUND, MISSING, NOT_SHIPPED = "found", "missing", "not_shipped"


def test_status(test_ref: str) -> str:
    """Does the named test exist in this build? NOT_SHIPPED when the tests are not part of this
    install at all (a production image leaves them out), so nothing can be said."""
    match = _TEST_REF.match(test_ref)
    if match is None:
        return MISSING
    base = Path(settings.BASE_DIR) / "tests"
    if not base.is_dir():
        return NOT_SHIPPED
    path = base / f"{match.group(1)}.py"
    if not path.is_file():
        return MISSING
    defined = re.search(rf"^def {re.escape(match.group(2))}\(", path.read_text(encoding="utf-8"),
                        re.MULTILINE)
    return FOUND if defined else MISSING


def control_dict(c: HazardControl, labels: dict[str, str] | None = None) -> dict[str, Any]:
    names = labels or {}

    def who(ref: str) -> str:
        return names.get(ref, ref) if ref else ""

    return {
        "id": c.public_id, "hazard": c.hazard_ref, "control_no": c.control_no, "kind": c.kind,
        "description": c.description, "implementation": c.implementation,
        "test_ref": c.test_ref, "test": test_status(c.test_ref), "state": c.state,
        "proposed_by": who(c.proposed_by_ref), "verified_by": who(c.verified_by_ref),
        "verified_at": c.verified_at.isoformat() if c.verified_at else None,
        "verified_build": c.verified_build, "evidence_note": c.evidence_note,
        "withdrawn_by": who(c.withdrawn_by_ref), "withdrawn_reason": c.withdrawn_reason,
        "created_at": c.created_at.isoformat(),
    }


def _reopen(h: Hazard, actor_ref: str, why: str) -> None:
    """A verified or accepted hazard goes back to open: its old sign-off no longer covers it."""
    if h.status in (HazardStatus.VERIFIED, HazardStatus.ACCEPTED):
        h.reviewer_ref = ""
        _step(h, "controls_changed", HazardStatus.OPEN, actor_ref, why)


def _audit(actor_ref: str, action: str, c: HazardControl) -> None:
    record(actor_ref=actor_ref, action=f"safety.control.{action}", object_type="hazard_control",
           object_ref=c.public_id, outcome=AuditOutcome.ALLOWED,
           details={"hazard_ref": c.hazard_ref, "control_no": c.control_no})


def _next_number() -> str:
    numbers = [int(n[2:]) for n in HazardControl.objects.filter(**_scope())
               .values_list("control_no", flat=True) if n[2:].isdigit()]
    return f"C-{max(numbers, default=0) + 1:03d}"


def propose(
    hazard_ref: str, *, actor_ref: str, kind: object, description: object,
    implementation: object, test_ref: object,
) -> dict[str, Any]:
    bad = []
    if kind not in ControlKind.values:
        bad.append("kind")
    try:
        text = _text(description, "description", 10, 500)
    except ZoikoError:
        bad.append("description")
    try:
        where = _text(implementation, "implementation", 3, 200)
    except ZoikoError:
        bad.append("implementation")
    test = test_ref.strip() if isinstance(test_ref, str) else ""
    if not _TEST_REF.match(test) or test_status(test) == MISSING:
        bad.append("test_ref")              # a control must name a test that really exists
    if bad:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=bad)
    with transaction.atomic():
        h = _own(hazard_ref)
        if h.status == HazardStatus.RETIRED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        c = HazardControl.objects.create(
            hazard_ref=h.public_id, control_no=_next_number(), kind=str(kind),
            description=text, implementation=where, test_ref=test, proposed_by_ref=actor_ref,
            **_scope())
        _audit(actor_ref, "propose", c)
        _reopen(h, actor_ref, f"{c.control_no} proposed")
    return control_dict(c)


def _own_control(control_ref: str) -> HazardControl:
    found = HazardControl.objects.select_for_update().filter(
        public_id=control_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def verify(control_ref: str, *, actor_ref: str, note: object) -> dict[str, Any]:
    """A second safety officer checks the control and its test, and signs it with the build."""
    evidence = _text(note, "note", 5, MAX_NOTE)       # what they checked
    with transaction.atomic():
        c = _own_control(control_ref)
        if c.state != ControlState.PROPOSED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if not actor_ref or actor_ref == c.proposed_by_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)    # never your own control
        if test_status(c.test_ref) == MISSING:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["test_ref"])
        c.state, c.verified_by_ref, c.verified_at = ControlState.VERIFIED, actor_ref, utc_now()
        c.verified_build = str(settings.ZOIKO_BUILD)[:64]
        c.evidence_note = evidence
        c.save()
        _audit(actor_ref, "verify", c)
    return control_dict(c)


def withdraw(control_ref: str, *, actor_ref: str, reason: object) -> dict[str, Any]:
    why = _text(reason, "reason", 3, MAX_NOTE)
    with transaction.atomic():
        c = _own_control(control_ref)
        if c.state == ControlState.WITHDRAWN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        c.state, c.withdrawn_by_ref, c.withdrawn_reason = ControlState.WITHDRAWN, actor_ref, why
        c.save()
        _audit(actor_ref, "withdraw", c)
        _reopen(_own(c.hazard_ref), actor_ref, f"{c.control_no} withdrawn")
    return control_dict(c)


def controls_of(hazard_ref: str, labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows = HazardControl.objects.filter(hazard_ref=hazard_ref, **_scope()).order_by("control_no")
    return [control_dict(c, labels) for c in rows]


def trace(labels: dict[str, str] | None = None) -> dict[str, Any]:
    """Every hazard with its controls and the state of each named test, and the gaps: hazards with
    no verified control, accepted hazards without one (accepted before this rule), and controls
    whose test has gone."""
    hazards = list(Hazard.objects.filter(**_scope()).order_by("library", "ref_no"))
    by_hazard: dict[str, list[dict[str, Any]]] = {}
    for c in HazardControl.objects.filter(**_scope()).order_by("control_no"):
        by_hazard.setdefault(c.hazard_ref, []).append(control_dict(c, labels))
    rows: list[dict[str, Any]] = []
    for h in hazards:
        controls = by_hazard.get(h.public_id, [])
        verified = [c for c in controls if c["state"] == ControlState.VERIFIED]
        rows.append({
            "id": h.public_id, "ref_no": h.ref_no, "title": h.title, "status": h.status,
            "residual_risk": h.residual_risk, "controls": controls,
            "verified_controls": len(verified),
        })
    live = [c for r in rows for c in r["controls"] if c["state"] != ControlState.WITHDRAWN]
    bare = [r for r in rows if not r["verified_controls"]]
    return {
        "hazards": rows,
        "gaps": {
            "without_verified_control": sum(1 for r in bare if r["status"] != HazardStatus.RETIRED),
            "accepted_without_verified_control": sum(
                1 for r in bare if r["status"] == HazardStatus.ACCEPTED),
            "controls_waiting": sum(1 for c in live if c["state"] == ControlState.PROPOSED),
            "tests_missing": sum(1 for c in live if c["test"] == MISSING),
        },
    }


def ready_to_accept(hazard_ref: str) -> bool:
    """At least one verified control, and none still waiting for its check."""
    states = list(HazardControl.objects.filter(hazard_ref=hazard_ref, **_scope())
                  .values_list("state", flat=True))
    return ControlState.VERIFIED in states and ControlState.PROPOSED not in states
