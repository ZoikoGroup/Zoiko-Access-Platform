"""Test settings. Safe fixed values so tests never depend on a developer's .env."""
import os

# Forced, not defaulted: a developer's .env must never change what the tests assert.
os.environ["DJANGO_SECRET_KEY"] = "test-only-not-a-secret"  # noqa: S105
os.environ["ZOIKO_CELL_ID"] = "TEST-01"
# A fixed throwaway key (32 bytes of "A"): tests must not depend on a developer's real key.
os.environ["ZOIKO_FIELD_KEYS"] = "t1:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="  # noqa: S105
os.environ["ZOIKO_FIELD_KEY_ID"] = "t1"
os.environ["PASSWORD_MIN_LENGTH"] = "10"  # noqa: S105 - a length, not a secret
os.environ.setdefault("POSTGRES_DB", "zoiko_test")
os.environ.setdefault("POSTGRES_USER", "zoiko")
os.environ.setdefault("POSTGRES_PASSWORD", "dev-only-change-me")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost")

from .base import *  # noqa: E402, F403

DEBUG = False
# The test database user is a superuser (the tests switch to a restricted role where it matters).
REQUIRE_RESTRICTED_DB_ROLE = False
# The test settings are all placeholders on purpose.
REQUIRE_PRODUCTION_SETTINGS = False
# The pretend partner connector (modules/connector/simulator.py). Never set in production.
CONNECTOR_SIMULATOR = True
# The pretend payment provider (modules/billing/payments.py). Payments stay switched off
# (BILLING_PAYMENT_PROVIDER = "") unless a test turns them on.
BILLING_PRETEND_PROVIDER = True

# A throwaway table for proving tenant isolation. Exists only under test settings.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.probe"]  # noqa: F405
# Demo and test customers have no signed country pack: allow, and log what would be refused
# (DEV-383). Tests that check refusals switch this to "enforce" themselves.
ZOIKO_PACK_GATING = "report"
# Test webhook receivers are not approved by anyone: send, and log (DEV-384). Tests of the
# allowlist switch this to "enforce" themselves.
ZOIKO_EGRESS = "report"
# Test wording is not approved by anyone: send, and log (DEV-386). Tests of the approval switch
# this to "enforce" themselves.
ZOIKO_TEMPLATE_GATING = "report"
# Tests make many requests quickly; the rate-limit tests switch this on themselves (DEV-387).
ZOIKO_RATE_LIMITING = "off"
# One extra write per request is not needed by most tests; the error-budget tests switch it on.
ZOIKO_SLO_RECORDING = False
# Report measures are not approved in tests: show, and log (DEV-389); their tests enforce.
ZOIKO_MEASURE_GATING = "report"
