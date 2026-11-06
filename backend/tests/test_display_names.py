import json

import pytest
from django.db import connection

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust.models import ActorName
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import started

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def name_it(p, name, subject="doc"):
    return p.post("auth/display-name", {"name": name}, subject=subject)


def providers(p):
    return {x["id"]: x["name"] for x in json.loads(p.get("staff/providers", subject="ops").content)[
        "providers"]}


def test_a_doctor_is_shown_by_login_until_they_choose_a_name(p):
    assert providers(p)[p.doc] == "doc"
    assert json.loads(p.get("me", subject="doc").content)["display_name"] == ""


def test_a_doctor_chooses_a_name_and_everyone_sees_it_instead_of_the_login(p):
    r = name_it(p, "  Dr.   Asha  Rao ")
    assert r.status_code == 200 and json.loads(r.content) == {"name": "Dr. Asha Rao"}
    assert providers(p)[p.doc] == "Dr. Asha Rao"
    assert providers(p)[p.doc2] == "doc2"                      # others unchanged
    assert json.loads(p.get("me", subject="doc").content)["display_name"] == "Dr. Asha Rao"


def test_the_patient_sees_the_doctors_name_on_the_appointment(p):
    booked(p, confirm=True)                     # the helper finds the doctor by login: book first
    name_it(p, "Dr. Asha Rao")
    rows = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    assert rows[0]["provider"]["name"] == "Dr. Asha Rao"


def test_the_team_list_shows_both_name_and_login(p):
    name_it(p, "Dr. Asha Rao")
    staff = json.loads(p.get("staff/directory", subject="ops").content)["staff"]
    doc = next(s for s in staff if s["id"] == p.doc)
    assert doc["name"] == "Dr. Asha Rao" and doc["login"] == "doc"


def test_a_name_can_be_changed_and_removed(p):
    name_it(p, "Dr. Asha Rao")
    name_it(p, "Dr. Asha Menon")
    assert providers(p)[p.doc] == "Dr. Asha Menon"
    r = p.client.delete("/api/v1/auth/display-name", **p.header("doc"))
    assert r.status_code == 200 and json.loads(r.content) == {"name": ""}
    assert providers(p)[p.doc] == "doc"
    assert p.client.delete("/api/v1/auth/display-name", **p.header("doc")).status_code == 200


@pytest.mark.parametrize("bad", [
    "", " ", "A", "x" * 81, "a@b.org", "Dr\x00Rao", "Dr\x07Rao", 5, None])
def test_a_bad_name_is_refused(p, bad):
    assert name_it(p, bad).status_code == 422
    assert providers(p)[p.doc] == "doc"


def test_patients_and_guardians_cannot_have_a_display_name(p):
    assert name_it(p, "Patient Name", subject="pat").status_code == 403


def test_it_needs_a_login(p):
    assert p.client.post("/api/v1/auth/display-name", "{}", content_type="application/json"
                         ).status_code == 401


def test_the_name_is_stored_encrypted_and_bound_to_the_person(p):
    name_it(p, "Dr. Asha Rao")
    with tenant_transaction(p.clinic.ctx):
        row = ActorName.objects.get(actor__public_id=p.doc)
        assert "Asha" not in row.name_enc
        with connection.cursor() as cur:
            cur.execute("select count(*) from trust_actorname where name_enc like %s", ["%Asha%"])
            assert cur.fetchone()[0] == 0
        # moved to another person's row, it no longer decrypts: that person is shown by login
        other = p.doc2
        ActorName.objects.create(actor=type(row.actor).objects.get(public_id=other),
                                 name_enc=row.name_enc)
    assert providers(p)[p.doc2] == "doc2"


def test_names_do_not_cross_customers(p):
    name_it(p, "Dr. Asha Rao")
    other = Practice()
    assert providers(other)[other.doc] == "doc"


def test_the_work_queue_and_messages_show_names_too(p):
    name_it(p, "Reena Desk", subject="ops")
    thread = started(p)
    p.post(f"messages/threads/{thread}/reply", {"body": "On it"}, subject="ops")
    messages = json.loads(p.get(f"messages/threads/{thread}", subject="pat").content)["thread"][
        "messages"]
    assert [m["from"] for m in messages if m["side"] == "staff"] == ["Reena Desk"]


def test_choosing_and_removing_a_name_is_audited_without_the_name(p):
    name_it(p, "Dr. Asha Rao")
    p.client.delete("/api/v1/auth/display-name", **p.header("doc"))
    with tenant_transaction(p.clinic.ctx):
        rows = list(AuditEvent.objects.filter(action__startswith="actor.display_name"))
    assert sorted(r.action for r in rows) == ["actor.display_name", "actor.display_name_clear"]
    assert "Asha" not in json.dumps([[str(v) for v in vars(r).values()] for r in rows])
