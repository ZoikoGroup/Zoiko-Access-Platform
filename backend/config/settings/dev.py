from .base import *  # noqa: F403

DEBUG = True
DEV_AUTH_STUB = True
# The pretend partner connector (modules/connector/simulator.py). Never set in production.
CONNECTOR_SIMULATOR = True
# The pretend payment provider (modules/billing/payments.py). Never set in production.
BILLING_PRETEND_PROVIDER = True
BILLING_PAYMENT_PROVIDER = env("BILLING_PAYMENT_PROVIDER", "pretend")  # noqa: F405
# Clinical notes are on in development (DEV-324); production keeps them off until the clinical
# safety review signs them off. Set ZOIKO_CLINICAL_NOTES=false to see the app with them off.
CLINICAL_NOTES_ENABLED = env("ZOIKO_CLINICAL_NOTES", "true").lower() == "true"  # noqa: F405
# Development stand-in for the virus scanner (DEV-345): finds only the EICAR test file.
ATTACHMENT_SCANNER = env("ATTACHMENT_SCANNER", "pretend")  # noqa: F405
# Demo and test customers have no signed country pack: allow, and log what would be refused
# (DEV-383). Tests that check refusals switch this to "enforce" themselves.
ZOIKO_PACK_GATING = env("ZOIKO_PACK_GATING", "report")  # noqa: F405
# Development webhook receivers are not approved by anyone: send, and log (DEV-384).
ZOIKO_EGRESS = env("ZOIKO_EGRESS", "report")  # noqa: F405
# Nobody has approved message wording on a development machine: send, and log (DEV-386).
ZOIKO_TEMPLATE_GATING = env("ZOIKO_TEMPLATE_GATING", "report")  # noqa: F405
# The browser robots make many requests quickly from one address: report, and log (DEV-387).
ZOIKO_RATE_LIMITING = env("ZOIKO_RATE_LIMITING", "report")  # noqa: F405
# Nobody approves report measures on a development machine: show, and log (DEV-389).
ZOIKO_MEASURE_GATING = env("ZOIKO_MEASURE_GATING", "report")  # noqa: F405
