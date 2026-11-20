"""Public interface of the safety module (D08): the hazard register.

This is the ONLY module other domain modules may import from `modules.safety`.
"""

from .purposes import (
    approve as approve_purpose,
)
from .purposes import (
    list_purposes,
    missing_for,
    propose,
)
from .purposes import (
    retire as retire_purpose,
)
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
    "approve_purpose",
    "list_purposes",
    "missing_for",
    "propose",
    "retire_purpose",
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
