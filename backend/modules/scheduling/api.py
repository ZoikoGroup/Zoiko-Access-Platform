"""Public interface of the scheduling module (D04).

This is the ONLY module other domain modules may import from `modules.scheduling`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    book,
    booking_counts,
    cancel,
    confirm,
    create_slots,
    get_booking,
    get_slot,
    list_bookings,
    list_bookings_for_provider,
    list_open_slots,
    mark_attendance,
    overdue_requests,
    report,
    reschedule,
    usual_confirmation_hours,
)

__all__ = [
    "book", "booking_counts", "cancel", "confirm", "create_slots", "get_booking", "get_slot",
    "list_bookings", "list_bookings_for_provider", "list_open_slots", "mark_attendance",
    "overdue_requests", "report", "reschedule",
    "usual_confirmation_hours",
]
