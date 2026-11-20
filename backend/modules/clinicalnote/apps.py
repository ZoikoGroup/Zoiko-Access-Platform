from django.apps import AppConfig


class ClinicalNoteConfig(AppConfig):
    """Consultation notes: encrypted text, signed, never edited, corrected by a new version."""

    name = "modules.clinicalnote"
    label = "clinicalnote"

    def ready(self) -> None:
        # Once a day the worker erases note text past its NHS retention period (DEV-324).
        from foundation import periodic

        from .jobs import RETENTION_EVERY_SECONDS, erase_expired_all_tenants

        periodic.register(
            "clinicalnote.retention", RETENTION_EVERY_SECONDS, erase_expired_all_tenants
        )
