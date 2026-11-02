import json

import pytest

from tests.test_api import Web, book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def treated(p):
    """The patient has been seen by `doc`: a booking made and confirmed."""
    p.add_times(p.doc)
    booking = json.loads(book(p, p.slot_of("doc"), subject="pat").content)["booking"]["id"]
    assert p.post(f"bookings/{booking}/confirm", subject="ops").status_code == 200


def team(p, subject="pat"):
    r = p.get("care-team", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)["people"]


def log(p, subject="pat"):
    r = p.get("access-log", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)["entries"]


# ------------------------------------------------------------------ my care team
def test_a_patient_with_no_visits_has_an_empty_care_team(p):
    assert team(p) == [{"person": p.person, "team": []}]


def test_the_doctor_appears_in_the_care_team_once_the_clinic_confirms_the_visit(p):
    p.add_times(p.doc)
    booking = json.loads(book(p, p.slot_of("doc"), subject="pat").content)["booking"]["id"]
    assert team(p)[0]["team"] == []                              # booked, not confirmed
    p.post(f"bookings/{booking}/confirm", subject="ops")
    members = team(p)[0]["team"]
    assert [m["clinician"] for m in members] == ["doc"]
    assert members[0]["basis"] == "appointment" and members[0]["since"] and members[0]["until"]


def test_another_patients_care_team_is_private(p):
    treated(p)
    _, other = p.patient("pat2")
    assert team(p, "pat2") == [{"person": other, "team": []}]
    assert all(entry["person"] != p.person for entry in team(p, "pat2"))


def test_doctors_and_staff_have_no_care_team_of_their_own(p):
    treated(p)
    assert team(p, "doc") == [] and team(p, "ops") == []


def test_a_contested_link_is_not_shown(p):
    treated(p)
    from foundation.cell_context import tenant_transaction
    from modules.trust.models import CareRelationship
    with tenant_transaction(p.clinic.ctx):
        CareRelationship.objects.filter(actor__public_id=p.doc).update(contested=True)
    assert team(p)[0]["team"] == []


# ------------------------------------------------------------------ who looked at my information
def test_the_patient_sees_when_a_doctor_looked_at_their_details(p):
    treated(p)
    assert p.get(f"people/{p.person}/profile", subject="doc").status_code == 200
    entries = log(p)
    seen = [e for e in entries if e["who"] == "doc" and e["operation"] == "read:person"]
    assert seen and seen[0]["allowed"] is True and seen[0]["person"] == p.person
    assert seen[0]["at"] and seen[0]["role"] == "R03" and seen[0]["purpose"] == "DIRECT_CARE"


def test_a_refused_attempt_is_shown_too(p):
    treated(p)
    assert p.get(f"people/{p.person}/profile", subject="doc2").status_code == 403   # no link
    refused = [e for e in log(p) if e["who"] == "doc2"]
    assert refused and all(e["allowed"] is False for e in refused)


def test_the_patients_own_looks_are_left_out(p):
    p.get("profile", subject="pat")
    p.get("bookings", subject="pat")
    p.get("coverage", subject="pat")
    assert log(p) == []        # nobody else looked; the patient's own looks are hidden


def test_the_newest_comes_first(p):
    treated(p)
    p.get(f"people/{p.person}/profile", subject="doc")
    p.get(f"people/{p.person}/profile", subject="ops")
    times = [e["at"] for e in log(p)]
    assert times == sorted(times, reverse=True) and len(times) >= 2


def test_nobody_else_sees_anyone_elses_log(p):
    treated(p)
    p.get(f"people/{p.person}/profile", subject="doc")
    _, other = p.patient("pat2")
    assert all(e["person"] == other for e in log(p, "pat2"))
    assert log(p, "doc") == [] and log(p, "ops") == []


def test_another_customers_activity_never_appears(p):
    treated(p)
    outsider = Web()
    outsider.patient("pat")
    outsider.staff("desk")
    outsider.get("services", subject="desk")
    assert all(e["person"] == p.person for e in log(p))
    assert log(outsider) == [] or all(e["person"] != p.person for e in log(outsider))


def test_the_log_is_capped_and_says_nothing_about_what_was_read(p):
    treated(p)
    for _ in range(3):
        p.get(f"people/{p.person}/profile", subject="doc")
    entries = log(p)
    assert len(entries) <= 100
    assert set(entries[0]) == {"at", "who", "role", "operation", "purpose", "allowed", "person"}
