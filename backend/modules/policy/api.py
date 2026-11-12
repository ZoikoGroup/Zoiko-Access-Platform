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
    decisions_about,
    denial_summary,
    recent_decisions,
)
from .rules import POLICY_DIGEST

__all__ = [
    "POLICY_DIGEST", "Decision", "NoRelationships", "Reason", "RelationshipChecker",
    "TrustRelationships",
    "authorize", "check_receipt", "decide", "decision_counts", "decisions_about",
    "denial_summary", "recent_decisions",
]
