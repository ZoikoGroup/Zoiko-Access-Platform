import pathlib
import threading
from types import SimpleNamespace

import pytest
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from foundation import release_check
from foundation.management.commands import relay_outbox
from foundation.release_check import (
    PLACEHOLDER_MARKERS,
    assert_release_ready,
    release_problems,
)

# The words that make a secret a placeholder, pinned here so removing one from the code is caught.
PINNED_MARKERS = ("dev-only", "replace-me", "replace-with", "change-me", "changeme", "test-only")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def good(**over):
    """Settings that are fine for a real server."""
    values = {
        "DEBUG": False, "DEV_AUTH_STUB": False, "PASSWORD_MIN_LENGTH": 12,
        "SECRET_KEY": "Zq8vN2mXk4RtP7wY1sLc9HdF3gJb6AeU5oTi0VnBxMyKrQ2z",
        "DATABASES": {"default": {"PASSWORD": "Vr7Qm2Xk9LpT4wYb8NsHd3GfJc6AeU1o"}},
        "FIELD_KEYS": "k1:3q2+7w==realkeymaterialnotaplaceholder0123456789abcdef",
        "ALLOWED_HOSTS": ["care.example.org"], "ZOIKO_CELL_ID": "EU-WEST-01",
        "EMAIL_HOST": "smtp.example.org", "EMAIL_PORT": 587,
    }
    values.update(over)
    return SimpleNamespace(**values)


def one(**over):
    return release_problems(good(**over))


# ------------------------------------------------------------------ the baseline
def test_settings_that_are_fine_for_a_real_server_have_no_problems():
    assert release_problems(good()) == []


def test_every_pinned_placeholder_word_is_still_in_the_code():
    assert set(PINNED_MARKERS) <= set(PLACEHOLDER_MARKERS)


# ------------------------------------------------------------------ each development-only thing
def test_debug_is_a_problem():
    assert any("DEBUG" in p for p in one(DEBUG=True))


def test_the_unsigned_development_sign_in_is_a_problem():
    assert any("DEV_AUTH_STUB" in p for p in one(DEV_AUTH_STUB=True))


@pytest.mark.parametrize("length,flagged", [(8, True), (9, True), (10, False), (12, False)])
def test_short_passwords_are_a_problem_below_ten_characters(length, flagged):
    found = [p for p in one(PASSWORD_MIN_LENGTH=length) if "passwords may be as short" in p]
    assert bool(found) is flagged
    if flagged:
        assert str(length) in found[0] and "10" in found[0]


@pytest.mark.parametrize("marker", PINNED_MARKERS)
@pytest.mark.parametrize("shape", ["{m}", "prefix-{m}-suffix", "PREFIX-{M}-SUFFIX"])
def test_a_placeholder_secret_is_a_problem_in_each_secret(marker, shape):
    value = "x" * 45 + shape.format(m=marker, M=marker.upper())
    assert any("DJANGO_SECRET_KEY is still a placeholder" in p for p in one(SECRET_KEY=value))
    assert any("database password" in p for p in one(
        DATABASES={"default": {"PASSWORD": value}}))
    assert any("ZOIKO_FIELD_KEYS" in p for p in one(FIELD_KEYS=value))


@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_secret_is_a_placeholder(empty):
    assert any("DJANGO_SECRET_KEY" in p for p in one(SECRET_KEY=empty or ""))
    assert any("database password" in p for p in one(DATABASES={"default": {"PASSWORD": empty}}))
    assert any("database password" in p for p in one(DATABASES={"default": {}}))
    assert any("ZOIKO_FIELD_KEYS" in p for p in one(FIELD_KEYS=empty or ""))


@pytest.mark.parametrize("length,flagged", [(39, True), (40, False), (60, False)])
def test_a_short_secret_key_is_a_problem_under_forty_characters(length, flagged):
    key = "A1b2" * 20
    found = [p for p in one(SECRET_KEY=key[:length]) if "too short" in p]
    assert bool(found) is flagged


@pytest.mark.parametrize("hosts,flagged", [
    ([], True), (["*"], True), (["care.example.org", "*"], True),
    (["localhost"], True), (["localhost", "127.0.0.1"], True), (["LOCALHOST"], True),
    (["testserver", "[::1]"], True),
    (["care.example.org"], False), (["localhost", "care.example.org"], False)])
