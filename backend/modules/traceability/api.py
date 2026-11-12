"""Public interface of the traceability module (D18): the requirements matrix.

This is the ONLY module other domain modules may import from `modules.traceability`.
"""
from .services import (
    import_rows,
    list_requirements,
    load_seed_everywhere,
    seed_rows,
    update_requirement,
)

__all__ = ["import_rows", "list_requirements", "load_seed_everywhere", "seed_rows",
           "update_requirement"]
