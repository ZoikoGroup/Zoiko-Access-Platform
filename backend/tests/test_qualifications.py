"""Doctors' qualifications and privileges (DEV-357): who may write, check and withdraw them, what a
patient is shown, and expiry."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import Qualification
from tests.test_api import API
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MINE = "clinician/qualifications"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    practice.add_times(practice.doc)
    return practice


def body(r):
    return json.loads(r.content)


def entry(**over):
    return {"kind": "degree", "title": "MBBS", "issuer": "Example Medical School",
            "reference": "REG-998877", **over}


def add(p, who="doc", **over):
    return p.post(MINE, entry(**over), subject=who)


def mine(p, who="doc"):
    r = p.get(MINE, subject=who)
    assert r.status_code == 200, r.content
    return body(r)


def staff_path(p, tail="qualifications", doctor=None):
    return f"staff/providers/{doctor or p.doc}/{tail}"


def check(p, qualification, who="boss", note="Seen on the register", step="verify"):
    return p.post(staff_path(p, f"qualifications/{qualification}/{step}"), {"note": note},
                  subject=who)


def added(p, **over):
    r = add(p, **over)
    assert r.status_code == 201, r.content
    return body(r)["qualifications"][-1]["id"]


def listed(p):
    r = p.get("directory/doctors", subject="pat")
    assert r.status_code == 200, r.content
    return next(d for d in body(r)["doctors"] if d["id"] == p.doc)


# ------------------------------------------------------------------ writing them down
def test_a_doctor_writes_one_down_and_it_starts_unchecked(p):
    added(p)
    (row,) = mine(p)["qualifications"]
    assert (row["title"], row["state"], row["checked_on"]) == ("MBBS", "recorded", None)
    assert listed(p)["qualifications"] == []                     # a patient sees nothing yet


def test_a_doctor_sees_only_their_own(p):
    added(p)
    assert mine(p, "doc2")["qualifications"] == []


@pytest.mark.parametrize("over,field", [
    ({"kind": "medal"}, "kind"), ({"title": "x"}, "title"), ({"issuer": ""}, "issuer"),
    ({"title": 5}, "title"), ({"obtained_on": "2999-01-01"}, "obtained_on"),
    ({"obtained_on": "soon"}, "obtained_on"),
    ({"obtained_on": "2020-01-01", "expires_on": "2019-01-01"}, "expires_on"),
])
def test_bad_details_are_refused(p, over, field):
    r = add(p, **over)
    assert r.status_code == 422 and field in body(r)["fields"], r.content


def test_only_a_doctor_uses_the_doctors_page(p):
    for who in ("pat", "ops", "boss"):
        assert p.get(MINE, subject=who).status_code == 403, who
        assert add(p, who).status_code == 403, who
    assert p.client.get(API + MINE).status_code == 401


# ------------------------------------------------------------------ checking
def test_an_administrator_records_a_check_and_the_patient_then_sees_it(p):
    qualification = added(p)
    assert check(p, qualification).status_code == 200
    (shown,) = listed(p)["qualifications"]
    assert shown["title"] == "MBBS" and shown["checked_on"]
    assert set(shown) == {"title", "issuer", "kind", "checked_on"}
    text = json.dumps(listed(p))
    assert "REG-998877" not in text and "Seen on the register" not in text


def test_only_the_administrator_checks_and_never_their_own(p):
    qualification = added(p)
    for who in ("ops", "doc", "doc2", "pat"):
        assert check(p, qualification, who=who).status_code == 403, who
    assert mine(p)["qualifications"][0]["state"] == "recorded"


def test_a_check_needs_a_note_and_happens_once(p):
    qualification = added(p)
    assert check(p, qualification, note="ok").status_code == 422
    assert check(p, qualification).status_code == 200
    assert check(p, qualification).status_code == 409


def test_a_withdrawn_one_disappears_for_patients_and_cannot_be_checked_again(p):
    qualification = added(p)
    check(p, qualification)
    assert check(p, qualification, step="withdraw", note="Entered wrongly").status_code == 200
    assert listed(p)["qualifications"] == []
    assert check(p, qualification).status_code == 409
    assert check(p, qualification, step="delete").status_code == 404


def test_an_expired_one_counts_for_nothing(p):
    qualification = added(p)
    check(p, qualification)
    with tenant_transaction(p.clinic.ctx):
        Qualification.objects.update(expires_on=utc_now().date() - dt.timedelta(days=1))
    assert mine(p)["qualifications"][0]["state"] == "expired"
    assert listed(p)["qualifications"] == []


def test_the_administrator_writes_one_down_for_a_doctor_and_staff_can_read_it(p):
    r = p.post(staff_path(p), entry(title="Fellowship"), subject="boss")
    assert r.status_code == 201, r.content
    seen = body(p.get(staff_path(p), subject="ops"))
    assert [q["title"] for q in seen["qualifications"]] == ["Fellowship"]
    assert seen["can_manage"] is False
    assert p.post(staff_path(p), entry(), subject="ops").status_code == 403
    assert p.get(staff_path(p), subject="pat").status_code == 403


def test_someone_who_is_not_this_clinics_doctor_is_not_found(p):
    other = Practice()
    path = f"staff/providers/{other.doc}/qualifications"
    assert p.get(path, subject="boss").status_code == 404
    assert p.post(path, entry(), subject="boss").status_code == 404


# ------------------------------------------------------------------ privileges
def grant(p, who="boss", **over):
    return p.post(staff_path(p, "privileges"), {"service": p.clinic.service, **over},
                  subject=who)


def test_the_administrator_grants_and_withdraws_a_privilege_and_the_doctor_sees_it(p):
    r = grant(p, note="Supervised for the first month")
    assert r.status_code == 201, r.content
    (row,) = body(r)["privileges"]
    assert row["state"] == "in_force" and row["service_name"]
    assert grant(p).status_code == 409                            # already held
    assert mine(p)["privileges"][0]["id"] == row["id"]
    path = staff_path(p, f"privileges/{row['id']}/withdraw")
    assert p.post(path, {"reason": "no"}, subject="boss").status_code == 422
    assert p.post(path, {"reason": "No longer works there"}, subject="boss").status_code == 200
    assert mine(p)["privileges"][0]["state"] == "withdrawn"
    assert grant(p).status_code == 201                            # may be granted afresh


def test_a_privilege_is_only_for_this_clinics_service_and_sensible_dates(p):
    other = Practice()
    assert grant(p, service=other.clinic.service).status_code == 404
    assert grant(p, valid_until="2000-01-01").status_code == 422


def test_only_the_administrator_grants(p):
    for who in ("ops", "doc", "pat"):
        assert grant(p, who=who).status_code == 403, who
    assert mine(p)["privileges"] == []


def test_every_step_is_audited(p):
    qualification = added(p)
    check(p, qualification)
    grant(p)
    with tenant_transaction(p.clinic.ctx):
        actions = set(AuditEvent.objects.filter(action__startswith="credential.")
                      .values_list("action", flat=True))
    assert actions == {"credential.qualification_added", "credential.qualification_verify",
                       "credential.privilege_granted"}