def test_allowed_hosts_must_name_a_real_host_and_not_everything(hosts, flagged):
    found = [p for p in one(ALLOWED_HOSTS=hosts) if "ALLOWED_HOSTS" in p]
    assert bool(found) is flagged


@pytest.mark.parametrize("cell,flagged", [
    ("DEV-01", True), ("dev-01", True), ("TEST-01", True), ("test-9", True),
    ("EU-WEST-01", False), ("DEVICE-1", False), ("PRODUCTION-1", False)])
def test_a_development_or_test_cell_id_is_a_problem(cell, flagged):
    found = [p for p in one(ZOIKO_CELL_ID=cell) if "ZOIKO_CELL_ID" in p]
    assert bool(found) is flagged


@pytest.mark.parametrize("host,port,flagged", [
    ("mail", 587, True), ("localhost", 587, True), ("LOCALHOST", 587, True),
    ("127.0.0.1", 587, True), ("mailpit", 587, True), ("mailhog", 587, True),
    ("smtp.example.org", 1025, True),
    ("smtp.example.org", 587, False), ("smtp.example.org", 25, False)])
def test_email_must_not_go_to_a_development_inbox(host, port, flagged):
    found = [p for p in one(EMAIL_HOST=host, EMAIL_PORT=port) if "development inbox" in p]
    assert bool(found) is flagged


def test_every_problem_is_listed_together_not_just_the_first():
    bad = good(DEBUG=True, DEV_AUTH_STUB=True, PASSWORD_MIN_LENGTH=8, SECRET_KEY="dev-only",
               DATABASES={"default": {"PASSWORD": "change-me"}}, FIELD_KEYS="REPLACE-with-x",
               ALLOWED_HOSTS=["*"], ZOIKO_CELL_ID="DEV-01", EMAIL_HOST="mail")
    assert len(release_problems(bad)) == 9


# ------------------------------------------------------------------ the file people copy
# The placeholder values in the committed `.env.example`, pinned here (the container only sees the
# backend folder, so the file itself is checked when it is visible).
EXAMPLE = {
    "DJANGO_SECRET_KEY": "dev-only-replace-me-with-a-long-random-string",
    "POSTGRES_PASSWORD": "dev-only-change-me",
    "ZOIKO_FIELD_KEYS": "k1:REPLACE-with-base64-of-32-random-bytes",
    "ZOIKO_CELL_ID": "DEV-01",
    "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
}


def test_copying_env_example_to_a_server_and_forgetting_to_edit_it_is_caught():
    conf = good(SECRET_KEY=EXAMPLE["DJANGO_SECRET_KEY"],
                DATABASES={"default": {"PASSWORD": EXAMPLE["POSTGRES_PASSWORD"]}},
                FIELD_KEYS=EXAMPLE["ZOIKO_FIELD_KEYS"], ZOIKO_CELL_ID=EXAMPLE["ZOIKO_CELL_ID"],
                ALLOWED_HOSTS=EXAMPLE["DJANGO_ALLOWED_HOSTS"].split(","))
    found = release_problems(conf)
    for needle in ("DJANGO_SECRET_KEY", "database password", "ZOIKO_FIELD_KEYS",
                   "ZOIKO_CELL_ID", "ALLOWED_HOSTS"):
        assert any(needle in p for p in found), needle


def test_the_pinned_example_values_still_match_the_real_file_when_it_can_be_seen():
    path = ROOT.parent / ".env.example"
    if not path.exists():
        pytest.skip("the repository root is not visible from here")
    lines = path.read_text(encoding="utf-8").splitlines()
    real = dict(line.split("=", 1) for line in lines
                if line.strip() and not line.startswith("#") and "=" in line)
    for key, value in EXAMPLE.items():
        assert real[key].strip() == value, key


def test_the_settings_the_tests_run_with_would_be_refused_on_a_real_server():
    assert release_problems()                                       # test settings are placeholders


# ------------------------------------------------------------------ what starting up does
@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setattr(
        release_check, "release_problems", lambda conf=None: ["first thing", "second"])


def test_in_production_development_settings_stop_the_start_with_the_whole_list(broken, settings):
    settings.DEBUG = False
    settings.REQUIRE_PRODUCTION_SETTINGS = True
    with pytest.raises(ImproperlyConfigured) as caught:
        assert_release_ready()
    message = str(caught.value)
    assert "first thing; second" in message and "Refusing to start" in message
    assert "docs/DEPLOYMENT.md" in message


