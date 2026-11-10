"""Public interface of the connector module (D14, GHE-13/22).

This is the ONLY module other domain modules may import from `modules.connector`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .adapters import (
    Adapter,
    AdapterTimeout,
    AdapterUnavailable,
    OperationView,
    Outcome,
    PartnerResult,
    register,
    registered,
)
from .services import (
    get_operation,
    is_source_confirmed,
    list_operations,
    operation_history,
    reconcile_due,
    recover_stuck,
    redrive,
    request_operation,
    resolve_by_hand,
    send_due,
)

__all__ = [
    "Adapter", "AdapterTimeout", "AdapterUnavailable", "OperationView", "Outcome",
    "PartnerResult", "get_operation", "is_source_confirmed", "list_operations",
    "operation_history", "reconcile_due", "recover_stuck", "redrive", "register", "registered",
    "request_operation", "resolve_by_hand", "send_due",
]
