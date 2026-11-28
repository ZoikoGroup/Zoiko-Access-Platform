"""Real texts go only to a market that is switched on (GHE-16 TC01, TC07; DEV-228).

A text through a real provider reaches a phone in some country. It may only be sent while a signed,
current country pack for the customer's country switches on messaging; when the pack is suspended,
expires or was never signed, nothing is sent. The development stand-in (`console`) and "no
provider" send nothing real, so they are not checked.
"""
from __future__ import annotations

from django.conf import settings

from foundation.cell_context import require_context
from modules.jurisdiction import api as jurisdiction

CAPABILITY = "messaging"


def texts_allowed() -> bool:
    if getattr(settings, "TEXT_PROVIDER", "") not in ("twilio", "sns"):   # real providers
        return True
    return CAPABILITY in jurisdiction.customer_capabilities(require_context().tenant_id)
