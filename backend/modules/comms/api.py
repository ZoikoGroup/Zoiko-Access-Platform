"""Public interface of the comms module (D05).

This is the ONLY module other domain modules may import from `modules.comms`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import get_notification, list_for_persons, mark_read, unread_count
from .subscribers import register_subscribers

__all__ = ["get_notification", "list_for_persons", "mark_read", "register_subscribers",
           "unread_count"]
