"""Re-encrypt every stored value onto the current field key. See foundation/rotation.py."""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from foundation import rotation
from foundation.crypto import current_key_id


class Command(BaseCommand):
    help = "Re-encrypt every stored value that still uses an old field key (after a key change)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true",
                            help="only count what would change")
        parser.add_argument("--batch", type=int, default=200,
                            help="rows per small transaction (default 200)")

    def handle(self, *args: Any, **options: Any) -> None:
        dry, batch = bool(options["dry_run"]), max(1, int(options["batch"]))
        self.stdout.write(f"Current field key: {current_key_id()}"
                          f"{' (dry run: nothing is changed)' if dry else ''}")
        total = rotation.RotationResult()
        for name, rotate in sorted(rotation.rotators().items()):
            done = rotate(batch, dry)
            total += done
            self.stdout.write(f"  {name}: {done.rotated} value(s) "
                              f"{'would move' if dry else 'moved'}, {done.unreadable} unreadable")
        self.stdout.write(f"Total: {total.rotated} {'would move' if dry else 'moved'}, "
                          f"{total.unreadable} unreadable (left as they were).")
