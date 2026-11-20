"""The worker's job that erases shared names once an agreement has run out (DEV-301)."""

from __future__ import annotations

from foundation.cell_context import tenant_transaction

from .services import erase_expired_identities, escalate_old_holds


def erase_expired_all() -> int:
    """Look in Zoiko's platform space, where the exchange lives, and erase what has expired."""
    from modules.trust import api as trust  # imported late: trust itself reaches the exchange

    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    if platform is None:
        return 0
    with tenant_transaction(trust.context_for(platform["public_id"])):
        return erase_expired_identities()


def escalate_holds_all() -> int:
    """Escalate requests that have been held too long (DEV-303)."""
    from modules.trust import api as trust

    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    if platform is None:
        return 0
    with tenant_transaction(trust.context_for(platform["public_id"])):
        return escalate_old_holds()
