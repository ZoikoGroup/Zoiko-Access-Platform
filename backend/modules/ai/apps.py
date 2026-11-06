from django.apps import AppConfig


class AiConfig(AppConfig):
    """D18: regulated AI, OFF BY DEFAULT. Permits, tool whitelists, human approval and evidence."""

    name = "modules.ai"
    label = "ai"
