import json

import pytest
from django.db import connection

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import PersonProfile
from tests.conftest import publish_service
from tests.test_api import Web, book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):   # staff deal only with their own site's patients
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def save(web, person, subject="pat", name="Asha Rao", birth="1990-04-12", phone="+91 98765 43210"):
    return web.post(f"people/{person}/profile",
                    {"name": name, "birth_date": birth, "phone": phone}, subject=subject)


def profiles(web, subject="pat"):
    r = web.get("profile", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)["profiles"]


def stored_columns():
    """What the database actually holds for every profile."""
    with connection.cursor() as cur:
        cur.execute("select legal_name_enc, birth_date_enc, phone_enc from trust_personprofile")
        return [v for row in cur.fetchall() for v in row]


# ------------------------------------------------------------------ the patient's own details
def test_a_patient_saves_and_reads_back_their_own_details(p):
    r = save(p, p.person)
    assert r.status_code == 200
    assert json.loads(r.content)["profile"]["name"] == "Asha Rao"
    mine = profiles(p)
    assert len(mine) == 1 and mine[0]["person"] == p.person and mine[0]["editable"] is True
    assert (mine[0]["name"], mine[0]["birth_date"], mine[0]["phone"]) == (
        "Asha Rao", "1990-04-12", "+91 98765 43210")


def test_the_database_holds_only_ciphertext(p):
    save(p, p.person)
    cells = [c for c in stored_columns() if c]
    assert len(cells) == 3 and all(c.startswith("zf1:") for c in cells)
    blob = " ".join(cells)
    assert "Asha" not in blob and "1990" not in blob and "98765" not in blob


def test_saving_again_replaces_the_details_and_can_clear_the_optional_ones(p):
    save(p, p.person)
    save(p, p.person, name="Asha Rao-Menon", birth="", phone="")
    row = profiles(p)[0]
    assert (row["name"], row["birth_date"], row["phone"]) == ("Asha Rao-Menon", "", "")


def test_a_patient_with_no_details_yet_sees_blanks(p):
    row = profiles(p)[0]
    assert row["name"] == "" and row["editable"] is True


def test_the_audit_entry_names_who_changed_it_but_never_what_it_says(p):
    save(p, p.person)
    with tenant_transaction(p.clinic.ctx):
        events = list(AuditEvent.objects.filter(action="person.profile_update"))
    assert len(events) == 1 and events[0].actor_ref == p.patient_actor
    assert "Asha" not in str(events[0].details) and "1990" not in str(events[0].details)


# ------------------------------------------------------------------ input rules
@pytest.mark.parametrize("fields", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 121}, {"name": "Bad\x00Name"},
    {"birth": "12/04/1990"}, {"birth": "1850-01-01"}, {"birth": "2999-01-01"},
    {"phone": "abc"}, {"phone": "12"}, {"phone": "+" + "9" * 30},
])
def test_bad_details_are_refused_and_nothing_is_stored(p, fields):
    assert save(p, p.person, **fields).status_code == 422
    assert [c for c in stored_columns() if c] == []


def test_extra_spaces_in_a_name_are_tidied(p):
    save(p, p.person, name="  Asha    Rao  ")
    assert profiles(p)[0]["name"] == "Asha Rao"


# ------------------------------------------------------------------ who may write
def test_nobody_can_edit_another_patients_details(p):
    other_actor, other_person = p.patient("pat2")
    assert other_actor
    assert save(p, other_person, subject="pat").status_code == 403
    assert save(p, p.person, subject="pat2").status_code == 403
    assert [c for c in stored_columns() if c] == []


def test_a_doctor_cannot_write_details_and_a_receptionist_can(p):
    assert save(p, p.person, subject="doc").status_code == 403
    assert save(p, p.person, subject="ops").status_code == 200
    assert profiles(p)[0]["name"] == "Asha Rao"


