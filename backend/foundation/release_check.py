"""Development-only settings must never reach a real server.

The development setup runs with an unsigned sign-in, short passwords, placeholder secrets and a
catch-all development inbox. Each is fine on a developer's computer and dangerous anywhere else.
This is the one list of them:

* `release_problems()` returns what is wrong, in plain words.
* `manage.py check --deploy` reports each as an error.
* The web server and the worker call `assert_release_ready()` as they start: with DEBUG off they
  refuse to start while any problem remains. With DEBUG on (development) nothing is checked.

The placeholder markers match the values in `.env.example`, so copying that file to a server and
forgetting to edit it is caught.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Error, Tags, register
from django.core.exceptions import ImproperlyConfigured

PLACEHOLDER_MARKERS = (
    "dev-only", "replace-me", "replace-with", "change-me", "changeme", "test-only")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1", "testserver"})
DEVELOPMENT_MAIL_HOSTS = frozenset({"localhost", "127.0.0.1", "mail", "mailpit", "mailhog"})
MIN_SECRET_KEY_LENGTH = 40
MIN_PASSWORD_LENGTH = 10


def _placeholder(value: str) -> bool:
    lowered = (value or "").lower()
    return not lowered or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def release_problems(conf: Any = settings) -> list[str]:
    """What stops these settings being used on a real server. Empty means nothing found."""
    problems: list[str] = []
    if conf.DEBUG:
        problems.append("DEBUG is on (debug pages show internals)")
    if conf.DEV_AUTH_STUB:
        problems.append("the unsigned development sign-in (DEV_AUTH_STUB) is on")
    if conf.PASSWORD_MIN_LENGTH < MIN_PASSWORD_LENGTH:
        problems.append(f"passwords may be as short as {conf.PASSWORD_MIN_LENGTH} characters "
                        f"(a real server needs at least {MIN_PASSWORD_LENGTH})")
    if _placeholder(conf.SECRET_KEY):
        problems.append("DJANGO_SECRET_KEY is still a placeholder")
    elif len(conf.SECRET_KEY) < MIN_SECRET_KEY_LENGTH:
        problems.append(
            f"DJANGO_SECRET_KEY is too short (under {MIN_SECRET_KEY_LENGTH} characters)")
    if _placeholder(str(conf.DATABASES["default"].get("PASSWORD") or "")):
        problems.append("the database password is still a placeholder (POSTGRES_PASSWORD)")
    if _placeholder(conf.FIELD_KEYS):
        problems.append("the field encryption keys are still a placeholder (ZOIKO_FIELD_KEYS)")
    hosts = [h.lower() for h in conf.ALLOWED_HOSTS]
    if not hosts or "*" in hosts:
        problems.append("DJANGO_ALLOWED_HOSTS is empty or allows every host")
    elif all(h in LOCAL_HOSTS for h in hosts):
        problems.append("DJANGO_ALLOWED_HOSTS lists only local addresses")
    if conf.ZOIKO_CELL_ID.upper().startswith(("DEV-", "TEST-")):
        problems.append("ZOIKO_CELL_ID is still a development or test id")
    if conf.EMAIL_HOST.lower() in DEVELOPMENT_MAIL_HOSTS or conf.EMAIL_PORT == 1025:
        problems.append("email still goes to a development inbox (EMAIL_HOST, EMAIL_PORT)")
    return problems


def assert_release_ready() -> None:
    """Call once when a web server or the worker starts. With DEBUG on, or with the test-only switch
    REQUIRE_PRODUCTION_SETTINGS off, nothing is checked. Otherwise refuse to start while any
    development-only setting remains."""
    if settings.DEBUG or not settings.REQUIRE_PRODUCTION_SETTINGS:
        return
    problems = release_problems()
    if problems:
        raise ImproperlyConfigured(
            "Refusing to start: these development-only settings are still in use: "
            + "; ".join(problems) + ". Set real values (see docs/DEPLOYMENT.md).")


@register(Tags.security, deploy=True)
def release_check(app_configs: Any, **kwargs: Any) -> list[Error]:
    """`manage.py check --deploy`: each problem is an error."""
    return [Error(problem, id=f"zoiko.E{n:03d}") for n, problem in enumerate(release_problems(), 1)]
