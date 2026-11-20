"""Service measurements and indicators (GHE-18; DEV-130): route patterns and counts only, two
indicators against proposed targets, a guarded metrics endpoint, a platform-only view."""
import json

import pytest
from django.test import Client

from foundation import metrics
from tests.test_api import API
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
TOKEN = "t" * 32


@pytest.fixture(autouse=True)
def fresh():
    metrics.reset()
    yield
    metrics.reset()


NOW = 1_800_000_000.0


def feed(n, status=200, seconds=0.05, route="api/v1/things", at=NOW):
    for _ in range(n):
        metrics.record(route, status, seconds, now=at)


# ------------------------------------------------------------------ the indicators
def test_one_failure_in_a_thousand_just_meets_the_availability_target():
    feed(999)
    feed(1, status=500)
    a = metrics.slo(5, now=NOW)["availability"]
    assert a["value"] == 0.999 and a["meeting"] is True and a["burn_rate"] == 1.0


def test_two_failures_in_a_thousand_miss_it_and_burn_twice_as_fast():
    feed(998)
    feed(2, status=503)
    a = metrics.slo(5, now=NOW)["availability"]
    assert a["meeting"] is False and a["burn_rate"] == 2.0


def test_client_errors_are_valid_answers_not_failures():
    feed(100, status=404)
    feed(100, status=401)
    feed(100, status=422)
    assert metrics.slo(5, now=NOW)["availability"]["value"] == 1.0


def test_latency_counts_the_share_answered_within_three_quarters_of_a_second():
    feed(95, seconds=0.2)
    feed(5, seconds=0.9)
    lat = metrics.slo(5, now=NOW)["latency"]
    assert lat["value"] == 0.95 and lat["meeting"] is True
    feed(1, seconds=2.0)
    assert metrics.slo(5, now=NOW)["latency"]["meeting"] is False


def test_with_no_requests_there_is_no_verdict_rather_than_a_perfect_score():
    result = metrics.slo(60, now=NOW)
    assert result["requests"] == 0
    assert result["availability"]["value"] is None and result["availability"]["meeting"] is None


def test_only_the_asked_window_counts():
    feed(10, status=500, at=NOW - 30 * 60)               # half an hour ago
    feed(10, at=NOW)
    assert metrics.slo(5, now=NOW)["requests"] == 10
    assert metrics.slo(5, now=NOW)["availability"]["value"] == 1.0
    assert metrics.slo(60, now=NOW)["requests"] == 20
    assert metrics.slo(60, now=NOW)["availability"]["value"] == 0.5


def test_the_window_is_never_longer_than_an_hour():
    feed(5, at=NOW - 90 * 60)
    assert metrics.slo(99999, now=NOW)["window_minutes"] == 60
    assert metrics.slo(99999, now=NOW)["requests"] == 0


def test_health_probes_and_the_metrics_endpoint_are_not_counted():
    for route in ("healthz", "readyz", "metrics", "/healthz/", "readyz"):
        metrics.record(route, 200, 0.001, now=NOW)
    assert metrics.slo(5, now=NOW)["requests"] == 0


