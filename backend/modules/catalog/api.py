"""Public interface of the catalog module (D03).

This is the ONLY module other domain modules may import from `modules.catalog`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    change_listing,
    create_location,
    create_service,
    get_location,
    get_service,
    is_offered,
    list_categories,
    list_locations,
    list_services,
    organization_zone,
    service_counts,
    set_service_status,
    update_location_contact,
)

__all__ = [
    "change_listing", "create_location", "create_service", "get_location", "get_service",
    "is_offered", "list_categories", "list_locations", "list_services", "organization_zone",
    "service_counts", "set_service_status", "update_location_contact",
]
