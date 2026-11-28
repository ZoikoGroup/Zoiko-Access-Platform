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


SUPPORTED_API_VERSIONS = ("v1",)


def unsupported_version(request: HttpRequest, version: str, rest: str = "") -> HttpResponse:
    """A request under /api/<version>/ that no route answered (GHE-13 T-05, DEV-149). An unknown
    major version gets the standard not-found body plus the versions we do serve: never a crash, a
    guess at another version, or a redirect that could replay a request. A known version with an
    unknown path is an ordinary not-found."""
    response = problem_response(ErrorCode.NOT_FOUND)
    if version not in SUPPORTED_API_VERSIONS:
        response["X-Supported-Api-Versions"] = ", ".join(SUPPORTED_API_VERSIONS)
        response["Link"] = '</api/v1/>; rel="successor-version"'
    return response
