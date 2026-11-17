from django.apps import AppConfig


class ClinicalNoteConfig(AppConfig):
    """Consultation notes: encrypted text, signed, never edited, corrected by a new version."""

    name = "modules.clinicalnote"
    label = "clinicalnote"
