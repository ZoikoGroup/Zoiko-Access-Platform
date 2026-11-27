from django.apps import AppConfig


class AiConfig(AppConfig):
    """D18: regulated AI, OFF BY DEFAULT. Permits, tool whitelists, human approval and evidence."""

    name = "modules.ai"
    label = "ai"

    def ready(self) -> None:
        # What answers the reply draft (the fixed template, or Claude when switched on). Still
        # nothing runs without an authorized permit.
        from .providers import register_builtin

        register_builtin()
