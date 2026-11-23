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

        # A patient-record merge (DEV-365): these rows never move, they are only counted.
        from foundation import person_refs

        from .models import ClinicalNote

        person_refs.keep("clinicalnote.ClinicalNote", ClinicalNote)
