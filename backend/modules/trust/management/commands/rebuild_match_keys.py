"""Rebuild the search fingerprints used to find possible duplicate patients.

Run it after a field-key rotation, and once after upgrading, so details saved before the
fingerprints existed can be found. Safe to run again.

    python manage.py rebuild_match_keys
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust


class Command(BaseCommand):
    help = "Rebuild the duplicate-search fingerprints of every patient."

    def handle(self, *args: Any, **options: Any) -> None:
        total = 0
        for tenant in trust.list_tenants():
            with tenant_transaction(trust.context_for(tenant["public_id"])):
                total += trust.rebuild_match_keys()
        self.stdout.write(f"rebuilt {total} patient(s)")
