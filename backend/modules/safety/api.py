"""Public interface of the safety module (D08): the hazard register.

This is the ONLY module other domain modules may import from `modules.safety`.
"""

from .services import (
    accept,
    assess,
    ensure_seed,
    hazard_dict,
    history,
    list_hazards,
    resolution_summary,
    retire,
    review,
)

__all__ = [
    "accept",
    "assess",
    "ensure_seed",
    "hazard_dict",
    "history",
    "list_hazards",
    "resolution_summary",
    "retire",
    "review",
]
