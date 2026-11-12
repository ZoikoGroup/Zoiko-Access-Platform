"""Load the developer's own readings of the specifications' acceptance tests (docs/*-TRACE.md) into
the requirements matrix of every platform tenant. Safe to repeat: it only adds and updates."""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from modules.traceability import api as traceability


class Command(BaseCommand):
    help = "Load the recorded acceptance-test readings into the requirements matrix."

    def handle(self, *args: Any, **options: Any) -> None:
        counts = traceability.load_seed_everywhere()
        self.stdout.write(f"requirements matrix: {counts}")
