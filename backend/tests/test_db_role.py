import logging
import threading

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection, transaction

from foundation import dbrole
from foundation.management.commands import relay_outbox

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


class Cursor:
    """A stand-in for a database cursor that answers the one question the check asks."""

    def __init__(self, row):
        self.row, self.asked = row, []

    def execute(self, sql, params=None):
        self.asked.append(sql)

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("row,expected", [
    ((False, False), []),
    ((True, False), ["is a superuser"]),
    ((False, True), ["has the BYPASSRLS attribute"]),
    ((True, True), ["is a superuser", "has the BYPASSRLS attribute"]),
    (None, ["could not be looked up"]),
])
def test_the_check_names_each_way_a_login_can_skip_row_level_security(row, expected):
    assert dbrole.role_problems(Cursor(row)) == expected


def test_the_check_asks_about_the_login_in_force_not_the_login_that_connected():
    cursor = Cursor((False, False))
    dbrole.role_problems(cursor)
    assert "current_user" in cursor.asked[0] and "session_user" not in cursor.asked[0]


# ------------------------------------------------------------------ against the real database
def test_a_restricted_role_passes_and_the_test_superuser_does_not():
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = session_user")
        superuser, bypass = cursor.fetchone()
    with connection.cursor() as cursor:
        assert bool(dbrole.role_problems(cursor)) == bool(superuser or bypass)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE zoiko_app")             # the restricted application role
        assert dbrole.role_problems(cursor) == []


# ------------------------------------------------------------------ what starting up does
@pytest.fixture
def bad_login(monkeypatch):
    monkeypatch.setattr(dbrole, "role_problems", lambda cursor: ["is a superuser"])


@pytest.fixture
def good_login(monkeypatch):
    monkeypatch.setattr(dbrole, "role_problems", lambda cursor: [])


def test_in_production_a_login_that_skips_row_level_security_stops_the_start(bad_login, settings):
    settings.DEBUG = False
    settings.REQUIRE_RESTRICTED_DB_ROLE = True
    with pytest.raises(ImproperlyConfigured) as caught:
        dbrole.assert_safe_database_role()
    message = str(caught.value)
    assert "login is a superuser" in message and "row-level security" in message
    assert "owner role" in message


def test_the_message_lists_every_problem(monkeypatch, settings):
    settings.DEBUG = False
    settings.REQUIRE_RESTRICTED_DB_ROLE = True
    monkeypatch.setattr(dbrole, "role_problems",
                        lambda cursor: ["is a superuser", "has the BYPASSRLS attribute"])
    with pytest.raises(ImproperlyConfigured, match="is a superuser and has the BYPASSRLS"):
        dbrole.assert_safe_database_role()


def test_a_safe_login_starts_quietly_in_production(good_login, settings, caplog):
    settings.DEBUG = False
    settings.REQUIRE_RESTRICTED_DB_ROLE = True
    with caplog.at_level(logging.DEBUG, logger="zoiko.dbrole"):
        dbrole.assert_safe_database_role()
    assert caplog.records == []


def test_in_development_it_only_warns(bad_login, settings, caplog):
    settings.DEBUG = True
    settings.REQUIRE_RESTRICTED_DB_ROLE = True
    with caplog.at_level(logging.WARNING, logger="zoiko.dbrole"):
        dbrole.assert_safe_database_role()                          # does not raise
    assert len(caplog.records) == 1 and "superuser" in caplog.records[0].getMessage()
    assert "development" in caplog.records[0].getMessage()


def test_only_the_explicit_test_setting_turns_the_refusal_off(bad_login, settings, caplog):
    settings.DEBUG = False
    settings.REQUIRE_RESTRICTED_DB_ROLE = False
    with caplog.at_level(logging.WARNING, logger="zoiko.dbrole"):
        dbrole.assert_safe_database_role()
    assert len(caplog.records) == 1


def test_a_real_deployment_cannot_end_up_with_the_refusal_off_by_default():
    from config.settings import base
    assert base.REQUIRE_RESTRICTED_DB_ROLE is True and base.DEBUG is False


def test_only_the_test_settings_switch_it_off():
    from config.settings import dev, test
    assert dev.REQUIRE_RESTRICTED_DB_ROLE is True       # dev is kept safe by DEBUG, not by this
    assert test.REQUIRE_RESTRICTED_DB_ROLE is False


# ------------------------------------------------------------------ the worker
def test_the_worker_does_not_start_when_the_guard_refuses(monkeypatch):
    reached = []
    monkeypatch.setattr(relay_outbox, "relay_once", lambda *a, **k: reached.append(1))

    def refuse():
        raise ImproperlyConfigured("Refusing to start")

    with pytest.raises(ImproperlyConfigured):
        relay_outbox.run_loop(threading.Event(), interval=1.0, batch=10, once=True,
                              log=lambda text: None, guard=refuse)
    assert reached == []                                           # it never touched an event


def test_the_worker_command_turns_the_refusal_into_a_clear_error(monkeypatch):
    def refuse():
        raise ImproperlyConfigured("Refusing to start: the database login is a superuser")

    monkeypatch.setattr(relay_outbox, "assert_safe_database_role", refuse)
    with pytest.raises(CommandError, match="Refusing to start"):
        call_command("relay_outbox", "--once")


def test_the_guard_runs_once_and_before_the_first_pass(monkeypatch):
    order = []
    def one_pass(*a, **k):
        order.append("pass")
        return relay_outbox.RelayResult(0, 0, 0)

    monkeypatch.setattr(relay_outbox, "relay_once", one_pass)
    monkeypatch.setattr(relay_outbox, "send_due", lambda: None)
    monkeypatch.setattr(relay_outbox, "run_due", lambda: {})
    stop = threading.Event()
    passes = iter([False, False, True])
    stop.wait = lambda timeout=None: next(passes)                  # type: ignore[method-assign]
    relay_outbox.run_loop(stop, interval=0.0, batch=10, once=False, log=lambda text: None,
                          guard=lambda: order.append("guard"))
    assert order == ["guard", "pass", "pass", "pass"]


def test_a_database_outage_at_start_is_retried_not_treated_as_a_refusal(monkeypatch):
    calls = []

    def flaky_guard():
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("not ready yet")

    monkeypatch.setattr(relay_outbox, "relay_once",
                        lambda *a, **k: relay_outbox.RelayResult(0, 0, 0))
    monkeypatch.setattr(relay_outbox, "send_due", lambda: None)
    monkeypatch.setattr(relay_outbox, "run_due", lambda: {})
    stop = threading.Event()
    results = iter([False, True])
    stop.wait = lambda timeout=None: next(results)                 # type: ignore[method-assign]
    relay_outbox.run_loop(stop, interval=0.0, batch=10, once=False, log=lambda text: None,
                          guard=flaky_guard)
    assert len(calls) == 2                                         # asked again after the outage


def test_the_web_entry_points_run_the_guard():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "config"
    for name in ("wsgi.py", "asgi.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "assert_safe_database_role()" in text, name
