from django.apps import AppConfig


class ClinicalrefConfig(AppConfig):
    """Pointers to clinical records kept in a clinic's OWN system (DEV-041, decision 3).
    Switched off unless CLINICAL_REFERENCES_ENABLED is True. No clinical content is ever stored."""

    name = "modules.clinicalref"
    label = "clinicalref"

    def ready(self) -> None:
        from foundation import retention

        from .retention import count_older

        retention.register("clinical_references", "Pointers to clinical records",
                           "the day the record is about", count_older)
