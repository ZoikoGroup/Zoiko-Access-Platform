"""Public interface of the comms module (D05).

This is the ONLY module other domain modules may import from `modules.comms`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""

from . import alert_phone
from .exchange_alerts import alert_critical_result, send_escalation_alerts
from .phone import confirm_code, phone_verified, send_code
from .services import (
    email_notices_enabled,
    get_notification,
    list_for_persons,
    mark_read,
    quiet_hours_of,
    set_email_notices,
    set_quiet_hours,
    set_texts,
    texts_enabled,
    unread_count,
)
from .subscribers import register_subscribers

__all__ = [
    "alert_phone",
    "alert_critical_result",
    "confirm_code",
    "email_notices_enabled",
    "get_notification",
    "list_for_persons",
    "mark_read",
    "phone_verified",
    "quiet_hours_of",
    "register_subscribers",
    "send_code",
    "send_escalation_alerts",
    "set_email_notices",
    "set_quiet_hours",
    "set_texts",
    "texts_enabled",
    "unread_count",
]
