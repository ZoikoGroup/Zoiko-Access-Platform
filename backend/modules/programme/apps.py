from django.apps import AppConfig


class ProgrammeConfig(AppConfig):
    """Employer programme links: a one-time code ties a patient to an employer's enrolled member."""

    name = "modules.programme"
    label = "programme"

    def ready(self) -> None:
        # A patient-record merge (DEV-365): these rows never move, they are only counted.
        from foundation import person_refs

        from .models import ProgrammeLink

        person_refs.keep("programme.ProgrammeLink", ProgrammeLink)
