import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.trust import api as trust
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.boss = practice.login("boss", "facility_user", "R13")
    return practice


def name(p, text, subject="doc"):
    r = p.post("auth/display-name", {"name": text}, subject=subject)
    assert r.status_code == 200
    return r


def providers(p, subject="boss"):
    r = p.get("staff/providers", subject=subject)
    assert r.status_code == 200
    return {row["id"]: row for row in json.loads(r.content)["providers"]}


def verify(p, who=None, subject="boss", **over):
    body = {"basis": "licence_register", "note": "Reg no checked on the council site", **over}
    return p.post(f"staff/providers/{who or p.doc}/verify", body, subject=subject)


def unverify(p, who=None, subject="boss", reason="Left the clinic"):
    return p.post(f"staff/providers/{who or p.doc}/unverify", {"reason": reason}, subject=subject)


def shown_to_patient(p):
    p.add_times(p.doc)
    slots = [s for s in p.open_slots() if s["provider"]]
    assert slots
    return {s["provider"]["id"]: s["provider"]["verified"] for s in slots}


# ------------------------------------------------------------------ the happy path
def test_an_administrator_verifies_a_doctors_name_and_patients_see_the_mark(p):
    name(p, "Dr Asha Rao")
    assert shown_to_patient(p)[p.doc] is False  # a typed name is not checked
    r = verify(p)
    assert r.status_code == 200
    assert json.loads(r.content)["name_check"]["state"] == "verified"
    row = providers(p)[p.doc]
    assert (
        row["name_check"]["state"] == "verified"
        and row["name_check"]["basis"] == "licence_register"
    )
    assert row["name_check"]["note"] and row["name_check"]["checked_on"]
    assert shown_to_patient(p)[p.doc] is True
    mine = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    assert mine == []  # nothing booked, nothing leaked


def test_a_booking_shows_whether_the_doctors_name_was_checked(p):
    name(p, "Dr Asha Rao")
    verify(p)
    p.add_times(p.doc)
    slot = p.slot_of("Dr Asha Rao")
    booked = json.loads(p.post("bookings", {"slot": slot}, subject="pat", key="k1" * 8).content)
    assert booked["booking"]["provider"] == {"id": p.doc, "name": "Dr Asha Rao", "verified": True}


def test_an_unchecked_doctor_is_shown_as_unverified(p):
    row = providers(p)[p.doc2]
    assert row["name_check"] == {"state": "none"}
    assert shown_to_patient(p).get(p.doc2) in (None, False)


# ------------------------------------------------------------------ a check belongs to one name
def test_changing_the_name_ends_the_check_until_it_is_done_again(p):
    name(p, "Dr Asha Rao")
    verify(p)
    name(p, "Dr Asha R. Rao")  # a new, unchecked name
    assert providers(p)[p.doc]["name_check"]["state"] == "name_changed"
    assert shown_to_patient(p)[p.doc] is False
    assert verify(p).status_code == 200  # the administrator checks the new one
    assert providers(p)[p.doc]["name_check"]["state"] == "verified"
    assert shown_to_patient(p)[p.doc] is True


def test_removing_the_name_also_ends_the_check(p):
    name(p, "Dr Asha Rao")
    verify(p)
    assert p.client.delete("/api/v1/auth/display-name", **p.header("doc")).status_code == 200
    assert providers(p)[p.doc]["name_check"]["state"] == "name_changed"
    assert shown_to_patient(p)[p.doc] is False


def test_there_is_nothing_to_check_until_the_doctor_has_chosen_a_name(p):
    assert verify(p).status_code == 422
    assert providers(p)[p.doc]["name_check"] == {"state": "none"}


# ------------------------------------------------------------------ who may do it
@pytest.mark.parametrize("who", ["ops", "pat", "doc"])
def test_only_an_administrator_may_verify_or_withdraw(p, who):
    name(p, "Dr Asha Rao")
    assert verify(p, subject=who).status_code == 403
    assert verify(p).status_code == 200
    assert unverify(p, subject=who).status_code == 403
    assert providers(p)[p.doc]["name_check"]["state"] == "verified"


