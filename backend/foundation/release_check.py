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
    # DEV-396: the master key lives in AWS KMS, never as a plain value in the environment.
    if str(getattr(conf, "FIELD_KEY_SOURCE", "env")).strip().lower() != "aws_kms":
        problems.append("the field encryption keys are not held by the key service "
                        "(ZOIKO_KEY_SOURCE must be aws_kms)")
    if _placeholder(getattr(conf, "PACK_SIGNING_KEY", "")):
        problems.append("the country-pack signing key is not set (ZOIKO_PACK_SIGNING_KEY)")
    hosts = [h.lower() for h in conf.ALLOWED_HOSTS]
    if not hosts or "*" in hosts:
        problems.append("DJANGO_ALLOWED_HOSTS is empty or allows every host")
    elif all(h in LOCAL_HOSTS for h in hosts):
        problems.append("DJANGO_ALLOWED_HOSTS lists only local addresses")
    if conf.ZOIKO_CELL_ID.upper().startswith(("DEV-", "TEST-")):
        problems.append("ZOIKO_CELL_ID is still a development or test id")
    if conf.EMAIL_HOST.lower() in DEVELOPMENT_MAIL_HOSTS or conf.EMAIL_PORT == 1025:
        problems.append("email still goes to a development inbox (EMAIL_HOST, EMAIL_PORT)")
    if getattr(conf, "ZOIKO_BUILD", "").strip().lower() in ("", "dev"):
        problems.append("ZOIKO_BUILD is still 'dev' (every audit row records which build wrote it)")
    # DEV-227: pretend partners and payments see synthetic data only; never on a real server.
    if getattr(conf, "CONNECTOR_SIMULATOR", False):
        problems.append("the pretend partner (CONNECTOR_SIMULATOR) is on")
    if getattr(conf, "BILLING_PRETEND_PROVIDER", False):
        problems.append("the pretend payment provider (BILLING_PRETEND_PROVIDER) is on")
    if getattr(conf, "BILLING_PAYMENT_PROVIDER", "") == "pretend":
        problems.append("payments are set to the pretend provider (BILLING_PAYMENT_PROVIDER)")
    # DEV-383: features a market's signed country pack has not switched on must stay off.
    if str(getattr(conf, "ZOIKO_PACK_GATING", "enforce")).strip().lower() != "enforce":
        problems.append("country-pack gating is not enforced (ZOIKO_PACK_GATING must be enforce)")
    # DEV-384: only addresses Zoiko approved may receive a customer's data.
    if str(getattr(conf, "ZOIKO_EGRESS", "enforce")).strip().lower() != "enforce":
        problems.append("outgoing addresses are not enforced (ZOIKO_EGRESS must be enforce)")
    # DEV-386: only message wording approved by safety and privacy is sent.
    if str(getattr(conf, "ZOIKO_TEMPLATE_GATING", "enforce")).strip().lower() != "enforce":
        problems.append("message templates are not enforced (ZOIKO_TEMPLATE_GATING must be "
                        "enforce)")
    # DEV-387: one caller cannot flood the API.
    if str(getattr(conf, "ZOIKO_RATE_LIMITING", "enforce")).strip().lower() != "enforce":
        problems.append("rate limits are not enforced (ZOIKO_RATE_LIMITING must be enforce)")
    # DEV-388: without the counts there is no error budget, and no release hold on it.
    if not getattr(conf, "ZOIKO_SLO_RECORDING", True):
        problems.append("request counting for the error budget is off (ZOIKO_SLO_RECORDING)")
    # DEV-389: no report number without an approved measure.
    if str(getattr(conf, "ZOIKO_MEASURE_GATING", "enforce")).strip().lower() != "enforce":
        problems.append("report measures are not enforced (ZOIKO_MEASURE_GATING must be enforce)")
    # DEV-400: somebody must be paged, and texts must really go out (or not be configured at all).
    if getattr(conf, "PAGER_PROVIDER", "") != "pagerduty" or not getattr(
            conf, "PAGERDUTY_ROUTING_KEY", ""):
        problems.append("nobody is paged for alerts (PAGER_PROVIDER=pagerduty and "
                        "PAGERDUTY_ROUTING_KEY)")
    if getattr(conf, "TEXT_PROVIDER", "") == "console":
        problems.append("text messages go to the development console (TEXT_PROVIDER)")
    if getattr(conf, "BILLING_PAYMENT_PROVIDER", "") == "stripe" and not getattr(
            conf, "STRIPE_SECRET_KEY", ""):
        problems.append("payments are set to Stripe without a key (STRIPE_SECRET_KEY)")
    small = getattr(conf, "REPORT_SMALL_CELL_K", 5)
    if not isinstance(small, int) or small < 5:
        problems.append("reports would show counts under 5 (REPORT_SMALL_CELL_K must be 5 or more)")
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
