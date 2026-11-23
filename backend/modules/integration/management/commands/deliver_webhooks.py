"""Send due webhook deliveries. `--once` runs one pass (for a scheduler); otherwise it loops.

Like the outbox worker (`relay_outbox`), it refuses to start with development-only settings or a
database login that could skip row-level security, and while the database is not ready (for
example migrations still running on a fresh start) it waits and tries again instead of crashing
(DEV-359). It logs counts only, never a payload or an exception message.
"""
from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, ProgrammingError, close_old_connections

from foundation.dbrole import assert_safe_database_role
from foundation.release_check import assert_release_ready
from modules.integration import api as integration

DATABASE_RETRY_SECONDS = 5.0


def startup_guard() -> None:
    """The same checks the outbox worker makes before its first pass."""
    assert_release_ready()
    assert_safe_database_role()


def run_loop(stop: threading.Event, *, interval: float, once: bool, log: Any,
             guard: Callable[[], None] | None = None) -> None:
    guarded = guard is None
    while True:
        close_old_connections()
        wait = interval
        try:
            if not guarded and guard is not None:
                guard()
                guarded = True
            counts = integration.deliver_all_tenants()
        except (OperationalError, ProgrammingError) as exc:
            if once:
                raise CommandError(f"database not available: {type(exc).__name__}") from None
            log(f"database not available ({type(exc).__name__}); retrying in "
                f"{DATABASE_RETRY_SECONDS:.0f}s")
            wait = DATABASE_RETRY_SECONDS
        else:
            if any(counts.values()):
                log(f"webhooks: {counts}")
        if once or stop.wait(wait):
            return


class Command(BaseCommand):
    help = "Send due webhook deliveries to registered endpoints."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=5.0)

    def handle(self, *args: Any, **options: Any) -> None:
        stop = threading.Event()
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
        log = lambda text: self.stdout.write(text)  # noqa: E731 - tiny local helper
        try:
            run_loop(stop, interval=options["interval"], once=options["once"], log=log,
                     guard=startup_guard)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from None
