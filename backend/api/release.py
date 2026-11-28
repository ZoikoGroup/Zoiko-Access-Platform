"""One release identity (DEV-212).

A release is more than its build label: two servers with the same `ZOIKO_BUILD` but different
routes, events or database migrations are not the same release. This joins all four into one id,
so a dashboard, an audit row's build label and a rollout check can all compare one value. It holds
no data about people, so `GET /api/v1/version` is open like `/openapi.json`.
"""
from __future__ import annotations

import functools
import hashlib
import json
from typing import Any

from django.conf import settings


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@functools.cache
def _contracts() -> dict[str, str]:
    """Computed once per process: the code cannot change under a running server."""
    from django.db.migrations.loader import MigrationLoader

    from foundation.event_catalog import build_asyncapi

    from .openapi import build_spec

    loader = MigrationLoader(None, ignore_no_migrations=True)
    leaves = sorted(f"{app}.{name}" for app, name in loader.graph.leaf_nodes())
    return {"api_contract": _digest(build_spec()), "event_contract": _digest(build_asyncapi()),
            "migrations": _digest(leaves)}


def release_identity() -> dict[str, str]:
    parts = {"build": str(settings.ZOIKO_BUILD), "cell": str(settings.ZOIKO_CELL_ID),
             **_contracts()}
    combined = {k: v for k, v in parts.items() if k != "cell"}   # one release runs in many cells
    return {**parts, "release_id": f"{parts['build']}+{_digest(combined)}"}


def version_document(request: Any) -> Any:
    """GET /api/v1/version: which release this server runs."""
    from django.http import HttpResponseNotAllowed, JsonResponse

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    response = JsonResponse(release_identity())
    response["Cache-Control"] = "no-store"
    return response
