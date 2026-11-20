"""Public interface of the waitlist module (D04).

This is the ONLY module other domain modules may import from `modules.waitlist`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    close_for_death,
    get_entry,
    join,
    leave,
    list_entries,
    report,
    waiting_counts,
)

__all__ = [
    "close_for_death",
    "get_entry",
    "join",
    "leave",
    "list_entries",
    "report",
    "waiting_counts",
]
