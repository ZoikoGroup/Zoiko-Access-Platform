"""Production settings (DEV-196): the base settings, plus what a real server behind HTTPS needs.

Everything secret comes from the environment (on Google Cloud: Secret Manager, see
deploy/gcp/). With DEBUG off the app also refuses to start while any development-only setting
remains (DEV-088): `python manage.py check --deploy` lists them.
"""
from .base import *  # noqa: F403

DEBUG = False
DEV_AUTH_STUB = False

# Cloud Run ends TLS in front of the container and says so in X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]       # probes come over plain HTTP inside
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31_536_000                              # one year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Clinical notes stay off in production until the clinical safety review has signed them off
# (DEV-324, LEAD DECISION). Turning them on needs BOTH the switch and the sign-off reference
# (for example the hazard log entry), so nobody can switch them on by accident.
NOTES_SAFETY_SIGNOFF = env("ZOIKO_NOTES_SAFETY_SIGNOFF", "").strip()  # noqa: F405
if CLINICAL_NOTES_ENABLED and not NOTES_SAFETY_SIGNOFF:  # noqa: F405
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "Clinical notes need ZOIKO_NOTES_SAFETY_SIGNOFF (the clinical safety review) in production"
    )
