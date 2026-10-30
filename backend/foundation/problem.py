"""Build the HTTP error response. One place, so every error looks the same."""
from __future__ import annotations

from collections.abc import Iterable

from django.http import JsonResponse

from .errors import CATALOG, ErrorCode, problem_body
from .request_context import get_request_id


def problem_response(code: ErrorCode, fields: Iterable[str] = ()) -> JsonResponse:
    response = JsonResponse(
        problem_body(code, get_request_id(), fields),
        status=CATALOG[code].status,
        content_type="application/problem+json",
    )
    response["Cache-Control"] = "no-store"
    return response