# ------------------------------------------------------------------ labels carry no references
def test_a_route_is_a_pattern_never_the_real_address(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    Client().get("/api/v1/messages/threads/SECRETREF123XYZ")
    routes = [r["route"] for r in metrics.routes_summary()]
    assert any("<str:thread_id>" in r for r in routes)
    assert not any("SECRETREF123XYZ" in r for r in routes)
    assert "SECRETREF123XYZ" not in metrics.prometheus_text()


def test_odd_characters_in_a_label_are_replaced():
    metrics.record('api/"; drop{}\n', 200, 0.01, now=NOW)
    text = metrics.prometheus_text()
    assert 'drop{' not in text and '\n"' not in text.split("zoiko_http_requests_total")[1][:60]


def test_the_number_of_distinct_routes_is_capped():
    for i in range(metrics.MAX_ROUTES + 50):
        metrics.record(f"api/v1/r{i}", 200, 0.01, now=NOW)
    labels = {r["route"] for r in metrics.routes_summary()} | {
        line.split('route="')[1].split('"')[0]
        for line in metrics.prometheus_text().splitlines() if 'route="' in line}
    assert len(labels) <= metrics.MAX_ROUTES + 1 and "other" in labels


def test_the_latency_buckets_only_ever_grow():
    metrics.record("api/v1/x", 200, 0.3, now=NOW)
    metrics.record("api/v1/x", 200, 3.0, now=NOW)
    counts = [int(line.rsplit(" ", 1)[1]) for line in metrics.prometheus_text().splitlines()
              if line.startswith("zoiko_http_request_seconds_bucket")]
    assert counts == sorted(counts) and counts[-1] == 2


# ------------------------------------------------------------------ the metrics endpoint
def test_the_metrics_endpoint_is_off_unless_a_token_is_set(settings):
    settings.METRICS_TOKEN = ""
    client = Client()
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", HTTP_AUTHORIZATION="Bearer ").status_code == 404


def test_the_metrics_endpoint_needs_the_exact_token(settings):
    settings.METRICS_TOKEN = TOKEN
    client = Client()
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", HTTP_AUTHORIZATION="Bearer wrong").status_code == 404
    assert client.get("/metrics", HTTP_AUTHORIZATION=f"bearer {TOKEN}").status_code == 404
    ok = client.get("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    assert ok.status_code == 200 and ok["Content-Type"].startswith("text/plain")


def test_the_endpoint_gives_the_standard_text_format_and_nothing_personal(settings):
    settings.METRICS_TOKEN = TOKEN
    feed(3, route="api/v1/bookings/<str:booking_id>/confirm")
    feed(1, status=500, route="api/v1/bookings/<str:booking_id>/confirm")
    body = Client().get("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}").content.decode()
    assert '# TYPE zoiko_http_requests_total counter' in body
    route = "api/v1/bookings/<str:booking_id>/confirm"
    assert f'zoiko_http_requests_total{{route="{route}",class="2xx"}} 3' in body
    assert 'class="5xx"} 1' in body
    assert "tenant" not in body and "person" not in body.replace("personal", "")


def test_the_metrics_endpoint_does_not_count_itself(settings):
    settings.METRICS_TOKEN = TOKEN
    client = Client()
    for _ in range(3):
        client.get("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        client.get("/healthz")
        client.get("/readyz")
    assert metrics.slo(5)["requests"] == 0


def test_a_real_request_through_the_site_is_counted_with_its_status(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    Client().get("/api/v1/me")                                   # no token: a 401
    row = next(r for r in metrics.routes_summary() if r["route"].endswith("v1/me"))
    assert row["requests"] == 1 and row["server_errors"] == 0


# ------------------------------------------------------------------ the platform view
@pytest.fixture
def world(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    w = Platform()
    w.staff("sam", "R11")
    w.staff("iyer", "R12")
    return w


def test_platform_staff_see_the_indicators_the_targets_and_the_limits(world):
    feed(10)
    for who in ("sam", "iyer"):
        r = world.client.get(API + "platform/slo", **world.header(world.tenant_public_id, who))
        assert r.status_code == 200
        body = json.loads(r.content)
        assert [w["window_minutes"] for w in body["windows"]] == [5, 60]
        assert body["targets"] == {"availability": 0.999, "latency_seconds": 0.75,
                                   "latency_share": 0.95}
        assert len(body["limits"]) == 3


def test_nobody_else_can_see_the_indicators(world):
    from tests.test_clinician import Practice
    p = Practice()
    assert p.get("platform/slo", subject="ops").status_code == 403
    assert p.get("platform/slo", subject="pat").status_code == 403
    assert p.get("platform/slo", subject="doc").status_code == 403
