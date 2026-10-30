"""Test settings. Safe fixed values so tests never depend on a developer's .env."""
import os

# Forced, not defaulted: a developer's .env must never change what the tests assert.
os.environ["DJANGO_SECRET_KEY"] = "test-only-not-a-secret"  # noqa: S105
os.environ["ZOIKO_CELL_ID"] = "TEST-01"
os.environ["PASSWORD_MIN_LENGTH"] = "10"  # noqa: S105 - a length, not a secret
os.environ.setdefault("POSTGRES_DB", "zoiko_test")
os.environ.setdefault("POSTGRES_USER", "zoiko")
os.environ.setdefault("POSTGRES_PASSWORD", "dev-only-change-me")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost")

from .base import *  # noqa: E402, F403

DEBUG = False

# A throwaway table for proving tenant isolation. Exists only under test settings.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.probe"]  # noqa: F405
