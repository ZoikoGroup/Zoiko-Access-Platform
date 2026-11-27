"""Publish the workflow definitions shipped with the code (modules/workflow/bundled/*.json).

Safe to run again: a version that is already released with the same content is left alone, and one
with different content is refused (a released version never changes).
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from foundation.errors import ZoikoError
from modules.workflow import api as workflow


class Command(BaseCommand):
    help = "Publish the bundled workflow definitions."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            done = workflow.load_bundled_definitions()
        except ZoikoError as exc:
            raise CommandError(f"refused: {exc.code.value}") from None
        for item in done:
            self.stdout.write(f"{item['key']} v{item['version']} {item['state']}")