def test_another_customers_staff_cannot_touch_it(p):
    outsider = Web()
    outsider.staff("desk")
    # Another customer's staff are refused outright: this patient has no link to their site.
    assert save(outsider, p.person, subject="desk").status_code == 403
    assert [c for c in stored_columns() if c] == []


# ------------------------------------------------------------------ who sees the name
def test_staff_see_the_patient_name_in_the_registry_and_in_appointments(p):
    save(p, p.person)
    registry = json.loads(p.get("staff/patients", subject="ops").content)["patients"]
    assert [x["patient_name"] for x in registry if x["id"] == p.person] == ["Asha Rao"]
    p.add_times(p.doc)
    slot = p.slot_of("doc")
    book(p, slot, subject="pat")
    rows = json.loads(p.get("bookings", subject="ops").content)["bookings"]
    assert rows and all(r["patient_name"] == "Asha Rao" for r in rows)


def test_a_patient_without_details_shows_no_name_and_the_app_falls_back_to_the_id(p):
    registry = json.loads(p.get("staff/patients", subject="ops").content)["patients"]
    assert all(x["patient_name"] is None for x in registry)


def test_a_doctor_sees_the_name_only_for_patients_they_have_a_relationship_with(p):
    save(p, p.person)
    p.add_times(p.doc)
    booking = json.loads(book(p, p.slot_of("doc"), subject="pat").content)["booking"]["id"]
    assert p.my_patients() == []                                   # no link yet: no name either
    p.post(f"bookings/{booking}/confirm", subject="ops")
    assert [x["patient_name"] for x in p.my_patients()] == ["Asha Rao"]
    assert [a["patient_name"] for a in p.schedule()] == ["Asha Rao"]
    assert p.my_patients("doc2") == []                             # another doctor: nothing


def test_referrals_and_the_waiting_list_show_the_name_to_those_allowed(p):
    save(p, p.person)
    p.add_times(p.doc)
    booking = json.loads(book(p, p.slot_of("doc"), subject="pat").content)["booking"]["id"]
    p.post(f"bookings/{booking}/confirm", subject="ops")
    made = p.post("services", {"organization": p.clinic.org, "name": "Cardiology",
                               "category": "cardiology", "duration_minutes": 30}, subject="ops")
    cardio = json.loads(made.content)["service"]["id"]
    publish_service(p.clinic.ctx, cardio)
    sent = p.post("clinician/referrals", {"patient": p.person, "service": cardio,
                                          "reason": "Chest pain", "urgency": "routine"},
                  subject="doc")
    assert sent.status_code == 201
    assert json.loads(sent.content)["referral"]["patient_name"] == "Asha Rao"
    incoming = json.loads(p.get("referrals/incoming", subject="ops").content)["referrals"]
    assert incoming[0]["patient_name"] == "Asha Rao"
    assert p.post("waitlist", {"service": cardio}, subject="pat").status_code == 201
    queue = json.loads(p.get("staff/waitlist", subject="ops").content)["entries"]
    assert queue[0]["patient_name"] == "Asha Rao"


# ------------------------------------------------------------------ tampering and isolation
def test_a_name_copied_onto_another_patient_is_not_shown(p):
    """The encrypted name is bound to its row: pasting it into someone else's row shows nothing."""
    _, other = p.patient("pat2")
    with tenant_transaction(p.clinic.ctx):
        trust.link_person_to_site(other, p.clinic.org, "booking")
    save(p, p.person)
    save(p, other, subject="ops", name="Ravi Kumar")
    with tenant_transaction(p.clinic.ctx):
        a = PersonProfile.objects.get(person__public_id=p.person)
        b = PersonProfile.objects.get(person__public_id=other)
        b.legal_name_enc = a.legal_name_enc                        # a stolen ciphertext
        b.save(update_fields=["legal_name_enc"])
    registry = json.loads(p.get("staff/patients", subject="ops").content)["patients"]
    shown = {x["id"]: x["patient_name"] for x in registry}
    assert shown[p.person] == "Asha Rao" and shown[other] is None
