from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('waitlist', '0002_priority_and_rules'),
    ]

    operations = [
        migrations.RemoveConstraint(model_name='waitrule', name='waitrule_one_per_clinic'),
        migrations.AddField(
            model_name='waitrule',
            name='service_ref',
            field=models.CharField(blank=True, default='', max_length=22),
        ),
        migrations.AddConstraint(
            model_name='waitrule',
            constraint=models.UniqueConstraint(
                fields=('tenant_id', 'organization_ref', 'service_ref'),
                name='waitrule_one_per_clinic_and_service'),
        ),
    ]
