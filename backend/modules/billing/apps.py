from django.apps import AppConfig


class BillingConfig(AppConfig):
    """GHE-17: a clinic's fees, invoices and credit notes (records only; no money moves here)."""

    name = "modules.billing"
    label = "billing"
