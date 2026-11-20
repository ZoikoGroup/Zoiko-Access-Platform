"""Public interface of the coverage module (D12).

This is the ONLY module other domain modules may import from `modules.coverage`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""

from .services import (
    add_coverage,
    end_coverage,
    get_coverage,
    list_coverage,
    set_sharing,
    shared_with_staff,
)

__all__ = [
    "add_coverage",
    "end_coverage",
    "get_coverage",
    "list_coverage",
    "set_sharing",
    "shared_with_staff",
]
