from django.apps import AppConfig


class ProbeConfig(AppConfig):
    """Test-only app: a tiny tenant-scoped table used to prove isolation really works."""

    name = "tests.probe"
    label = "probe"
