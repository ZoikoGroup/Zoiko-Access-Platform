"""Public interface of the coverage module (D12).

This is the ONLY module other domain modules may import from `modules.coverage`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""

from .insurers import register_adapter, reset_adapters
from .services import (
    add_coverage,
    check_cover,
    end_coverage,
    get_coverage,
    list_checks,
    list_coverage,
    set_sharing,
    shared_with_staff,
)

__all__ = [
    "check_cover",
    "list_checks",
    "register_adapter",
    "reset_adapters",
    "add_coverage",
    "end_coverage",
    "get_coverage",
    "list_coverage",
    "set_sharing",
    "shared_with_staff",
]
