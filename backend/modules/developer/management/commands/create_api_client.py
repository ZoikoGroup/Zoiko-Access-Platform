"""Create an API client for a customer and print its key ONCE (only a hash is kept).

    python manage.py create_api_client <tenant id> "Acme Labs" --scopes catalog:read events:read
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from foundation.errors import ZoikoError
from modules.developer import api as developer


class Command(BaseCommand):
    help = "Create an API client and print its key once."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("tenant")
        parser.add_argument("name")
        parser.add_argument("--scopes", nargs="+", required=True, choices=list(developer.SCOPES))
        parser.add_argument("--environment", choices=["sandbox", "production"], default="sandbox")
        parser.add_argument("--organization", default="",
                            help="the clinic that owns it (its public id); empty = whole customer")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            client, key = developer.create_client(
                tenant_public_id=options["tenant"], name=options["name"],
                environment=options["environment"], scopes=options["scopes"],
                organization_ref=options["organization"], created_by_ref="management_command")
        except ZoikoError as exc:
            raise CommandError(f"refused: {exc.code.value}") from None
        self.stdout.write(f"client {client['public_id']} ({client['environment']})")
        self.stdout.write(f"key {key}")
        self.stdout.write("Store the key now: it cannot be shown again.")
