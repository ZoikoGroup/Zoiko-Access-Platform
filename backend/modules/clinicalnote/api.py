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
    guardian_may_read,
    list_for_person,
    page_for_person,
    patient_list,
    patient_read,
    read,
    set_guardian_hidden,
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
    "guardian_may_read",
    "list_for_person",
    "page_for_person",
    "patient_list",
    "patient_read",
    "read",
    "set_guardian_hidden",
    "sign",
]
