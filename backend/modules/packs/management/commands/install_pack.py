"""Install a signed country pack, or a revocation notice, from a JSON file.

    python manage.py install_pack GB-v3.json
    python manage.py install_pack revoke-notice.json --notice

The pack is checked exactly as at run time (signature, dates, revocation epoch, newer version).
"""
from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from modules.packs import api as packs


class Command(BaseCommand):
    help = "Install a signed country pack (or a revocation notice) from a JSON file."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("path")
        parser.add_argument("--notice", action="store_true", help="the file is a revocation notice")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            with open(options["path"], encoding="utf8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            raise CommandError("cannot read the file as JSON") from None
        try:
            done = (packs.apply_revocation_notice(document) if options["notice"]
                    else packs.install_pack(document))
        except packs.PackRejected as exc:
            raise CommandError(f"refused: {exc.reason}") from None
        self.stdout.write(json.dumps(done, sort_keys=True))
