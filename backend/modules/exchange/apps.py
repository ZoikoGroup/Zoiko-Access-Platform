from django.apps import AppConfig


class ExchangeConfig(AppConfig):
    """The status exchange between separate customers (references and status only)."""

    name = "modules.exchange"
    label = "exchange"
