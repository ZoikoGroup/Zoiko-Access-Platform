"""Public interface of the waitlist module (D04).

This is the ONLY module other domain modules may import from `modules.waitlist`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .models import WaitPriority
from .services import (
    get_entry,
    join,
    leave,
    list_entries,
    offer_hours_for,
    report,
    set_offer_hours,
    set_priority,
    waiting_counts,
)

__all__ = [
    "WaitPriority", "get_entry", "join", "leave", "list_entries", "offer_hours_for", "report",
    "set_offer_hours", "set_priority", "waiting_counts",
]
