"""Clinical references (DEV-041, decision 3): a pointer to a record kept in a clinic's own system,
never the record. Off unless CLINICAL_REFERENCES_ENABLED; when on, only a treating doctor adds or
withdraws, the patient sees their own without the record's id, and nothing is ever deleted."""
import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

GOOD = {"system": "emis-web", "external_id": "CN-2026-00417", "kind": "consultation_note",
        "refers_to": "2026-11-02"}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def on(settings):
    settings.CLINICAL_REFERENCES_ENABLED = True


@pytest.fixture
def p():
    """A clinic where `doc` treats the patient and `doc2` does not."""
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=practice.doc, person_public_id=practice.person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=practice.clinic.org, by="system")
    return practice


def add(p, body=None, subject="doc", person=None):
    return p.post(f"clinician/patients/{person or p.person}/references", body or GOOD,
                  subject=subject)


def audit(p, action):
    with tenant_transaction(p.clinic.ctx):
        return list(AuditEvent.objects.filter(action=action))


# ------------------------------------------------------------------ switched off
def test_switched_off_by_default_every_endpoint_says_not_found(p, settings):
    assert settings.CLINICAL_REFERENCES_ENABLED is False
    calls = [
        add(p),
        p.get(f"clinician/patients/{p.person}/references", subject="doc"),
        p.post("clinician/references/AAAAAAAAAAAAAAAAAAAAAA/withdraw",
               {"reason": "other"}, subject="doc"),
        p.get("my/references", subject="pat"),
        add(p, subject="pat"),                  # the same answer for anyone
    ]
    assert [r.status_code for r in calls] == [404] * 5
    assert {r.json()["code"] for r in calls} == {"NOT_FOUND"}
    assert audit(p, "clinicalref.added") == []


def test_switched_off_the_module_itself_refuses(p):
    from foundation.errors import ZoikoError
    from modules.clinicalref import api as clinicalref
    with tenant_transaction(p.clinic.ctx), pytest.raises(ZoikoError) as refused:
        clinicalref.list_for_person(p.person, viewer_ref=p.doc, care_team=True)
    assert refused.value.code == "NOT_FOUND"


# ------------------------------------------------------------------ switched on
def test_a_treating_doctor_adds_and_withdraws_and_the_row_stays(p, on):
    made = add(p)
    assert made.status_code == 201, made.content
    ref = made.json()["reference"]
    assert ref["external_id"] == "CN-2026-00417" and ref["author"] == p.doc
    assert ref["organization"] == {"id": p.clinic.org, "name": "North"}
    assert ref["status"] == "active" and ref["kind"] == "consultation_note"
    assert add(p).status_code == 409                       # the same pointer twice

    done = p.post(f"clinician/references/{ref['id']}/withdraw", {"reason": "entered_in_error"},
                  subject="doc")
    assert done.status_code == 200, done.content
    assert done.json()["reference"]["status"] == "withdrawn"
    again = p.post(f"clinician/references/{ref['id']}/withdraw", {"reason": "other"},
                   subject="doc")
    assert again.status_code == 409

    team = p.get(f"clinician/patients/{p.person}/references", subject="doc").json()["references"]
    assert [(r["id"], r["status"], r["withdrawn_reason"]) for r in team] == [
        (ref["id"], "withdrawn", "entered_in_error")]
    mine = p.get("my/references", subject="pat").json()["references"]
    assert [(r["id"], r["status"]) for r in mine] == [(ref["id"], "withdrawn")]
    assert add(p).status_code == 201                       # withdrawn: may be pointed at again


def test_only_a_treating_doctor_may_add(p, on):
    other_customer = Practice()
    other_customer.login("doc9", trust.ActorType.CLINICIAN, "R03")
    assert add(p, subject="doc2").status_code == 403       # a doctor without a care relationship
    assert add(p, subject="ops").status_code == 403        # clinic staff
    assert add(p, subject="pat").status_code == 403        # the patient
    assert add(other_customer, subject="doc9", person=p.person).status_code == 403
    for subject in ("doc2", "ops", "pat"):
        r = p.get(f"clinician/patients/{p.person}/references", subject=subject)
        assert r.status_code == 403, subject
    assert audit(p, "clinicalref.added") == []


