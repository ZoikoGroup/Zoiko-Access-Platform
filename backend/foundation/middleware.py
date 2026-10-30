"""Two middlewares.

RequestIdMiddleware     gives every request an id and returns it in the X-Request-ID header.
ErrorHandlingMiddleware turns any exception raised in a view into a problem+json response.
                        Unexpected exceptions are logged by TYPE and LOCATION only, never by
                        message, because a message can contain personal data.
"""
from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Callable

from django.http import Http404, HttpRequest, HttpResponse

from .errors import ErrorCode, ZoikoError
from .problem import problem_response
from .request_context import META_KEY, accept_or_new, reset_request_id, set_request_id

logger = logging.getLogger("foundation.errors")


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = accept_or_new(request.headers.get("X-Request-ID"))
        request.META[META_KEY] = request_id
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)
        response["X-Request-ID"] = request_id
        return response


def _safe_location(exc: BaseException) -> str:
    """Where the exception was raised (file:line in function). Never the exception message."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    last = frames[-1]
    return f"{os.path.basename(last.filename)}:{last.lineno} in {last.name}"


class ErrorHandlingMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse:
        if isinstance(exception, ZoikoError):
            return problem_response(exception.code, exception.fields)
        if isinstance(exception, Http404):
            return problem_response(ErrorCode.NOT_FOUND)
        logger.error(
            "unhandled_exception type=%s at=%s",
            type(exception).__name__,
            _safe_location(exception),
        )
        return problem_response(ErrorCode.INTERNAL_ERROR)
