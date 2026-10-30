"""Public interface of the policy module (D02).

This is the ONLY module other domain modules may import from `modules.policy`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .engine import (
    Decision,
    NoRelationships,
    Reason,
    RelationshipChecker,
    TrustRelationships,
    authorize,
    check_receipt,
    decide,
    decision_counts,
    recent_decisions,
)

__all__ = [
    "Decision", "NoRelationships", "Reason", "RelationshipChecker", "TrustRelationships",
    "authorize", "check_receipt", "decide", "decision_counts", "recent_decisions",
]