def test_reading_for_continuity_of_care_cannot_add(p, on):
    r = p.post(f"clinician/patients/{p.person}/references", GOOD, subject="doc",
               HTTP_X_ZOIKO_PURPOSE="CONTINUITY_OF_CARE")
    assert r.status_code == 403
    got = p.get(f"clinician/patients/{p.person}/references", subject="doc",
                HTTP_X_ZOIKO_PURPOSE="CONTINUITY_OF_CARE")
    assert got.status_code == 200


def test_a_doctor_who_does_not_treat_the_patient_cannot_withdraw(p, on):
    ref = add(p).json()["reference"]
    path = f"clinician/references/{ref['id']}/withdraw"
    assert p.post(path, {"reason": "other"}, subject="doc2").status_code == 403
    assert p.post(path, {"reason": "other"}, subject="ops").status_code == 403
    assert p.post(path, {"reason": "other"}, subject="pat").status_code == 403
    assert p.post("clinician/references/nope/withdraw", {"reason": "other"},
                  subject="doc").status_code == 404
    assert p.post(path, {"reason": "because I said so"}, subject="doc").status_code == 422


def test_the_patient_sees_only_their_own_and_never_the_record_id(p, on):
    add(p)
    other_actor, other_person = p.patient("pat2")
    with tenant_transaction(p.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=p.doc, person_public_id=other_person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=p.clinic.org, by="system")
    assert add(p, dict(GOOD, external_id="RES-99", kind="result"), person=other_person
               ).status_code == 201
    mine = p.get("my/references", subject="pat").json()["references"]
    assert len(mine) == 1 and mine[0]["person"] == p.person
    assert mine[0]["organization"]["name"] == "North" and mine[0]["system"] == "emis-web"
    assert mine[0]["refers_to"] == "2026-11-02"
    assert {"external_id", "author", "withdrawn_reason", "withdrawn_by"}.isdisjoint(mine[0])
    theirs = p.get("my/references", subject="pat2").json()["references"]
    assert [r["kind"] for r in theirs] == ["result"]
    assert p.get("my/references", subject="ops").json()["references"] == []


@pytest.mark.parametrize("field,value", [
    ("external_id", "patient has type 2 diabetes"),           # spaces: free text
    ("external_id", "diabetes"),                              # a word, not an id
    ("external_id", "A1" * 40),                               # too long
    ("external_id", ""),
    ("external_id", 12345),
    ("system", "EMIS Web"),
    ("system", "x"),
    ("kind", "diagnosis"),
    ("refers_to", "2099-01-01"),                              # a record about the future
    ("refers_to", "02/11/2026"),
    ("refers_to", None),
])
def test_anything_that_could_carry_content_is_refused(p, on, field, value):
    r = add(p, dict(GOOD, **{field: value}))
    assert r.status_code == 422, (field, value)
    assert r.json()["fields"] == [field]


def test_extra_fields_are_ignored_and_never_stored(p, on):
    r = add(p, dict(GOOD, note="Blood pressure 180/110, started amlodipine"))
    assert r.status_code == 201
    assert "note" not in r.json()["reference"]
    assert "amlodipine" not in str(p.get(f"clinician/patients/{p.person}/references",
                                         subject="doc").content)


def test_every_add_withdraw_and_read_is_audited(p, on):
    ref = add(p).json()["reference"]
    p.post(f"clinician/references/{ref['id']}/withdraw", {"reason": "wrong_patient"},
           subject="doc")
    p.get(f"clinician/patients/{p.person}/references", subject="doc")
    p.get("my/references", subject="pat")
    added, withdrawn = audit(p, "clinicalref.added"), audit(p, "clinicalref.withdrawn")
    assert [(e.actor_ref, e.object_ref) for e in added] == [(p.doc, ref["id"])]
    assert [(e.actor_ref, e.reason_code) for e in withdrawn] == [(p.doc, "wrong_patient")]
    assert [e.object_ref for e in audit(p, "clinicalref.read_care_team")] == [p.person]
    assert [(e.actor_ref, e.object_ref) for e in audit(p, "clinicalref.read_own")] == [
        (p.patient_actor, p.person)]
    for e in added + withdrawn:
        assert "CN-2026-00417" not in str(e.details)        # the record's id stays out of audit