def test_the_list_says_whether_the_viewer_can_manage_checks(p):
    for subject, expected in (("boss", True), ("ops", False)):
        data = json.loads(p.get("staff/providers", subject=subject).content)
        assert data["can_manage"] is expected
    assert set(data["bases"]) == {
        "licence_register",
        "identity_document",
        "employer_record",
        "other",
    }


def test_nobody_can_check_their_own_name(p):
    ctx = p.clinic.ctx
    with tenant_transaction(ctx), pytest.raises(ZoikoError) as e:
        trust.verify_name(
            p.doc, by_ref=p.doc, organization_ref=p.clinic.org, basis="other", note="my own name"
        )
    assert e.value.code == ErrorCode.POLICY_DENIED


def test_only_a_doctor_of_this_organization_can_be_checked(p):
    other_admin = p.login("boss2", "facility_user", "R13")
    for who in ("nobody123", other_admin, p.desk):  # unknown, another administrator, a receptionist
        assert verify(p, who=who).status_code == 404
    assert verify(p, who=p.boss).status_code == 403  # an administrator is never their own checker


def test_a_doctor_of_another_site_cannot_be_checked_by_this_sites_administrator(p):
    with tenant_transaction(p.clinic.ctx):
        site = trust.create_organization(name="Other Site").public_id
        away = trust.register_actor(actor_type="clinician", issuer="idp", subject="away").public_id
        trust.assign_role(
            actor_public_id=away, organization_public_id=site, role_code="R03", by="system"
        )
    p.post("auth/display-name", {"name": "Dr Far Away"}, subject="away")
    assert verify(p, who=away).status_code == 404  # a doctor, but not at this site


# ------------------------------------------------------------------ what is recorded
@pytest.mark.parametrize(
    "bad",
    [
        {"basis": "trust_me"},
        {"basis": ""},
        {"basis": "other", "note": ""},
        {"basis": "other", "note": "no"},
        {"note": "x" * 201},
    ],
)
def test_a_bad_check_is_refused(p, bad):
    name(p, "Dr Asha Rao")
    assert verify(p, **bad).status_code == 422
    assert providers(p)[p.doc]["name_check"] == {"state": "none"}


def test_withdrawing_needs_a_reason_and_works_once(p):
    name(p, "Dr Asha Rao")
    verify(p)
    assert unverify(p, reason="").status_code == 422
    assert unverify(p, reason="Licence lapsed").status_code == 200
    row = providers(p)[p.doc]["name_check"]
    assert row["state"] == "withdrawn" and row["reason"] == "Licence lapsed"
    assert shown_to_patient(p)[p.doc] is False
    assert unverify(p).status_code == 409  # nothing left to withdraw
    assert verify(p).status_code == 200  # a fresh check after a withdrawal
    assert providers(p)[p.doc]["name_check"]["state"] == "verified"


def test_withdrawing_when_never_checked_is_refused(p):
    assert unverify(p).status_code == 409


def test_every_check_and_withdrawal_is_audited(p):
    from modules.evidence.models import AuditEvent

    name(p, "Dr Asha Rao")
    verify(p)
    unverify(p)
    with tenant_transaction(p.clinic.ctx):
        actions = list(AuditEvent.objects.filter(object_ref=p.doc).values_list("action", flat=True))
    assert "actor.name_verified" in actions and "actor.name_check_withdrawn" in actions


def test_a_doctor_can_see_the_state_of_their_own_name_check_but_not_the_note(p):
    def mine(subject="doc"):
        return json.loads(p.get("me", subject=subject).content)["name_check"]

    assert mine() == {"state": "none"}
    name(p, "Dr Asha Rao")
    verify(p, note="Private note about the register number")
    got = mine()
    assert got["state"] == "verified" and got["basis_text"] and got["checked_on"]
    assert "note" not in got and "Private" not in json.dumps(got)
    name(p, "Dr Asha R. Rao")
    assert mine()["state"] == "name_changed"
    verify(p)
    unverify(p, reason="Licence lapsed")
    assert mine()["state"] == "withdrawn" and mine()["reason"] == "Licence lapsed"
    assert mine("doc2") == {"state": "none"}                       # each doctor sees only their own
    assert mine("pat") == {"state": "none"}
