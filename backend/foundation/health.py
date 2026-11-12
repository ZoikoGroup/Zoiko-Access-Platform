"""Liveness and readiness probes. Return no data about people, only service state."""
import hmac

from django.conf import settings
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse

from . import metrics


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. Does not touch the database."""
    return JsonResponse({"status": "ok"})


def metrics_text(request: HttpRequest) -> HttpResponse:
    """Prometheus-format request numbers, for a monitoring system. Off unless ZOIKO_METRICS_TOKEN is
    set; then it needs `Authorization: Bearer <that token>`. Route patterns and counts only."""
    token = getattr(settings, "METRICS_TOKEN", "")
    given = request.headers.get("Authorization", "")
    if not token or not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
        raise Http404          # not even a 401: an off or guarded endpoint looks like nothing
    return HttpResponse(metrics.prometheus_text(), content_type="text/plain; version=0.0.4")


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: the process can serve traffic (database reachable)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - any failure means not ready; never leak the error text
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
