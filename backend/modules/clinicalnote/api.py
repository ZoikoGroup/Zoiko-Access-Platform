"""Public interface of the clinical notes module (DEV-304). Switched off unless
CLINICAL_NOTES_ENABLED is True."""

from .models import AmendReason
from .services import (
    ATTEST,
    READ,
    WRITE,
    add_addendum,
    amend,
    create_draft,
    edit_draft,
    enabled,
    get,
    list_for_person,
    patient_list,
    patient_read,
    read,
    sign,
)

__all__ = [
    "ATTEST",
    "READ",
    "WRITE",
    "AmendReason",
    "add_addendum",
    "amend",
    "create_draft",
    "edit_draft",
    "enabled",
    "get",
    "list_for_person",
    "patient_list",
    "patient_read",
    "read",
    "sign",
]
