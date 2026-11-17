"""Public interface of the programme module (DEV-312): employer programme links. Every function
runs with the platform tenant bound.

This is the ONLY module other domain modules may import from `modules.programme`.
"""

from .services import (
    issue,
    leave,
    linked_by_clinic,
    list_for_employer,
    list_for_patients,
    person_of,
    redeem,
    revoke,
)

__all__ = [
    "issue",
    "leave",
    "linked_by_clinic",
    "list_for_employer",
    "list_for_patients",
    "person_of",
    "redeem",
    "revoke",
]
