import importlib
import sys

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def test_cell_id_comes_from_environment():
    assert settings.ZOIKO_CELL_ID == "TEST-01"


def test_time_is_utc():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"


def test_django_builtin_auth_is_not_installed():
    # Actors/roles are our own models (trust module); see settings/base.py for why.
    assert "django.contrib.auth" not in settings.INSTALLED_APPS


def test_all_domain_modules_are_installed():
    expected = {"trust", "policy", "catalog", "scheduling", "workflow", "comms", "evidence"}
    installed = {app.split(".")[-1] for app in settings.INSTALLED_APPS}
    assert expected <= installed


def test_missing_secret_fails_fast_without_leaking_values(monkeypatch):
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    sys.modules.pop("config.settings.base", None)
    with pytest.raises(ImproperlyConfigured) as excinfo:
        importlib.import_module("config.settings.base")
    assert "DJANGO_SECRET_KEY" in str(excinfo.value)
