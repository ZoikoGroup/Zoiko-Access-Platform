"""Recurring webhook delivery (see foundation/periodic.py)."""
from .services import deliver_all_tenants

DELIVER_EVERY_SECONDS = 5.0

__all__ = ["DELIVER_EVERY_SECONDS", "deliver_all_tenants"]
