"""Test-only URL set: views that fail in different ways."""
from django.http import Http404, HttpResponse
from django.urls import path

from foundation.errors import ErrorCode, ZoikoError


def ok(request):
    return HttpResponse("ok")


def denied(request):
    raise ZoikoError(ErrorCode.POLICY_DENIED)


def invalid(request):
    raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["birth_date", "address.postcode"])


def conflict(request):
    raise ZoikoError(ErrorCode.CONFLICT_VERSION)


def leaky_crash(request):
    # A realistic accident: a developer puts real data in an exception message.
    raise ValueError("patient John Smith, NHS 943 476 5919, has HIV")


def leaky_missing(request):
    raise Http404("no patient called John Smith")


urlpatterns = [
    path("ok", ok),
    path("denied", denied),
    path("invalid", invalid),
    path("conflict", conflict),
    path("crash", leaky_crash),
    path("missing", leaky_missing),
]

handler400 = "foundation.error_views.bad_request"
handler403 = "foundation.error_views.permission_denied"
handler404 = "foundation.error_views.not_found"
handler500 = "foundation.error_views.server_error"
