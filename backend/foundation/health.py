"""Liveness and readiness probes. Return no data about people, only service state."""
from django.db import connection
from django.http import HttpRequest, JsonResponse


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. Does not touch the database."""
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: the process can serve traffic (database reachable)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - any failure means not ready; never leak the error text
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
