"""Go-live readiness for a release in a country (GHE-20 sections 03 to 06, WF-05 to WF-25).

A release is a tuple (build, cell, country, capabilities) with a fingerprint. Each requirement of
gate G3 (production readiness) and G4 (country go-live) needs one signed attestation FOR THAT
FINGERPRINT, so an approval can never be carried to another version or market. The rules:

* One person signs at most one requirement of a release, and never the person who created it:
  the specification asks for independent attestations, not one person ticking every box.
* Clinical requirements (the clinical partner and the clinical safety case) are signed by a safety
  officer (R12); the others by a support officer (R11), who stand in for the roles the specification
  names (security lead, privacy lead, SRE, legal, operating entity) until those roles exist.
* Any support or safety officer may VETO a requirement. A veto cannot be undone; the answer is a
  new release. There are no waivers (the specification allows only narrow ones, with compensating
  controls, which are not modelled).
* An attestation has an expiry date and stops counting after it.
* The country-pack requirement is not signed by anyone: it is checked against the signed country
  packs (a signed, unexpired pack for the country that switches on every capability of the release).
* The decision is only ever NOT READY, READY (G3) or AUTHORISED (G4). Nothing here deploys anything
  or goes live, and there is no pilot or live state (DEV-137).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.jurisdiction import api as jurisdiction
from modules.policy import api as policy
from modules.safety import api as safety

from .models import Decision, Evidence, GateDecision, Release

WRITE = "write:platform"
ATTEST = "attest:platform"
G3 = ("signed_build", "security_signoff", "privacy_signoff", "requirements_coverage",
      "recovery_exercised", "operations_staffed")
G4 = ("legal_authority", "clinical_partner", "clinical_safety_signoff", "operating_entity")
COUNTRY_PACK = "country_pack"            # checked by the system, signed by nobody
HAZARD_REGISTER = "hazard_register"      # likewise: every hazard accepted or retired (GHE-08 AT42)
CLINICAL = frozenset({"clinical_partner", "clinical_safety_signoff"})
ALL_REQUIREMENTS = (*G3, *G4)
MAX_VALID_DAYS = 366
_REF = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
_BUILD = re.compile(r"^[A-Za-z0-9._:-]{3,80}$")
_CELL = re.compile(r"^[A-Z0-9-]{2,32}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_ARTEFACT = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _digest(ref: str, build: str, cell: str, country: str, caps: list[str]) -> str:
    body = {"release": ref, "build": build, "cell": cell, "country": country,
            "capabilities": sorted(caps)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                          ).hexdigest()


def _own(ref: str) -> Release:
    found = Release.objects.select_for_update().filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


# ------------------------------------------------------------------ what each requirement stands at
def requirement_states(rel: Release, clock: Clock | None = None) -> list[dict[str, Any]]:
    """Each requirement's standing, worked out now (an expiry needs nobody to act)."""
    today = utc_now(clock).date()
    rows = list(Evidence.objects.filter(release=rel, **_scope()).order_by("created_at"))
    out: list[dict[str, Any]] = []
    for gate, names in (("G3", G3), ("G4", G4)):
        for name in names:
            mine = [e for e in rows if e.requirement == name and e.tuple_digest == rel.tuple_digest]
            vetoes = [e for e in mine if e.outcome == "vetoed"]
            signs = [e for e in mine if e.outcome == "signed"]
            if vetoes:
                status, who = "vetoed", vetoes[-1]
            elif signs and (signs[-1].expires_on is None or signs[-1].expires_on >= today):
                status, who = "signed", signs[-1]
            elif signs:
                status, who = "expired", signs[-1]
            else:
                status, who = "missing", None
            out.append({
                "gate": gate, "requirement": name, "status": status,
                "signer": who.signer_ref if who else "", "role": who.signer_role if who else "",
                "reference": who.reference if who else "",
                "expires_on": who.expires_on.isoformat() if who and who.expires_on else None,
                "note": who.note if who and status == "vetoed" else ""})
    return out


def _pack_state(rel: Release, clock: Clock | None) -> dict[str, Any]:
    found = jurisdiction.pack_covers(rel.country, list(rel.capabilities), clock)
    return {"gate": "G4", "requirement": COUNTRY_PACK, "status": "signed" if found else "missing",
            "signer": "", "role": "", "reference": found or "", "expires_on": None, "note": ""}


