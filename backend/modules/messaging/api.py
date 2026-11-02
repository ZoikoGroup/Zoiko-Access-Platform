"""Public interface of the messaging module (D16).

This is the ONLY module other domain modules may import from `modules.messaging`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    close_thread,
    get_thread,
    list_threads,
    open_thread,
    overdue_threads_for_staff,
    reply,
    report,
    start_thread,
    unread_threads_for_staff,
)

__all__ = ["close_thread", "get_thread", "list_threads", "open_thread",
           "overdue_threads_for_staff", "reply", "report", "start_thread",
           "unread_threads_for_staff"]
