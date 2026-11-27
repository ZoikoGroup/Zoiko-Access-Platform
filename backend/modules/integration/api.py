"""Public interface of the integration module (D15): outbound signed webhooks.

This is the ONLY module other domain modules may import from `modules.integration`.
"""
from .egress import ask as ask_egress
from .egress import decide as decide_egress
from .egress import listing as egress_permits
from .egress import mode as egress_mode
from .fhir import (
    list_quarantined,
    parse_payload,
    quarantine_step,
    receive_fhir,
    retry_code_held,
    retry_quarantined,
)
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
from .public_keys import ApiCaller
from .public_keys import authenticate as authenticate_api_key
from .public_keys import require_scope as require_api_scope
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
    "parse_payload",
    "ApiCaller", "authenticate_api_key", "require_api_scope",          # DEV-393
    "ask_egress", "decide_egress", "egress_mode", "egress_permits",     # DEV-384
    "retry_code_held",
    "retry_quarantined",
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
