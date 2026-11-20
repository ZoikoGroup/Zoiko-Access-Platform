from django.apps import AppConfig


class ProgrammeConfig(AppConfig):
    """Employer programme links: a one-time code ties a patient to an employer's enrolled member."""

    name = "modules.programme"
    label = "programme"
