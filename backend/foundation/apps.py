from django.apps import AppConfig


class FoundationConfig(AppConfig):
    """Shared plumbing: ids, time, errors, outbox, inbox, idempotency (built in B1-B2).

    Named `foundation`, not `platform`, because a top-level `platform` package would shadow
    Python's standard-library `platform` module and break imports.
    """

    name = "foundation"
    label = "foundation"
