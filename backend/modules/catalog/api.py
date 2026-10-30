"""Public interface of the catalog module (D03).

This is the ONLY module other domain modules may import from `modules.catalog`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    create_location,
    create_service,
    get_location,
    get_service,
    list_locations,
    list_services,
    service_counts,
    set_service_status,
)

__all__ = [
    "create_location", "create_service", "get_location", "get_service", "list_locations",
    "list_services", "service_counts", "set_service_status",
]
