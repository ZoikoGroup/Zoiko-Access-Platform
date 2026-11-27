"""Unused accounts for the retention report (DEV-398, suggested 2 years after the last sign-in):
counted only. Closing an account is a person's decision with its own steps; nothing here does it."""
from __future__ import annotations

import datetime as dt

from django.db.models import Q

from foundation.cell_context import require_context

from .models import Credential


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Password logins not used since the cut-off (never used: counted from when they were made)."""
    ctx = require_context()
    return (Credential.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
            .filter(Q(last_login_at__lt=cutoff)
                    | Q(last_login_at__isnull=True, created_at__lt=cutoff))
            .count())
