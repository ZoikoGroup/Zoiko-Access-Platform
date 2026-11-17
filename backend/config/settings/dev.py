from .base import *  # noqa: F403

DEBUG = True
DEV_AUTH_STUB = True
# The pretend partner connector (modules/connector/simulator.py). Never set in production.
CONNECTOR_SIMULATOR = True
# The pretend payment provider (modules/billing/payments.py). Never set in production.
BILLING_PRETEND_PROVIDER = True
