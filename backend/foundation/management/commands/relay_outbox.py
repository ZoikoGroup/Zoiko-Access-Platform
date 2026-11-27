"""The worker: delivers queued outbox events to their subscribers AND sends queued emails.

    python manage.py relay_outbox              # run until stopped (Ctrl+C or SIGTERM)
    python manage.py relay_outbox --once       # one pass, then exit (cron, tests)

Safe to run several copies at once (they skip rows another copy has locked). It logs counts only:
never an event payload or an exception message, because those can hold personal data.
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
from foundation.dispatch import dispatch
from foundation.mail import send_due
from foundation.outbox import RelayResult, relay_once
from foundation.periodic import run_due
from foundation.release_check import assert_release_ready

DATABASE_RETRY_SECONDS = 5.0


def startup_guard() -> None:
    """What the real worker checks before its first pass: no development-only settings, and a
    database login that cannot skip row-level security."""
    assert_release_ready()
    assert_safe_database_role()


def run_loop(
    stop: threading.Event, *, interval: float, batch: int, once: bool, log: Any,
    guard: Callable[[], None] | None = None,
) -> int:
    """The worker loop (events, then email); returns how many events were published.

    `guard` runs once, before the first pass that reaches the database (the real command passes the
    database-login check, so a worker that could skip row-level security never starts)."""
    total = 0
    guarded = guard is None
    while True:
        close_old_connections()
        wait = interval
        try:
            if not guarded and guard is not None:
                guard()
                guarded = True
            result: RelayResult = relay_once(dispatch, batch_size=batch)
        except (OperationalError, ProgrammingError) as exc:
            # The database is not ready or went away (for example migrations still running).
            if once:
                raise CommandError(f"database not available: {type(exc).__name__}") from None
            log(f"database not available ({type(exc).__name__}); retrying in "
                f"{DATABASE_RETRY_SECONDS:.0f}s")
            wait = DATABASE_RETRY_SECONDS
        else:
            try:
                mail = send_due()
            except (OperationalError, ProgrammingError):
                mail = None
            if mail and (mail.sent or mail.retrying or mail.failed):
                log(f"mail sent={mail.sent} retrying={mail.retrying} failed={mail.failed}")
            try:
                for name, outcome in run_due().items():
                    if outcome not in (0, None):
                        log(f"job {name}: {outcome}")
            except (OperationalError, ProgrammingError):
                pass                    # the next pass tries again
            total += result.published
            busy = result.published + result.retrying + result.quarantined
            if busy:
                log(f"published={result.published} retrying={result.retrying} "
                    f"quarantined={result.quarantined}")
            # A full batch means there is probably more waiting: go again at once.
            wait = 0.0 if result.published >= batch else interval
        if once or stop.wait(wait):
            return total


class Command(BaseCommand):
    help = "Deliver queued outbox events to their subscribers (the event-delivery worker)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true", help="one pass, then exit")
        parser.add_argument("--interval", type=float, default=1.0,
                            help="seconds to rest when idle (default 1)")
        parser.add_argument("--batch", type=int, default=50, help="events per pass (default 50)")

    def handle(self, *args: Any, **options: Any) -> None:
        if not 0.05 <= options["interval"] <= 3600 or not 1 <= options["batch"] <= 1000:
            raise CommandError("interval must be 0.05-3600 seconds and batch 1-1000")
        stop = threading.Event()
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
        log = lambda text: self.stdout.write(text)  # noqa: E731 - tiny local helper
        if not options["once"]:
            log("relay_outbox started")
        try:
            total = run_loop(stop, interval=options["interval"], batch=options["batch"],
                             once=options["once"], log=log, guard=startup_guard)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from None
        log(f"relay_outbox stopped; published {total} event(s)")
