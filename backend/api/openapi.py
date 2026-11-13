"""The OpenAPI 3.1 description of the live /api/v1 routes, built from the routes themselves so it
cannot list one that does not exist (GHE-13 T-01, DEV-150).

It describes what the code really does and nothing more: each path and method, whether a login is
needed, and the one error shape. Request and response bodies are not described field by field
(the views read plain JSON), so the schemas below are deliberately loose, and the document carries
no examples, so it can hold no real patient data (T-08)."""
from __future__ import annotations

import inspect
import re
from typing import Any

from django.urls import URLPattern, URLResolver

from foundation.errors import CATALOG

VERSION = "1.0.0"
_PARAM = re.compile(r"<(?:[a-z]+:)?([A-Za-z_][A-Za-z0-9_]*)>")


def _walk(patterns: list[Any], prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for p in patterns:
        if isinstance(p, URLResolver):
            found += _walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            found.append((prefix + str(p.pattern), p.callback))
    return found


def _methods_and_auth(view: Any) -> tuple[list[str], bool]:
    """(methods, needs a login). Wrapped views name their methods; the few open views are the ones
    guarded by Django's require_GET / require_POST."""
    allowed = getattr(view, "allowed_methods", None)
    if allowed:
        return sorted(allowed), True
    source = ""
    fn = view
    while fn is not None:
        try:
            source += inspect.getsource(fn)
        except (OSError, TypeError):
            pass
        fn = getattr(fn, "__wrapped__", None)
    methods = [m for m, marker in (("GET", "require_GET"), ("POST", "require_POST"))
               if marker in source]
    return (methods or ["GET", "POST"]), False


def _summary(view: Any) -> str:
    fn = inspect.unwrap(view)
    doc = (fn.__doc__ or "").strip().split("\n\n")[0].replace("\n", " ")
    return doc[:200] or fn.__name__.replace("_", " ")


def build_spec() -> dict[str, Any]:
    from . import urls

    problem = {"$ref": "#/components/schemas/Problem"}
    paths: dict[str, Any] = {}
    for route, view in sorted(_walk(urls.urlpatterns), key=lambda r: r[0]):
        methods, authed = _methods_and_auth(view)
        path = "/" + _PARAM.sub(r"{\1}", route)
        params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                  for n in _PARAM.findall(route)]
        item = paths.setdefault(path, {})
        for m in methods:
            op: dict[str, Any] = {
                "operationId": f"{m.lower()}_{inspect.unwrap(view).__name__}",
                "summary": _summary(view),
                "parameters": params,
                "responses": {
                    "200": {"description": "Success.",
                            "content": {"application/json": {"schema": {"type": "object"}}}},
                    "default": {"description": "An error, in the standard shape.",
                                "content": {"application/problem+json": {"schema": problem}}},
                },
                "security": [{"bearerAuth": []}] if authed else [],
            }
            if m in ("POST", "PUT", "PATCH"):
                op["requestBody"] = {"required": False, "content": {
                    "application/json": {"schema": {"type": "object"}}}}
                op["parameters"] = params + [{
                    "name": "Idempotency-Key", "in": "header", "required": False,
                    "description": "16 to 128 letters, digits, dashes. Used where the "
                                   "operation says it repeats safely.",
                    "schema": {"type": "string", "minLength": 16, "maxLength": 128}}]
            item[m.lower()] = op
    codes = sorted(c.value for c in CATALOG)
    return {
        "openapi": "3.1.0",
        "info": {"title": "Zoiko Access API", "version": VERSION,
                 "description": "The API the Zoiko Access web apps use. Generated from the "
                                "routes; request and response bodies are not described field "
                                "by field. Only v1 exists."},
        "servers": [{"url": "/api/v1"}],
        "paths": {k: {m: v[m] for m in sorted(v)} for k, v in paths.items()},
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {"Problem": {
                "type": "object",
                "required": ["type", "title", "status", "code", "request_id", "retryable"],
                "properties": {
                    "type": {"type": "string"}, "title": {"type": "string"},
                    "status": {"type": "integer"}, "detail": {"type": "string"},
                    "code": {"type": "string", "enum": codes},
                    "request_id": {"type": "string"}, "retryable": {"type": "boolean"},
                    "fields": {"type": "array", "items": {"type": "string"}}}}},
        },
    }
