"""Public interface of the referral module (D07).

This is the ONLY module other domain modules may import from `modules.referral`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    acknowledge,
    decide,
    get_referral,
    inbox_dict,
    issue,
    list_for_answerer,
    list_referrals,
    referral_counts,
    report,
    set_inbox,
)

__all__ = [
    "acknowledge", "decide", "get_referral", "inbox_dict", "issue", "list_for_answerer",
    "list_referrals", "referral_counts", "report", "set_inbox",
]
