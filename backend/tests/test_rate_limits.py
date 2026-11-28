"""How often one caller may call the API (DEV-387): per network address (stricter for sign-in),
per signed-in login (reading and changing apart), per customer, with safety-critical routes never
blocked by the customer-wide cap; 429 with a bounded Retry-After; nothing personal stored; and
production must enforce. Mostly refusals."""
import json
import logging

import pytest
from django.test import Client

from foundation import ratelimit
from foundation.models import RateWindow
from foundation.release_check import release_problems
from foundation.timeutil import FixedClock, utc_now
from tests.test_clinician import Practice
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def limits(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_RATE_LIMITING = "enforce"
    settings.RATE_LIMITS = {"ip": 1000, "ip_auth": 3, "actor_read": 4, "actor_write": 2,
                            "tenant": 6}


@pytest.fixture
def p():
    return Practice()


def sign_in_attempt(address="10.1.2.3"):
    return Client(REMOTE_ADDR=address).post(
        "/api/v1/auth/login", json.dumps({"organization": "x", "login": "y", "password": "z"}),
        content_type="application/json")


# ------------------------------------------------------------------ per network address
def test_sign_in_attempts_from_one_address_are_limited_with_retry_after():
    for _ in range(3):
        assert sign_in_attempt().status_code == 401
    r = sign_in_attempt()
    assert r.status_code == 429 and json.loads(r.content)["code"] == "RATE_LIMITED"
    assert 1 <= int(r["Retry-After"]) <= 60
    assert sign_in_attempt("10.9.9.9").status_code == 401      # another address is not affected


def test_every_api_request_counts_towards_the_address_limit(settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "ip": 2}
    client = Client(REMOTE_ADDR="10.4.4.4")
    assert client.get("/api/v1/version").status_code == 200
    assert client.get("/api/v1/version").status_code == 200
    assert client.get("/api/v1/version").status_code == 429


def test_health_checks_are_never_limited(settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "ip": 1}
    client = Client(REMOTE_ADDR="10.5.5.5")
    for _ in range(3):
        assert client.get("/healthz").status_code == 200


# ------------------------------------------------------------------ per login and per customer
def test_a_login_has_its_own_read_limit(p):
    for _ in range(4):
        assert p.get("me", subject="pat").status_code == 200
    r = p.get("me", subject="pat")
    assert r.status_code == 429 and 1 <= int(r["Retry-After"]) <= 60
    assert p.get("me", subject="ops").status_code == 200        # another login is not affected


def test_changing_things_has_a_smaller_limit_than_reading(p):
    # (A development login has no password, so signing out answers 404: the limit is counted
    # before the endpoint runs either way.)
    for _ in range(2):
        assert p.post("auth/logout", {}, subject="doc").status_code != 429
    assert p.post("auth/logout", {}, subject="doc").status_code == 429
    assert p.get("me", subject="doc").status_code == 200        # reading has its own limit


def test_one_customer_cannot_use_more_than_its_share(p, settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "actor_read": 100}
    codes = [p.get("me", subject=who).status_code for who in ("pat", "ops", "doc") * 3]
    assert codes[:6] == [200] * 6 and set(codes[6:]) == {429}
    other = Practice()                                           # another customer: unaffected
    assert other.get("me", subject="pat").status_code == 200


def test_safety_critical_work_is_not_blocked_by_the_customer_cap(p, settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "actor_read": 100}
    for _ in range(6):
        p.get("me", subject="ops")                               # the clinic uses up its share
    assert p.get("me", subject="pat").status_code == 429
    assert p.get("clinician/schedule", subject="doc").status_code == 200


def test_a_new_minute_starts_afresh(p):
    later = FixedClock(utc_now().replace(second=0, microsecond=0))
    for _ in range(5):
        ratelimit.check([("actor_read", "someone")], clock=later)
    assert not ratelimit.check([("actor_read", "someone")], clock=later).allowed
    from datetime import timedelta
    next_minute = FixedClock(later.now() + timedelta(minutes=1))
    assert ratelimit.check([("actor_read", "someone")], clock=next_minute).allowed


# ------------------------------------------------------------------ nothing personal, cleaned up
def test_no_address_or_login_is_stored(p):
    sign_in_attempt("10.7.7.7")
    p.get("me", subject="pat")
    keys = " ".join(RateWindow.objects.values_list("key", flat=True))
    assert keys and "10.7.7.7" not in keys and p.person not in keys


def test_old_windows_are_forgotten():
    from datetime import timedelta
    old = FixedClock(utc_now() - timedelta(minutes=30))
    ratelimit.check([("ip", "10.8.8.8")], clock=old)
    assert ratelimit.forget_old() == 1 and RateWindow.objects.count() == 0


# ------------------------------------------------------------------ report, off, production
def test_report_allows_and_logs_only_the_kind_of_limit(settings, caplog):
    settings.ZOIKO_RATE_LIMITING = "report"
    with caplog.at_level(logging.WARNING, logger="zoiko.ratelimit"):
        for _ in range(5):
            assert sign_in_attempt("10.6.6.6").status_code == 401
    lines = [r.getMessage() for r in caplog.records if r.name == "zoiko.ratelimit"]
    assert lines and set(lines) == {"rate_limit would_refuse kind=ip_auth"}


def test_off_counts_nothing(settings):
    settings.ZOIKO_RATE_LIMITING = "off"
    for _ in range(5):
        assert sign_in_attempt().status_code == 401
    assert RateWindow.objects.count() == 0


def test_an_unknown_mode_enforces(settings):
    settings.ZOIKO_RATE_LIMITING = "sometimes"
    for _ in range(3):
        sign_in_attempt("10.3.3.3")
    assert sign_in_attempt("10.3.3.3").status_code == 429


@pytest.mark.parametrize("value", ["report", "off", ""])
def test_a_real_server_must_enforce(value):
    assert any("ZOIKO_RATE_LIMITING" in x
               for x in release_problems(good(ZOIKO_RATE_LIMITING=value)))
    assert release_problems(good(ZOIKO_RATE_LIMITING="enforce")) == []


def test_throttled_summary_counts_exceeded_windows_without_leaking_keys(settings):
    from foundation import ratelimit

    settings.ZOIKO_RATE_LIMITING = "enforce"
    settings.RATE_LIMITS = {"ip_auth": 2}
    for _ in range(3):
        sign_in_attempt("10.9.9.9")
    summary = ratelimit.throttled_summary()
    assert summary["throttled_by_kind"]["ip_auth"] >= 1
    assert summary["active_throttles"] >= 1
    assert "10.9.9.9" not in str(summary)
