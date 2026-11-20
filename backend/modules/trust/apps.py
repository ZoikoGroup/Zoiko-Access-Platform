from django.apps import AppConfig


class TrustConfig(AppConfig):
    """D01: Trust, parties and tenancy: orgs, actors, persons, roles, delegation."""

    name = "modules.trust"
    label = "trust"

    def ready(self) -> None:
        # Hourly: a patient whose identity check has expired stops showing as verified.
        from foundation import periodic

        from .identity_jobs import REFRESH_EVERY_SECONDS, refresh_all_tenants

        periodic.register("trust.identity_expiry", REFRESH_EVERY_SECONDS, refresh_all_tenants)
        # A few times a day: a child who has come of age stops a parent's access until they decide.
        from .age_jobs import REVIEW_EVERY_SECONDS, review_all_tenants

        periodic.register("trust.age_review", REVIEW_EVERY_SECONDS, review_all_tenants)
        # `manage.py rotate_field_keys` moves this module's encrypted values onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("trust", rotate)

        # Hourly: a patient whose identity check has expired stops showing as verified.
        from foundation import periodic

        from .identity_jobs import REFRESH_EVERY_SECONDS, refresh_all_tenants

        periodic.register("trust.identity_expiry", REFRESH_EVERY_SECONDS, refresh_all_tenants)

        # Every minute: a serious incident nobody acknowledged in time goes to its deputy (DEV-202).
        from .oncall import ESCALATE_EVERY_SECONDS, escalate_all

        periodic.register("trust.incident_escalation", ESCALATE_EVERY_SECONDS, escalate_all)
