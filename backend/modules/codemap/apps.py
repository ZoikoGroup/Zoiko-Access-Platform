from django.apps import AppConfig


class CodemapConfig(AppConfig):
    """Partners' local codes, mapped to SNOMED CT by two people; never guessed."""

    name = "modules.codemap"
    label = "codemap"
