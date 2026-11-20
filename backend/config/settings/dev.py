from .base import *  # noqa: F403

DEBUG = True
DEV_AUTH_STUB = True
# The pretend partner connector (modules/connector/simulator.py). Never set in production.
CONNECTOR_SIMULATOR = True
# The pretend payment provider (modules/billing/payments.py). Never set in production.
BILLING_PRETEND_PROVIDER = True
# Clinical notes are on in development (DEV-324); production keeps them off until the clinical
# safety review signs them off. Set ZOIKO_CLINICAL_NOTES=false to see the app with them off.
CLINICAL_NOTES_ENABLED = env("ZOIKO_CLINICAL_NOTES", "true").lower() == "true"  # noqa: F405
# Development stand-in for the virus scanner (DEV-345): finds only the EICAR test file.
ATTACHMENT_SCANNER = env("ATTACHMENT_SCANNER", "pretend")  # noqa: F405
