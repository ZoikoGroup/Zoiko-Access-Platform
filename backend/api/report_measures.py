"""The last step of every clinic report (DEV-389): small numbers suppressed, unapproved measures
withheld, each measure's definition attached, and the run recorded so it can be reproduced.
Rules: foundation/measures.py.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from foundation import measures
from foundation.cell_context import tenant_transaction
from modules.evidence import api as evidence
from modules.trust import api as trust


def measured(auth: trust.Authenticated, org: str, report: dict[str, Any]) -> dict[str, Any]:
    protected, suppressed = measures.protect(report)
    shown, withheld = measures.withhold_unapproved(protected)
    described = measures.described()
    body = json.dumps(shown, sort_keys=True, default=str).encode()
    versions = "|".join(f"{m['key']}={m['version']}" for m in described)
    evidence_ref = hashlib.sha256(body).hexdigest()[:32]
    with tenant_transaction():
        # What makes the run reproducible (GHE-10 DAT-11): the period, the suppression rule, the
        # measure versions and a fingerprint of exactly what was shown. Never the numbers.
        evidence.record(
            actor_ref=auth.actor_public_id, action="report.run", object_type="organization",
            object_ref=org, outcome=evidence.AuditOutcome.ALLOWED,
            details={"days": report.get("days"), "from_day": report.get("from"),
                     "to_day": report.get("to"), "small_cell_k": measures.small_cell_k(),
                     "suppressed": len(suppressed), "withheld": len(withheld),
                     "measures": hashlib.sha256(versions.encode()).hexdigest()[:32],
                     "result": evidence_ref})
    return {**shown, "measures": described, "small_cell_k": measures.small_cell_k(),
            "suppressed": suppressed, "withheld": withheld, "evidence": evidence_ref}