def test_in_development_nothing_is_checked(broken, settings):
    settings.DEBUG = True
    settings.REQUIRE_PRODUCTION_SETTINGS = True
    assert_release_ready()                                          # does not raise


def test_only_the_explicit_test_switch_turns_the_refusal_off(broken, settings):
    settings.DEBUG = False
    settings.REQUIRE_PRODUCTION_SETTINGS = False
    assert_release_ready()


def test_clean_settings_start_quietly_in_production(monkeypatch, settings):
    monkeypatch.setattr(release_check, "release_problems", lambda conf=None: [])
    settings.DEBUG = False
    settings.REQUIRE_PRODUCTION_SETTINGS = True
    assert_release_ready()


def test_a_real_deployment_cannot_end_up_with_the_refusal_off_by_default():
    from config.settings import base, test
    assert base.REQUIRE_PRODUCTION_SETTINGS is True and base.DEBUG is False
    assert test.REQUIRE_PRODUCTION_SETTINGS is False


# ------------------------------------------------------------------ manage.py check --deploy
def our_errors(**kw):
    found = checks.run_checks(include_deployment_checks=True, **kw)
    return [e for e in found if (e.id or "").startswith("zoiko.")]


def test_check_deploy_reports_each_problem_as_a_numbered_error(broken):
    errors = our_errors()
    assert [(e.id, e.msg) for e in errors] == [
        ("zoiko.E001", "first thing"), ("zoiko.E002", "second")]
    assert all(isinstance(e, checks.Error) for e in errors)


def test_check_deploy_is_silent_when_nothing_is_wrong(monkeypatch):
    monkeypatch.setattr(release_check, "release_problems", lambda conf=None: [])
    assert our_errors() == []


def test_the_check_runs_only_for_deploy_checks_not_every_command(broken):
    plain = [e for e in checks.run_checks() if (e.id or "").startswith("zoiko.")]
    assert plain == []


def test_the_check_command_fails_when_something_is_wrong(broken):
    from django.core.management.base import SystemCheckError
    with pytest.raises(SystemCheckError, match="zoiko.E001"):
        call_command("check", "--deploy", "--fail-level", "ERROR")


# ------------------------------------------------------------------ worker and web entry points
def test_the_worker_checks_the_settings_before_the_database_login(monkeypatch):
    order = []
    monkeypatch.setattr(relay_outbox, "assert_release_ready", lambda: order.append("settings"))
    monkeypatch.setattr(relay_outbox, "assert_safe_database_role", lambda: order.append("login"))
    relay_outbox.startup_guard()
    assert order == ["settings", "login"]


def test_a_settings_refusal_stops_the_worker_before_it_looks_at_the_login(monkeypatch):
    reached = []

    def refuse():
        raise ImproperlyConfigured("Refusing to start: placeholder secrets")

    monkeypatch.setattr(relay_outbox, "assert_release_ready", refuse)
    monkeypatch.setattr(relay_outbox, "assert_safe_database_role", lambda: reached.append("login"))
    with pytest.raises(ImproperlyConfigured):
        relay_outbox.startup_guard()
    assert reached == []


def test_the_worker_command_turns_a_settings_refusal_into_a_clear_error(monkeypatch):
    def refuse():
        raise ImproperlyConfigured("Refusing to start: placeholder secrets")

    monkeypatch.setattr(relay_outbox, "assert_release_ready", refuse)
    with pytest.raises(CommandError, match="placeholder secrets"):
        call_command("relay_outbox", "--once")


def test_the_worker_loop_runs_the_startup_guard_once_before_any_work(monkeypatch):
    seen = []
    def one_pass(*a, **k):
        seen.append("pass")
        return relay_outbox.RelayResult(0, 0, 0)

    monkeypatch.setattr(relay_outbox, "relay_once", one_pass)
    monkeypatch.setattr(relay_outbox, "send_due", lambda: None)
    monkeypatch.setattr(relay_outbox, "run_due", lambda: {})
    relay_outbox.run_loop(threading.Event(), interval=0.0, batch=10, once=True,
                          log=lambda text: None, guard=lambda: seen.append("guard"))
    assert seen == ["guard", "pass"]


def test_both_web_entry_points_run_the_settings_check_first():
    for name in ("wsgi.py", "asgi.py"):
        text = (ROOT / "config" / name).read_text(encoding="utf-8")
        assert "assert_release_ready()" in text and "assert_safe_database_role()" in text, name
        first = text.index("assert_release_ready()")
        second = text.index("assert_safe_database_role()")
        assert first < second, name
