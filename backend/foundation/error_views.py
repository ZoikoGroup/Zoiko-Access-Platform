"""Django's own error pages (bad URL, bad host, crash outside a view) in our error format."""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse

from .errors import ErrorCode
from .problem import problem_response


def bad_request(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return problem_response(ErrorCode.VALIDATION_FAILED)


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return problem_response(ErrorCode.POLICY_DENIED)


def not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return problem_response(ErrorCode.NOT_FOUND)


def server_error(request: HttpRequest) -> HttpResponse:
    return problem_response(ErrorCode.INTERNAL_ERROR)
