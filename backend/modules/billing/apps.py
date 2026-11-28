from django.apps import AppConfig


class BillingConfig(AppConfig):
    """GHE-17: a clinic's fees, invoices and credit notes (records only; no money moves here)."""

    name = "modules.billing"
    label = "billing"

    def ready(self) -> None:
        from django.conf import settings

        from . import payments

        # The pretend payment provider exists only where the settings allow it (development and
        # tests). No real provider has been chosen, so production registers none.
        if getattr(settings, "BILLING_PRETEND_PROVIDER", False):
            payments.register(payments.pretend_provider)
        # DEV-400 (decided 27 Nov 2026): Stripe, when this deployment chooses it.
        from django.conf import settings as conf

        if getattr(conf, "BILLING_PAYMENT_PROVIDER", "") == "stripe":
            from .stripe_provider import stripe_provider

            payments.register(stripe_provider)

        # A patient-record merge (DEV-365): these rows never move, they are only counted.
        from foundation import person_refs

        from .models import Invoice

        person_refs.keep("billing.Invoice", Invoice)