def _hazard_state() -> dict[str, Any]:
    """The hazard register must exist and hold no unresolved hazard: a hazard that is open, verified
    but not accepted, or rejected holds the release back. An empty register is not a clean one."""
    sums = safety.resolution_summary()
    ok = sums["total"] > 0 and sums["unresolved"] == 0
    if sums["total"] == 0:
        why = "no hazards recorded"
    else:
        why = f"{sums['total'] - sums['unresolved']} of {sums['total']} resolved"
    return {"gate": "G3", "requirement": HAZARD_REGISTER, "status": "signed" if ok else "missing",
            "signer": "", "role": "", "reference": why, "expires_on": None, "note": ""}


def evaluate_now(rel: Release, clock: Clock | None = None) -> dict[str, Any]:
    """The decision and what blocks it, worked out now without recording anything."""
    states = requirement_states(rel, clock)
    states.append(_hazard_state())
    states.append(_pack_state(rel, clock))
    blockers = [f"{s['gate'].lower()}.{s['requirement']}:{s['status']}"
                for s in states if s["status"] != "signed"]
    vetoed = any(s["status"] == "vetoed" for s in states)
    g3_ok = all(s["status"] == "signed" for s in states if s["gate"] == "G3")
    g4_ok = all(s["status"] == "signed" for s in states if s["gate"] == "G4")
    if vetoed or not g3_ok:
        decision = Decision.NO_GO
    elif g4_ok:
        decision = Decision.G4_AUTHORIZED
    else:
        decision = Decision.G3_READY
    return {"decision": decision, "blockers": blockers, "requirements": states}


def release_dict(rel: Release, clock: Clock | None = None) -> dict[str, Any]:
    now = evaluate_now(rel, clock)
    return {
        "public_id": rel.public_id, "release_ref": rel.release_ref, "build": rel.build,
        "cell": rel.cell, "country": rel.country, "capabilities": list(rel.capabilities),
        "tuple_digest": rel.tuple_digest, "created_at": rel.created_at.isoformat(),
        "author": rel.created_by_ref, "decision": now["decision"], "blockers": now["blockers"],
        "recorded_decision": rel.decision, "requirements": now["requirements"],
    }


def _record_if_changed(rel: Release, trigger: str, actor: str, clock: Clock | None) -> None:
    now = evaluate_now(rel, clock)
    if now["decision"] == rel.decision and now["blockers"] == rel.blockers:
        return
    rel.decision, rel.blockers = now["decision"], now["blockers"]
    rel.entity_version += 1
    rel.save(update_fields=["decision", "blockers", "entity_version", "updated_at"])
    GateDecision.objects.create(release=rel, decision=now["decision"], blockers=now["blockers"],
                                trigger=trigger, actor_ref=actor, **_scope())
    record(actor_ref=actor, action=f"readiness.decision_{now['decision']}",
           object_type="release", object_ref=rel.public_id, outcome=AuditOutcome.ALLOWED)


# ------------------------------------------------------------------ writing
def _check(decision: policy.Decision, actor: str, operation: str, org: str | None) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=operation,
                         organization_public_id=org)


