"""The worker's daily job that erases note text whose NHS retention period has run out (DEV-324).

It runs whether or not the notes switch is on: text already stored must still go on time.
"""

from __future__ import annotations

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust

from .services import erase_expired, people_with_notes

RETENTION_EVERY_SECONDS = 86_400.0


def erase_expired_all_tenants() -> int:
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            people = people_with_notes()
            if not people:
                continue
            births = {
                ref: p.get("birth_date", "")
                for ref, p in trust.get_person_profiles(people).items()
            }
            total += erase_expired(births, death_dates=trust.death_dates(people))
    return total
