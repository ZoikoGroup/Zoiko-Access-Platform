"""Public interface of the comms module (D05).

This is the ONLY module other domain modules may import from `modules.comms`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    email_notices_enabled,
    get_notification,
    list_for_persons,
    mark_read,
    set_email_notices,
    unread_count,
)
from .subscribers import register_subscribers

__all__ = ["email_notices_enabled", "get_notification", "list_for_persons", "mark_read",
           "register_subscribers", "set_email_notices", "unread_count"]
