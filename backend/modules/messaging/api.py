"""Public interface of the messaging module (D16).

This is the ONLY module other domain modules may import from `modules.messaging`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .attachments import MAX_BYTES, MAX_FILES, content_of, find, register_scanner, reset_scanner
from .services import (
    close_thread,
    get_thread,
    list_threads,
    open_thread,
    overdue_threads_for_staff,
    read_messages,
    reply,
    report,
    start_thread,
    unanswered_threads,
    unread_threads_for_staff,
)

__all__ = ["MAX_BYTES", "MAX_FILES", "content_of", "find", "register_scanner", "reset_scanner",
           "close_thread", "get_thread", "list_threads", "open_thread",
           "overdue_threads_for_staff", "read_messages", "reply", "report", "start_thread",
           "unanswered_threads", "unread_threads_for_staff"]
