"""Freeze today's event catalogue as the contract of a release (GHE-20 F-03, DEV-212).

    python manage.py freeze_event_contract 2026.11.16-baseline

Run it when a release goes out. From then on the test `test_contracts.py` refuses any change that
would break a consumer of that release (see foundation/contracts.py). Refuses to freeze a catalogue
that already breaks the previous frozen release, so a breaking change cannot be waved through by
freezing over it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from foundation.contracts import event_breaking_changes
from foundation.event_catalog import build_asyncapi
from foundation.timeutil import utc_now

FROZEN = Path(__file__).resolve().parents[2] / "released_events.json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def load_frozen() -> dict[str, Any] | None:
    if not FROZEN.exists():
        return None
    return json.loads(FROZEN.read_text(encoding="utf-8"))


class Command(BaseCommand):
    help = "Freeze the current event catalogue as the contract of a named release."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("release", help="the release name, e.g. 2026.11.16-a1b2c3d")

    def handle(self, *args: Any, **options: Any) -> None:
        release = options["release"]
        if not _NAME.match(release):
            raise CommandError("A release name is letters, digits, dots, dashes or underscores.")
        current = build_asyncapi()
        previous = load_frozen()
        if previous is not None:
            problems = event_breaking_changes(previous["asyncapi"], current)
            if problems:
                raise CommandError("These changes break the frozen release "
                                   f"{previous['release']}: " + "; ".join(problems))
        body = {"release": release, "frozen_at": utc_now().date().isoformat(),
                "asyncapi": current}
        FROZEN.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        self.stdout.write(f"Froze {len(current['components']['messages'])} events as {release}.")
