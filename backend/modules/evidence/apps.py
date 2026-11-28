from django.apps import AppConfig


class EvidenceConfig(AppConfig):
    """D17: Analytics and evidence: audit events, disclosure log, evidence records, provenance."""

    name = "modules.evidence"
    label = "evidence"

    def ready(self) -> None:
        # Counted for the retention report (DEV-398, suggested 10 years); never removed here.
        from foundation import retention

        from .retention import count_older

        retention.register("audit_log", "The audit log", "when it happened", count_older,
                           suggested_days=10 * retention.YEAR)