def create_release(
    *, release_ref: str, build: str, cell: str, country: str, capabilities: Any,
    actor_public_id: str, decision: policy.Decision, organization_ref: str | None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if not _REF.match(release_ref or ""):
        raise _bad("release_ref")
    if not _BUILD.match(build or ""):
        raise _bad("build")
    if not _CELL.match(cell or ""):
        raise _bad("cell")
    if not _COUNTRY.match(country or ""):
        raise _bad("country")
    if (not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 8
            or any(c not in jurisdiction.CAPABILITIES for c in capabilities)
            or len(set(capabilities)) != len(capabilities)):
        raise _bad("capabilities")
    caps = sorted(capabilities)
    with transaction.atomic():
        if Release.objects.filter(release_ref=release_ref, **_scope()).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        rel = Release(release_ref=release_ref, build=build, cell=cell, country=country,
                      capabilities=caps, created_by_ref=actor_public_id,
                      tuple_digest=_digest(release_ref, build, cell, country, caps), **_scope())
        rel.save()
        record(actor_ref=actor_public_id, action="readiness.release_created",
               object_type="release", object_ref=rel.public_id, outcome=AuditOutcome.ALLOWED)
        _record_if_changed(rel, "created", actor_public_id, clock)
        return release_dict(rel, clock)


def sign(
    *, release_ref: str, requirement: str, reference: str, expires_on: Any, note: str,
    actor_public_id: str, decision: policy.Decision, organization_ref: str | None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Attest one requirement for this exact release. The right kind of officer, not the author,
    not someone who already signed another requirement, with an expiry date."""
    if requirement not in ALL_REQUIREMENTS:
        raise _bad("requirement")
    needed = "R12" if requirement in CLINICAL else "R11"
    _check(decision, actor_public_id, ATTEST, organization_ref)
    if decision.role_code != needed:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if not _ARTEFACT.match(reference or ""):
        raise _bad("reference")
    today = utc_now(clock).date()
    try:
        expiry = dt.date.fromisoformat(str(expires_on))
    except ValueError:
        raise _bad("expires_on") from None
    if not today < expiry <= today + dt.timedelta(days=MAX_VALID_DAYS):
        raise _bad("expires_on")
    text = " ".join((note or "").split())
    if len(text) > 300:
        raise _bad("note")
    with transaction.atomic():
        rel = _own(release_ref)
        if actor_public_id == rel.created_by_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)             # not your own release
        mine = Evidence.objects.filter(release=rel, signer_ref=actor_public_id, **_scope())
        if mine.exclude(requirement=requirement).exists():
            raise ZoikoError(ErrorCode.POLICY_DENIED)             # one person, one requirement
        states = {s["requirement"]: s["status"] for s in requirement_states(rel, clock)}
        if states[requirement] == "vetoed":
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)          # a veto stands: new release
        if states[requirement] == "signed":
            return release_dict(rel, clock)                       # already attested
        Evidence.objects.create(
            release=rel, requirement=requirement, outcome="signed", signer_ref=actor_public_id,
            signer_role=decision.role_code, reference=reference, tuple_digest=rel.tuple_digest,
            expires_on=expiry, note=text, **_scope())
        record(actor_ref=actor_public_id, action="readiness.signed", object_type="release",
               object_ref=rel.public_id, outcome=AuditOutcome.ALLOWED, reason_code=requirement)
        _record_if_changed(rel, f"signed:{requirement}", actor_public_id, clock)
        return release_dict(rel, clock)


def veto(
    *, release_ref: str, requirement: str, note: str, actor_public_id: str,
    decision: policy.Decision, organization_ref: str | None, clock: Clock | None = None,
) -> dict[str, Any]:
    """Stopping is easier than starting: any support or safety officer may veto a requirement. It
    cannot be undone for this release."""
    if requirement not in ALL_REQUIREMENTS:
        raise _bad("requirement")
    _check(decision, actor_public_id, ATTEST if decision.role_code == "R12" else WRITE,
           organization_ref)
    if decision.role_code not in ("R11", "R12"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    text = " ".join((note or "").split())
    if not 3 <= len(text) <= 300:
        raise _bad("note")
    with transaction.atomic():
        rel = _own(release_ref)
        states = {s["requirement"]: s["status"] for s in requirement_states(rel, clock)}
        if states[requirement] == "vetoed":
            return release_dict(rel, clock)
        Evidence.objects.create(
            release=rel, requirement=requirement, outcome="vetoed", signer_ref=actor_public_id,
            signer_role=decision.role_code, reference="veto", tuple_digest=rel.tuple_digest,
            note=text, **_scope())
        record(actor_ref=actor_public_id, action="readiness.vetoed", object_type="release",
               object_ref=rel.public_id, outcome=AuditOutcome.ALLOWED, reason_code=requirement)
        _record_if_changed(rel, f"veto:{requirement}", actor_public_id, clock)
        return release_dict(rel, clock)


def re_evaluate(
    *, release_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None, clock: Clock | None = None,
) -> dict[str, Any]:
    """Record the decision as it stands NOW (an expiry or a pack change can move it without anyone
    signing anything)."""
    _check(decision, actor_public_id, WRITE if decision.role_code == "R11" else ATTEST,
           organization_ref)
    if decision.role_code not in ("R11", "R12"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        rel = _own(release_ref)
        _record_if_changed(rel, "re-evaluated", actor_public_id, clock)
        return release_dict(rel, clock)


def list_releases(clock: Clock | None = None) -> list[dict[str, Any]]:
    return [release_dict(r, clock)
            for r in Release.objects.filter(**_scope()).order_by("-created_at")]


def get_release(ref: str, clock: Clock | None = None) -> dict[str, Any]:
    found = Release.objects.filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    out = release_dict(found, clock)
    out["history"] = [
        {"decision": g.decision, "blockers": g.blockers, "trigger": g.trigger,
         "actor": g.actor_ref, "at": g.created_at.isoformat()}
        for g in GateDecision.objects.filter(release=found, **_scope()).order_by("created_at")]
    return out
