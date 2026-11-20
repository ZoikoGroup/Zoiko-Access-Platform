from django.apps import AppConfig


class ClaimConfig(AppConfig):
    """Claims between a clinic and a payer: member id, service, amount and date only."""

    name = "modules.claim"
    label = "claim"
