"""Keeping and reading the requirements matrix (GHE-19 section on traceability).

Rules (proposed, DEV-138):
* A support officer (R11) may add or update a requirement; a safety officer (R12) may read.
* "Met" needs evidence that names an automated test file (`test_...py`), so a status cannot be
  claimed without pointing at something that can be run.
* Anything else may say "partly" or "not built" with a plain reason.
* Importing a batch updates what is already there (by source and id) and adds the rest; it never
  deletes a requirement.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import Requirement, Standing

WRITE = "write:platform"
MAX_BATCH = 500
SEED_DIR = Path(__file__).parent / "seed"
_SOURCE = re.compile(r"^GHE-\d{2}$")
_REQ = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$")
_TEST = re.compile(r"\btest_[a-z0-9_]+\.py\b")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def requirement_dict(r: Requirement) -> dict[str, Any]:
    return {"public_id": r.public_id, "source": r.source, "req_id": r.req_id, "group": r.group,
            "title": r.title, "status": r.status, "evidence": r.evidence,
            "updated_at": r.updated_at.isoformat()}


def _clean(item: Any) -> dict[str, str]:
    if not isinstance(item, dict):
        raise _bad("rows")
    source, req_id = str(item.get("source", "")), str(item.get("req_id", ""))
    status = str(item.get("status", ""))
    title = " ".join(str(item.get("title", "")).split())
    evidence = " ".join(str(item.get("evidence", "")).split())
    group = " ".join(str(item.get("group", "")).split())
    if not _SOURCE.match(source) or not _REQ.match(req_id):
        raise _bad("rows")
    if status not in Standing.values or not 3 <= len(title) <= 500 or len(group) > 80:
        raise _bad("rows")
    if len(evidence) > 1000 or any(ord(ch) < 32 for ch in title + evidence + group):
        raise _bad("rows")
    if status == Standing.MET and not _TEST.search(evidence):
        raise _bad("evidence")                    # "met" has to name a test that can be run
    return {"source": source, "req_id": req_id, "status": status, "title": title,
            "evidence": evidence, "group": group}


def import_rows(
    *, rows: Any, actor_public_id: str, decision: policy.Decision, organization_ref: str | None
) -> dict[str, int]:
    policy.check_receipt(decision, actor_public_id=actor_public_id, operation=WRITE,
                         organization_public_id=organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_BATCH:
        raise _bad("rows")
    clean = [_clean(r) for r in rows]
    if len({(c["source"], c["req_id"]) for c in clean}) != len(clean):
        raise _bad("rows")
    return _apply(clean, actor_public_id)


def _apply(clean: list[dict[str, str]], actor_public_id: str) -> dict[str, int]:
    added = changed = 0
    with transaction.atomic():
        for c in clean:
            existing = Requirement.objects.select_for_update().filter(
                source=c["source"], req_id=c["req_id"], **_scope()).first()
            if existing is None:
                Requirement.objects.create(updated_by_ref=actor_public_id, **c, **_scope())
                added += 1
            elif any(getattr(existing, k) != v for k, v in c.items()):
                for k, v in c.items():
                    setattr(existing, k, v)
                existing.updated_by_ref = actor_public_id
                existing.entity_version += 1
                existing.save()
                changed += 1
        record(actor_ref=actor_public_id, action="traceability.imported",
               object_type="requirements", object_ref=clean[0]["source"],
               outcome=AuditOutcome.ALLOWED, details={"added": added, "changed": changed})
    return {"added": added, "changed": changed, "unchanged": len(clean) - added - changed}


def load_seed_everywhere() -> dict[str, int]:
    """For the command: load the recorded readings into every active platform tenant."""
    from foundation.cell_context import tenant_transaction
    from modules.trust import api as trust

    clean = [_clean(r) for r in seed_rows()]
    total = {"added": 0, "changed": 0, "unchanged": 0}
    for tenant in trust.list_tenants():
        if tenant["type"] != "platform" or tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            got = _apply(clean, "system")
        for key, value in got.items():
            total[key] += value
    return total


def update_requirement(
    *, requirement_ref: str, status: str, evidence: str, actor_public_id: str,
    decision: policy.Decision, organization_ref: str | None
) -> dict[str, Any]:
    policy.check_receipt(decision, actor_public_id=actor_public_id, operation=WRITE,
                         organization_public_id=organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        row = Requirement.objects.select_for_update().filter(
            public_id=requirement_ref, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        c = _clean({"source": row.source, "req_id": row.req_id, "group": row.group,
                    "title": row.title, "status": status, "evidence": evidence})
        if (row.status, row.evidence) != (c["status"], c["evidence"]):
            row.status, row.evidence = c["status"], c["evidence"]
            row.updated_by_ref = actor_public_id
            row.entity_version += 1
            row.save(update_fields=["status", "evidence", "updated_by_ref", "entity_version",
                                    "updated_at"])
            record(actor_ref=actor_public_id, action="traceability.updated",
                   object_type="requirement", object_ref=row.public_id,
                   outcome=AuditOutcome.ALLOWED, reason_code=c["status"])
        return requirement_dict(row)


def list_requirements(source: str | None = None, status: str | None = None) -> dict[str, Any]:
    rows = Requirement.objects.filter(**_scope())
    everything = list(rows.values_list("source", "status"))
    if source:
        rows = rows.filter(source=source)
    if status:
        if status not in Standing.values:
            raise _bad("status")
        rows = rows.filter(status=status)
    summary: dict[str, dict[str, int]] = {}
    for src, st in everything:
        summary.setdefault(src, Counter())[st] += 1             
    return {
        "requirements": [requirement_dict(r) for r in rows.order_by("source", "req_id")],
        "summary": {src: {"total": sum(c.values()), "met": c.get("met", 0),
                          "partly": c.get("partly", 0), "not_built": c.get("not_built", 0)}
                    for src, c in sorted(summary.items())},
    }


def seed_rows() -> list[dict[str, str]]:
    """The requirements recorded from the developer's own readings (docs/*-TRACE.md)."""
    out: list[dict[str, str]] = []
    for path in sorted(SEED_DIR.glob("*.json")):
        out.extend(json.loads(path.read_text(encoding="utf-8")))
    return out
