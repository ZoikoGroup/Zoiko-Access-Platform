"""Public interface of the claim module (DEV-308): claims between a clinic and a payer. Every
function runs with the platform tenant bound.

This is the ONLY module other domain modules may import from `modules.claim`.
"""

from .models import DeclineReason
from .services import (
    add_attachment,
    announce_consent_needed,
    answer,
    clinic_counts,
    history,
    list_for_patients,
    list_incoming,
    list_sent,
    member_of,
    open_attachment,
    page_incoming,
    page_sent,
    payer_counts,
    payer_step,
    person_of,
    remove_attachment,
    scan_pending_attachments,
    send,
    withdraw,
)

__all__ = [
    "DeclineReason",
    "add_attachment",
    "announce_consent_needed",
    "answer",
    "clinic_counts",
    "history",
    "list_for_patients",
    "list_incoming",
    "list_sent",
    "member_of",
    "open_attachment",
    "page_incoming",
    "page_sent",
    "payer_counts",
    "payer_step",
    "person_of",
    "remove_attachment",
    "scan_pending_attachments",
    "send",
    "withdraw",
]
