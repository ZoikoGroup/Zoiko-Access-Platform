from django.apps import AppConfig


class ClaimConfig(AppConfig):
    """Claims between a clinic and a payer: member id, service, amount and date only."""

    name = "modules.claim"
    label = "claim"

    def ready(self) -> None:
        # A patient-record merge (DEV-365): these rows never move, they are only counted.
        from foundation import person_refs

        from .models import Claim

        person_refs.keep("claim.Claim", Claim)
