"""Public interface of the evidence module (D17).

This is the ONLY module other domain modules may import from `modules.evidence`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .audit import ChainReport, activity_summary, recent_events, record, verify_chain
from .disclosures import list_disclosures, record_disclosure
from .models import AuditOutcome

__all__ = [
    "AuditOutcome", "ChainReport", "activity_summary", "list_disclosures", "recent_events",
    "record", "record_disclosure", "verify_chain",
]
