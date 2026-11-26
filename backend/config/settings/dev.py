from .base import *  # noqa: F403

DEBUG = True
DEV_AUTH_STUB = True
# The pretend partner connector (modules/connector/simulator.py). Never set in production.
CONNECTOR_SIMULATOR = True
# The pretend payment provider (modules/billing/payments.py). Never set in production.
BILLING_PRETEND_PROVIDER = True
# Paying a bill online uses the pretend provider in development; set BILLING_PAYMENT_PROVIDER= to
# see the apps with it off.
BILLING_PAYMENT_PROVIDER = env("BILLING_PAYMENT_PROVIDER", "pretend")  # noqa: F405
WEBHOOK_ALLOW_INSECURE = True
WEBHOOK_ALLOW_PRIVATE = True
# Clinical notes are on in development (DEV-324); production keeps them off until the clinical
# safety review signs them off. Set ZOIKO_CLINICAL_NOTES=false to see the app with them off.
CLINICAL_NOTES_ENABLED = env("ZOIKO_CLINICAL_NOTES", "true").lower() == "true"  # noqa: F405
COVER_SIMULATOR = True
# Pointers to records in a clinic's own system (DEV-041) are on in development; production keeps
# them off. Set ZOIKO_CLINICAL_REFERENCES=false to see the apps with them off.
CLINICAL_REFERENCES_ENABLED = env("ZOIKO_CLINICAL_REFERENCES", "true").lower() == "true"  # noqa: F405
