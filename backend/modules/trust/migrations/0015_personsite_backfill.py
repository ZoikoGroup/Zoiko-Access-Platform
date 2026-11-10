"""Connect each existing patient to the clinic sites they already deal with (DEV-107), so the new
"staff see only their own site's patients" rule does not hide anyone who already belongs there."""
from django.db import migrations


def backfill(apps, schema_editor):
    Person = apps.get_model("trust", "Person")
    PersonSite = apps.get_model("trust", "PersonSite")
    Care = apps.get_model("trust", "CareRelationship")
    Booking = apps.get_model("scheduling", "Booking")
    Referral = apps.get_model("referral", "Referral")
    WaitEntry = apps.get_model("waitlist", "WaitEntry")
    Thread = apps.get_model("messaging", "Thread")

    found = set()   # (tenant_id, person public id, organization, reason)
    for b in Booking.objects.select_related("slot").iterator():
        found.add((b.tenant_id, b.person_ref, b.slot.organization_ref, "booking"))
    for r in Referral.objects.iterator():
        found.add((r.tenant_id, r.person_ref, r.destination_organization_ref, "referral"))
    for w in WaitEntry.objects.iterator():
        found.add((w.tenant_id, w.person_ref, w.organization_ref, "waitlist"))
    for t in Thread.objects.iterator():
        found.add((t.tenant_id, t.person_ref, t.organization_ref, "message"))
    for c in Care.objects.exclude(organization_ref="").select_related("person").iterator():
        found.add((c.tenant_id, c.person.public_id, c.organization_ref, "care"))

    people = {(p.tenant_id, p.public_id): p for p in Person.objects.iterator()}
    done = set()
    for tenant_id, person_ref, org, reason in sorted(found):
        person = people.get((tenant_id, person_ref))
        if person is None or not org or (person.pk, org) in done:
            continue
        done.add((person.pk, org))
        PersonSite.objects.get_or_create(
            tenant_id=tenant_id, person=person, organization_ref=org,
            defaults={"cell_id": person.cell_id, "reason": reason})


class Migration(migrations.Migration):

    dependencies = [
        ("trust", "0014_personsite"),
        ("scheduling", "0009_bookingevent_digest_version"),
        ("referral", "0001_initial"),
        ("waitlist", "0001_initial"),
        ("messaging", "0002_thread_first_reply_at"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
