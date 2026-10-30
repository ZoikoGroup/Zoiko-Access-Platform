"""Guard for small, reference-only JSON (event payloads, saved idempotent results).

This cannot detect personal data by meaning. What it does is make the safe shape the only
shape: flat, short values, plain key names, tiny total size. A name or a diagnosis does not
fit by accident, and a free-text note is refused.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_BYTES = 2048
MAX_STRING = 128
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PayloadError(ValueError):
    """The payload is not a small, flat, reference-only mapping."""


def check_reference_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PayloadError("payload must be a mapping")
    for key, value in payload.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise PayloadError("payload keys must be plain lowercase names")
        if isinstance(value, bool) or value is None or isinstance(value, int | float):
            continue
        if isinstance(value, str):
            if len(value) > MAX_STRING:
                raise PayloadError("payload strings must be short references, not text")
            continue
        raise PayloadError("payload values must be flat (no nested lists or objects)")
    if len(json.dumps(payload, separators=(",", ":")).encode()) > MAX_BYTES:
        raise PayloadError("payload is too large")
    return payload
