"""The production settings (DEV-196) turn development off and the HTTPS protections on."""
import importlib
import re


def test_production_settings_switch_development_off_and_protections_on():
    prod = importlib.import_module("config.settings.production")
    assert prod.DEBUG is False and prod.DEV_AUTH_STUB is False
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SESSION_COOKIE_SECURE is True and prod.CSRF_COOKIE_SECURE is True
    assert prod.SECURE_HSTS_SECONDS >= 31_536_000 and prod.X_FRAME_OPTIONS == "DENY"
    # the secrets redaction filter of DEV-186 is inherited, not lost
    assert "secrets" in prod.LOGGING["handlers"]["console"]["filters"]


def test_the_frame_header_is_actually_sent_in_production():
    """X_FRAME_OPTIONS does nothing without its middleware (A7); the base list stays as it is."""
    prod = importlib.import_module("config.settings.production")
    base = importlib.import_module("config.settings.base")
    xfo = "django.middleware.clickjacking.XFrameOptionsMiddleware"
    security = "django.middleware.security.SecurityMiddleware"
    assert prod.MIDDLEWARE.index(xfo) == prod.MIDDLEWARE.index(security) + 1
    assert xfo not in base.MIDDLEWARE


def test_the_health_probes_are_not_redirected_to_https():
    prod = importlib.import_module("config.settings.production")
    for path in ("healthz", "readyz"):
        assert any(re.match(rule, path) for rule in prod.SECURE_REDIRECT_EXEMPT)
