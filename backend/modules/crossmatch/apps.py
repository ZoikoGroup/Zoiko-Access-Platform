from django.apps import AppConfig


class CrossmatchConfig(AppConfig):
    """Matching a patient between separate customers, by fingerprint only (DEV-363)."""

    name = "modules.crossmatch"
    label = "crossmatch"
