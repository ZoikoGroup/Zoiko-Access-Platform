"""Public interface of the codemap module (DEV-322): local codes mapped to SNOMED CT by two people.
Every function runs with the platform tenant bound.

This is the ONLY module other domain modules may import from `modules.codemap`.
"""

from .models import RejectReason
from .services import approve, list_mappings, lookup, note_seen, propose, reject, waiting_count

__all__ = [
    "RejectReason",
    "approve",
    "list_mappings",
    "lookup",
    "note_seen",
    "propose",
    "reject",
    "waiting_count",
]
