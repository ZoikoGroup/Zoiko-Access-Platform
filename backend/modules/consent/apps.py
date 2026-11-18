from django.apps import AppConfig


class ConsentConfig(AppConfig):
    """A patient's choices about who may see what: purpose by purpose, with an end date if they want
    one, and a history that cannot be changed."""

    name = "modules.consent"
    label = "consent"
