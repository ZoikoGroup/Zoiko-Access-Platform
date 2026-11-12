"""Send due webhook deliveries. `--once` runs one pass (for a scheduler); otherwise it loops."""
from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand

from modules.integration import api as integration


class Command(BaseCommand):
    help = "Send due webhook deliveries to registered endpoints."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=5.0)

    def handle(self, *args: Any, **options: Any) -> None:
        while True:
            counts = integration.deliver_all_tenants()
            if any(counts.values()):
                self.stdout.write(f"webhooks: {counts}")
            if options["once"]:
                return
            time.sleep(options["interval"])
