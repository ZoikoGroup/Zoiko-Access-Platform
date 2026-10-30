"""Settings shared by every environment.

Rules (from the architecture docs):
- Everything that differs per deployment comes from the environment, never from code.
- The same code runs in every cell. The cell is identified by ZOIKO_CELL_ID, not by `if country`.
- Secrets are never logged and never given real defaults.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if value is None or (required and value == ""):
        # Report the NAME only. Never print values: they may be secrets.
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = False
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

# Identifies this deployment. Used later to bind every request and every row to one cell.
ZOIKO_CELL_ID = env("ZOIKO_CELL_ID", required=True)
# Google sign-in is offered only when a Google OAuth client id is configured (empty = off).
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
DEV_AUTH_STUB = False  # unsigned dev login; only dev settings turn it on

# We deliberately do NOT install django.contrib.auth/admin/sessions.
# Actors, roles and delegation are our own models (trust module) and authentication is
# OIDC token validation. Django's built-in User model would be hard to replace later.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "foundation",
    "modules.trust",
    "modules.policy",
    "modules.catalog",
    "modules.scheduling",
    "modules.workflow",
    "modules.referral",
    "modules.comms",
    "modules.evidence",
]

MIDDLEWARE = [
    "foundation.middleware.RequestIdMiddleware",        # first: every request gets an id
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "foundation.middleware.ErrorHandlingMiddleware",    # last: sees view exceptions first
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", required=True),
        "USER": env("POSTGRES_USER", required=True),
        "PASSWORD": env("POSTGRES_PASSWORD", required=True),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        # Every transaction must be explicit in our service layer; see ARCHITECTURE section 5.
        "ATOMIC_REQUESTS": False,
    }
}

# Time: store everything in UTC. Original source time zones are kept in separate columns.
TIME_ZONE = "UTC"
USE_TZ = True
LANGUAGE_CODE = "en"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"  # real IDs (UUIDv7 + public_id) arrive in B1

# Logging: structured later. Rule from day one: NEVER log request bodies, tokens, or health data.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "foundation.request_context.RequestIdFilter"}},
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s"}
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "plain",
        }
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
}

# --- Email ---------------------------------------------------------------------------------
# The worker sends queued mail over SMTP. No host configured means "localhost": sending then
# fails visibly and is retried, never silently dropped. Production must set a real provider.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "1025"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", "") == "1"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("EMAIL_FROM", "Zoiko Access <no-reply@zoiko.example>")
# Where the links in emails point (the patient web app).
PATIENT_APP_URL = env("PATIENT_APP_URL", "http://localhost:5174").rstrip("/")
STAFF_APP_URL = env("STAFF_APP_URL", "http://localhost:5173").rstrip("/")
CLINICIAN_APP_URL = env("CLINICIAN_APP_URL", "http://localhost:5176").rstrip("/")

# --- Passwords ------------------------------------------------------------------------------
# Minimum length. 10 unless the deployment says otherwise, and never below 8. Only the local
# development Docker setup lowers it (so the demo logins can be short); production keeps 10.
PASSWORD_MIN_LENGTH = max(8, int(env("PASSWORD_MIN_LENGTH", "10")))
