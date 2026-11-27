"""Test helper (T-43): a new partner has no secret until a Zoiko support person records the
identity, legal and security checks and the clinic then issues the secret. Tests that just need a
working partner call `issue()`."""
from __future__ import annotations

import json
from typing import Any

from tests.test_api import API
from tests.test_platform import Platform

CHECKS = ("identity", "legal", "security", "sandbox_test")      # DEV-401


def zoiko(world: Any) -> Platform:
    """Zoiko's own tenant with one support person, made once per test world."""
    found = getattr(world, "_zoiko", None)
    if found is None:
        found = Platform()
        found.sam = found.staff("zsam", "R11")
        found.tess = found.staff("ztess", "R11")
        world._zoiko = found
    return found


def tenant_of(world: Any) -> str:
    return getattr(world, "tenant_public_id", None) or world.clinic.tenant_public_id


def check(world: Any, client_id: str, which: str, note: str = "Looked at it in the test",
          subject: str = "zsam", customer: str | None = None):
    z = zoiko(world)
    return z.client.post(
        API + f"platform/partner-checks/{customer or tenant_of(world)}/{client_id}/{which}",
        json.dumps({"note": note}), content_type="application/json",
        **z.header(z.tenant_public_id, subject))


def record_all_checks(world: Any, client_id: str) -> None:
    for which in CHECKS:
        r = check(world, client_id, which)
        assert r.status_code == 200, r.content


def issue(world: Any, client: dict[str, Any], subject: str = "boss") -> dict[str, Any]:
    """Record the three checks, then issue the secret: returns the client with `secret`."""
    record_all_checks(world, client["public_id"])
    r = world.post(f"staff/integrations/partners/{client['public_id']}/issue-secret", {},
                   subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["client"]
