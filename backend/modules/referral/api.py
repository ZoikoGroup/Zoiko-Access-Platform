"""Public interface of the referral module (D07).

This is the ONLY module other domain modules may import from `modules.referral`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import decide, get_referral, issue, list_referrals, referral_counts

__all__ = ["decide", "get_referral", "issue", "list_referrals", "referral_counts"]
