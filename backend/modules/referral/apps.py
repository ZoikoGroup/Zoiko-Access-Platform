from django.apps import AppConfig


class ReferralConfig(AppConfig):
    """D07: Referrals and care coordination: a doctor sends a patient on, the receiver decides."""

    name = "modules.referral"
    label = "referral"

    def ready(self) -> None:
        # The worker tells the referral inbox when a doctor leaves a site.
        from .subscribers import register_subscribers

        register_subscribers()

        # A patient-record merge (DEV-365): these rows move to the surviving record.
        from foundation import person_refs

        from .models import Referral

        person_refs.register("referral.Referral", Referral)
