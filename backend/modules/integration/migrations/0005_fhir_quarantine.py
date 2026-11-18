import django.db.models.deletion
from django.db import migrations, models

import foundation.ids
import foundation.timeutil
from foundation.rls import EnableRLS


class Migration(migrations.Migration):
    """GHE-13 T-25, T-60: a quarantine table for incoming FHIR resources that fail validation
    or reference unknown entities. Stored encrypted; creates no clinical state."""

    dependencies = [
        ("integration", "0004_partner_validation"),
    ]

    operations = [
        migrations.CreateModel(
            name="FhirQuarantine",
            fields=[
                ("id", models.UUIDField(default=foundation.ids.new_internal_id,
                                        editable=False, primary_key=True, serialize=False)),
                ("public_id", models.CharField(default=foundation.ids.new_public_id,
                                               editable=False, max_length=22, unique=True)),
                ("cell_id", models.CharField(editable=False, max_length=32)),
                ("tenant_id", models.UUIDField(editable=False)),
                ("entity_version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(default=foundation.timeutil.utc_now,
                                                    editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("delivery_id", models.CharField(max_length=64)),
                ("resource_type", models.CharField(blank=True, default="", max_length=64)),
                ("partner_ref", models.CharField(blank=True, default="", max_length=64)),
                ("body_digest", models.CharField(max_length=64)),
                ("payload_enc", models.TextField()),
                ("issues", models.JSONField(default=list)),
                ("state", models.CharField(
                    choices=[("held", "Held for staff review"),
                             ("discarded", "Discarded by staff"),
                             ("resend_requested", "Resend requested by staff")],
                    default="held", max_length=20)),
                ("decided_by_ref", models.CharField(blank=True, default="", max_length=64)),
                ("decision_note", models.CharField(blank=True, default="", max_length=300)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                             related_name="+", to="integration.partnerclient")),
            ],
            options={
                "abstract": False,
                "indexes": [
                    models.Index(fields=["cell_id", "tenant_id"], name="fhir_quar_scope_idx"),
                    models.Index(fields=["state", "created_at"], name="fhir_quar_state_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("client", "delivery_id"),
                                            name="fhir_quarantine_once_per_delivery"),
                ],
            },
        ),
        EnableRLS("integration_fhirquarantine"),
    ]
