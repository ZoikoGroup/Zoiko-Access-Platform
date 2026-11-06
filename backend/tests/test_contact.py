import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_attention import booked
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def contact(p, phone="+91 98765 43210", address="12 Park Road", subject="ops", location=None):
    return p.post(f"locations/{location or p.clinic.location}/contact",
                  {"phone": phone, "address": address}, subject=subject)


def location_of(p, subject="ops"):
    rows = json.loads(p.get("locations", subject=subject).content)["locations"]
    return next(r for r in rows if r["id"] == p.clinic.location)


def test_a_new_site_has_no_phone_until_staff_add_one(p):
    assert location_of(p)["phone"] == ""


def test_staff_set_the_phone_and_address_of_a_site(p):
    r = contact(p)
    assert r.status_code == 200, r.content
    got = json.loads(r.content)["location"]
    assert got["phone"] == "+91 98765 43210" and got["address"] == "12 Park Road"
    assert location_of(p)["phone"] == "+91 98765 43210"


def test_a_site_can_be_created_with_a_phone(p):
    made = p.post("locations", {"organization": p.clinic.org, "name": "North Wing",
                                "time_zone": "Asia/Kolkata", "phone": "(011) 4000-1234"},
                  subject="ops")
    assert made.status_code == 201, made.content
    assert json.loads(made.content)["location"]["phone"] == "(011) 4000-1234"


def test_an_empty_phone_removes_the_number(p):
    contact(p)
    contact(p, phone="")
    assert location_of(p)["phone"] == ""


@pytest.mark.parametrize("phone", [
    "call me", "12", "(1) 2", "---1234", "+", "abc123", "123456; DROP", "1" * 30,
    "<script>1234</script>", "+91 98765\n43210"])
def test_a_phone_that_is_not_a_phone_number_is_refused(p, phone):
    assert contact(p, phone=phone).status_code == 422


def test_the_patient_sees_the_phone_on_their_appointment(p):
    contact(p)
    booked(p, confirm=True)
    rows = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    assert rows[0]["location"]["phone"] == "+91 98765 43210"


def test_patients_and_doctors_cannot_change_a_sites_contact_details(p):
    for who in ("pat", "doc"):
        assert contact(p, subject=who).status_code == 403
    assert location_of(p)["phone"] == ""


def test_another_site_of_the_same_customer_cannot_change_it(p):
    with tenant_transaction(p.clinic.ctx):
        site = trust.create_organization(name="Other Site").public_id
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="desk2")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=site,
                          role_code="R04", by="system")
    assert contact(p, subject="desk2").status_code == 403
    assert location_of(p)["phone"] == ""


def test_another_customer_cannot_change_it(p):
    other = Practice()
    r = other.post(f"locations/{p.clinic.location}/contact", {"phone": "+91 98765 43210"},
                   subject="ops")
    assert r.status_code == 404
    assert location_of(p)["phone"] == ""


def test_the_change_is_written_to_the_audit_trail(p):
    contact(p)
    with tenant_transaction(p.clinic.ctx):
        actions = list(AuditEvent.objects.filter(object_ref=p.clinic.location).values_list(
            "action", flat=True))
    assert "location.contact" in actions
