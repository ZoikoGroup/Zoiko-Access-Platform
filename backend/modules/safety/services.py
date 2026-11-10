"""Working the hazard register: seed it, assess, review independently, accept or retire a hazard.

Rules (the docs name the fields and the status words; the steps below are how we apply them):
- A hazard is ASSESSED by one person (severity, probability, how it was judged, the remaining
  risk) and REVIEWED by a DIFFERENT person. Nobody reviews their own assessment.
- Remaining risk can be ACCEPTED only after an independent review has verified it, by someone
  other than the assessor, and never when the remaining risk is HIGH (it must be reduced first).
- Assessing again after a review clears the review, so an old sign-off never covers a new judgment.
- Every step is written to an append-only history and to the audit log. Notes must never hold
  patient details.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record

from .models import Hazard, HazardEvent, HazardStatus, Library

# The 20 product hazards the docs seed (schema file 05, `safety.hazard`).
PRODUCT_SEED = {
    "H01": "Wrong-patient result",
    "H02": "Wrong order/result correlation",
    "H03": "Preliminary result seen as final",
    "H04": "Corrected report unnoticed",
    "H05": "Clinical acknowledgment forged by delivery",
    "H06": "Critical message not actioned",
    "H07": "Autonomous critical prioritization",
    "H08": "Referral lost or duplicated",
    "H09": "Stale professional credentials",
    "H10": "Unsafe patient notification",
    "H11": "Unit or terminology conversion",
    "H12": "Incomplete handover",
    "H13": "Emergency workflow misconstrued",
    "H14": "Partner outage or lag",
    "H15": "Wrong or expired market pack",
    "H16": "Consent withdrawn",
    "H17": "AI drives clinical action",
    "H18": "Unsafe configuration rollout",
    "H19": "Patient cannot understand instruction",
    "H20": "Poor post-incident traceability",
}
# The 16 AI hazards, kept as a separate library (schema file 05, section G).
AI_SEED = {
    "HAI-01": "Fabricated fact",
    "HAI-02": "Missed urgent sign",
    "HAI-03": "Unapproved medical purpose",
    "HAI-04": "Wrong patient",
    "HAI-05": "Unauthorized export",
    "HAI-06": "Prompt injection",
    "HAI-07": "Automation bias",
    "HAI-08": "Silent model update",
    "HAI-09": "Subgroup harm",
    "HAI-10": "Data leakage",
    "HAI-11": "Misleading confidence",
    "HAI-12": "Agent overreach",
    "HAI-13": "Lost emergency path",
    "HAI-14": "Version mismatch",
    "HAI-15": "Unreported device event",
    "HAI-16": "Misuse outside indication",
}
RISKS = ("low", "medium", "high")
MAX_NOTE = 300


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(*fields: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=list(fields))


def _text(value: object, field: str, low: int, high: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not low <= len(text) <= high or any(ord(ch) < 32 for ch in text):
        raise _bad(field)
    return text


def _score(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
        raise _bad(field)
    return value


def _optional(value: object, field: str, high: int) -> str:
    return _text(value, field, 0, high) if isinstance(value, str) and value.strip() else ""


def hazard_dict(h: Hazard) -> dict[str, Any]:
    return {
        "id": h.public_id,
        "ref_no": h.ref_no,
        "library": h.library,
        "title": h.title,
        "hazardous_situation": h.hazardous_situation,
        "exposure_path": h.exposure_path,
        "affected_groups": h.affected_groups,
        "severity": h.severity,
        "probability": h.probability,
        "basis_method": h.basis_method,
        "residual_risk": h.residual_risk,
        "owner": h.owner_ref,
        "linked_build": h.linked_build,
        "assessed_by": h.assessed_by_ref,
        "reviewer": h.reviewer_ref,
        "status": h.status,
        "assessed": bool(h.severity and h.probability and h.residual_risk),
    }


def ensure_seed(actor_ref: str) -> int:
    """Make sure every seeded hazard exists for this customer. Safe to repeat; returns how many."""
    have = set(Hazard.objects.filter(**_scope()).values_list("ref_no", flat=True))
    wanted = [(r, t, Library.PRODUCT) for r, t in PRODUCT_SEED.items()]
    wanted += [(r, t, Library.AI) for r, t in AI_SEED.items()]
    missing = [(r, t, lib) for r, t, lib in wanted if r not in have]
    if not missing:
        return 0
    with transaction.atomic():
        for ref, title, lib in missing:
            Hazard.objects.get_or_create(
                ref_no=ref, defaults={"title": title, "library": lib}, **_scope()
            )
        record(
            actor_ref=actor_ref,
            action="safety.hazard_seed",
            object_type="hazard_register",
            object_ref="seed",
            outcome=AuditOutcome.ALLOWED,
            details={"added": len(missing)},
        )
    return len(missing)


def list_hazards() -> list[dict[str, Any]]:
    rows = Hazard.objects.filter(**_scope()).order_by("library", "ref_no")
    return [hazard_dict(h) for h in rows]


def history(hazard_ref: str) -> list[dict[str, Any]]:
    events = HazardEvent.objects.filter(hazard_ref=hazard_ref, **_scope()).order_by("created_at")
    return [
        {
            "action": e.action,
            "from": e.from_status,
            "to": e.to_status,
            "by": e.actor_ref,
            "note": e.note,
            "at": e.created_at.isoformat(),
        }
        for e in events
    ]


def _own(hazard_ref: str) -> Hazard:
    found = Hazard.objects.select_for_update().filter(public_id=hazard_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _step(h: Hazard, action: str, to: str, actor_ref: str, note: str) -> None:
    before = h.status
    h.status = to
    h.save()
    HazardEvent.objects.create(
        hazard_ref=h.public_id,
        action=action,
        from_status=before,
        to_status=to,
        actor_ref=actor_ref,
        note=note,
        **_scope(),
    )
    record(
        actor_ref=actor_ref,
        action=f"safety.hazard.{action}",
        object_type="hazard",
        object_ref=h.public_id,
        outcome=AuditOutcome.ALLOWED,
    )


def assess(
    hazard_ref: str,
    *,
    actor_ref: str,
    severity: object,
    probability: object,
    basis_method: object,
    residual_risk: object,
    hazardous_situation: object = "",
    exposure_path: object = "",
    affected_groups: object = "",
    owner_ref: str = "",
    linked_build: object = "",
) -> dict[str, Any]:
    sev, prob = _score(severity, "severity"), _score(probability, "probability")
    if residual_risk not in RISKS:
        raise _bad("residual_risk")
    risk = str(residual_risk)
    basis = _text(basis_method, "basis_method", 5, 300)
    fields = {
        "hazardous_situation": _optional(hazardous_situation, "hazardous_situation", 500),
        "exposure_path": _optional(exposure_path, "exposure_path", 300),
        "affected_groups": _optional(affected_groups, "affected_groups", 200),
        "linked_build": _optional(linked_build, "linked_build", 100),
    }
    with transaction.atomic():
        h = _own(hazard_ref)
        if h.status == HazardStatus.RETIRED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        h.severity, h.probability, h.basis_method = sev, prob, basis
        h.residual_risk, h.owner_ref, h.assessed_by_ref = risk, owner_ref, actor_ref
        h.reviewer_ref = ""  # a new judgment needs a new independent review
        h.hazardous_situation = fields["hazardous_situation"]
        h.exposure_path = fields["exposure_path"]
        h.affected_groups = fields["affected_groups"]
        h.linked_build = fields["linked_build"]
        _step(h, "assess", HazardStatus.OPEN, actor_ref, "")
    return hazard_dict(h)


def review(
    hazard_ref: str, *, actor_ref: str, verdict: object, note: object = ""
) -> dict[str, Any]:
    """An independent person verifies or rejects the assessment of the person who made it."""
    if verdict not in ("verified", "rejected"):
        raise _bad("verdict")
    text = _optional(note, "note", MAX_NOTE)
    if verdict == "rejected" and not text:
        raise _bad("note")  # say why, so the assessor can fix it
    with transaction.atomic():
        h = _own(hazard_ref)
        if h.status != HazardStatus.OPEN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if not (h.severity and h.probability and h.residual_risk):
            raise _bad("assessment")  # nothing to review yet
        if not actor_ref or actor_ref == h.assessed_by_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        h.reviewer_ref = actor_ref
        _step(
            h,
            "review_" + verdict,
            HazardStatus.VERIFIED if verdict == "verified" else HazardStatus.REJECTED,
            actor_ref,
            text,
        )
    return hazard_dict(h)


def accept(hazard_ref: str, *, actor_ref: str, note: object = "") -> dict[str, Any]:
    """Accept what risk remains, after a verified review. Never for a HIGH remaining risk."""
    text = _optional(note, "note", MAX_NOTE)
    with transaction.atomic():
        h = _own(hazard_ref)
        if h.status != HazardStatus.VERIFIED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if not actor_ref or actor_ref == h.assessed_by_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if h.residual_risk == "high":
            raise _bad("residual_risk")  # reduce it first, then assess again
        _step(h, "accept", HazardStatus.ACCEPTED, actor_ref, text)
    return hazard_dict(h)


def retire(hazard_ref: str, *, actor_ref: str, reason: object) -> dict[str, Any]:
    text = _text(reason, "reason", 3, MAX_NOTE)
    with transaction.atomic():
        h = _own(hazard_ref)
        if h.status == HazardStatus.RETIRED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        _step(h, "retire", HazardStatus.RETIRED, actor_ref, text)
    return hazard_dict(h)
