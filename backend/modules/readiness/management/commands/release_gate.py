"""The deploy step's gate (decided 27 Nov 2026, DEV-397): a release goes out only when the
readiness register says so. Run by the deploy job BEFORE `migrate`; a non-zero exit stops it.

    python manage.py release_gate --release rel-2026-12-1 --build "$ZOIKO_BUILD"
    python manage.py release_gate --release rel-2026-12-1 --build "$ZOIKO_BUILD" --country GB

Passes only when the release exists in Zoiko's platform record, is for THIS build, and its decision
worked out NOW is ready for production (G3: every requirement signed, including the CTO and the
Clinical safety lead, two key holders, and every system check), or authorised for the country (G4)
when --country is given. It prints what blocks it, never who signed or anything about anyone.
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from foundation.cell_context import tenant_transaction
from modules.readiness.models import Release
from modules.readiness.services import evaluate_now
from modules.trust import api as trust

PASS = {"g3": ("g3_ready", "g4_authorized"), "g4": ("g4_authorized",)}


class Command(BaseCommand):
    help = "Refuse a deploy unless the readiness register approves this exact release and build."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--release", required=True)
        parser.add_argument("--build", required=True)
        parser.add_argument("--country", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        platforms = [t for t in trust.list_tenants() if t["type"] == "platform"]
        if len(platforms) != 1:
            raise CommandError("refused: no single Zoiko platform record in this cell")
        with tenant_transaction(trust.context_for(platforms[0]["public_id"])):
            rel = Release.objects.filter(release_ref=options["release"]).first()
            if rel is None:
                raise CommandError("refused: no such release in the readiness register")
            if rel.build != options["build"]:
                raise CommandError("refused: the release was approved for a different build")
            country = options["country"].upper()
            if country and rel.country != country:
                raise CommandError("refused: the release is for a different country")
            now = evaluate_now(rel)
        if now["decision"] not in PASS["g4" if country else "g3"]:
            raise CommandError("refused: not approved; blocked by " + ", ".join(now["blockers"]))
        self.stdout.write(f"approved: {options['release']} ({now['decision']})")
