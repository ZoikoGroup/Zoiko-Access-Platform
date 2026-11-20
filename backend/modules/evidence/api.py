"""Public interface of the evidence module (D17).

This is the ONLY module other domain modules may import from `modules.evidence`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .audit import ChainReport, activity_summary, recent_events, record, verify_chain
from .models import AuditOutcome

__all__ = [
    "AuditOutcome", "ChainReport", "activity_summary", "recent_events", "record", "verify_chain",
]
