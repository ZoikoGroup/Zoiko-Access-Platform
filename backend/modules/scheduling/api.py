"""Public interface of the scheduling module (D04).

This is the ONLY module other domain modules may import from `modules.scheduling`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .history import booking_history
from .services import (
    book,
    booking_counts,
    cancel,
    confirm,
    confirm_from_provider,
    create_slots,
    decline_from_provider,
    forecast_week,
    get_booking,
    get_slot,
    list_bookings,
    list_bookings_for_provider,
    list_open_slots,
    mark_attendance,
    overdue_requests,
    provider_load,
    report,
    reschedule,
    today_counts,
    usual_confirmation_hours,
    waiting_requests,
    withdraw_slot,
    withdraw_slots,
)

__all__ = [
    "book", "booking_counts", "booking_history", "cancel", "confirm", "confirm_from_provider",
    "create_slots", "decline_from_provider",
    "forecast_week", "get_booking", "get_slot",
    "list_bookings", "list_bookings_for_provider", "list_open_slots", "mark_attendance",
    "overdue_requests", "provider_load", "report", "reschedule", "today_counts",
    "usual_confirmation_hours", "waiting_requests", "withdraw_slot", "withdraw_slots",
]
