"""Public interface of the integration module (D15): outbound signed webhooks.

This is the ONLY module other domain modules may import from `modules.integration`.
"""
from .fhir import list_quarantined, quarantine_step, receive_fhir
from .inbound import (
    create_client,
    issue_client_secret,
    list_clients,
    list_events,
    pending_for_platform,
    receive,
    record_check,
    rotate_client_secret,
    set_client_state,
)
from .models import FhirQuarantineState
from .services import (
    create_endpoint,
    deliver_all_tenants,
    deliver_due,
    gave_up_count,
    list_deliveries,
    list_endpoints,
    redrive,
    rotate_secret,
    set_state,
    support_attempts,
    verify_endpoint,
)

__all__ = [
    "FhirQuarantineState",
    "create_client",
    "create_endpoint",
    "deliver_all_tenants",
    "deliver_due",
    "gave_up_count",
    "issue_client_secret",
    "list_clients",
    "list_deliveries",
    "support_attempts",
    "list_endpoints",
    "list_events",
    "list_quarantined",
    "pending_for_platform",
    "quarantine_step",
    "receive",
    "receive_fhir",
    "record_check",
    "redrive",
    "rotate_client_secret",
    "rotate_secret",
    "set_client_state",
    "set_state",
    "verify_endpoint",
]
