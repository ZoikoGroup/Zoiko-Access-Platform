"""Recurring jobs the worker runs between delivering events and sending email.

A module registers a job at start-up (`register`), and the worker calls `run_due()` on every pass.
A job decides for itself what is due (reminders look at appointment times), so running it often is
harmless; `every_seconds` only limits how often the worker even asks.

A job that fails never stops the worker or the other jobs, and only the exception CLASS is
reported, never its message, because messages can hold personal data.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

Job = Callable[[], int]      # does the work, returns how many things it handled


@dataclass
class _Task:
    name: str
    every_seconds: float
    job: Job
    last_started: float | None = None


_tasks: dict[str, _Task] = {}


def register(name: str, every_seconds: float, job: Job) -> None:
    """Add (or replace) a job by name. Safe to call again."""
    _tasks[name] = _Task(name=name, every_seconds=every_seconds, job=job)


def registered() -> list[str]:
    return sorted(_tasks)


def run_due(now: Callable[[], float] = time.monotonic) -> dict[str, int | str]:
    """Run every job whose interval has passed. Returns {name: handled-count, or "failed:Class"}."""
    results: dict[str, int | str] = {}
    for task in list(_tasks.values()):
        moment = now()
        if task.last_started is not None and moment - task.last_started < task.every_seconds:
            continue
        task.last_started = moment
        try:
            results[task.name] = task.job()
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the others
            results[task.name] = f"failed:{type(exc).__name__}"
    return results
