import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests.test_api import API, Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PRIVATE_LOGIN = "pat.private@example.org"
SCREENS = ("staff/dashboard", "staff/directory", "staff/patients", "staff/compliance")


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def web():
    return Web()


def body_of(response):
    return json.loads(response.content)


def test_staff_screens_need_a_login(web):
    for path in SCREENS:
        r = web.client.get(API + path)
        assert r.status_code == 401


def test_clinic_operations_staff_can_open_every_staff_screen(web):
    web.staff("ops")
    web.patient(PRIVATE_LOGIN)
    for path in SCREENS:
        assert web.get(path, subject="ops").status_code == 200, path
    dash = body_of(web.get("staff/dashboard", subject="ops"))
    assert dash["counts"]["patients"] == 1 and dash["counts"]["organizations"] == 2
    assert dash["evidence"] is True and dash["chain"]["ok"] is True
    patients = body_of(web.get("staff/patients", subject="ops"))["patients"]
    assert len(patients) == 1 and patients[0]["has_login"] is True
    assert patients[0]["identity_status"] == "unverified"


def test_staff_never_see_a_patients_login_anywhere(web):
    web.staff("ops")
    web.patient(PRIVATE_LOGIN)
    slot = web.slots[0]["public_id"]
    assert book(web, slot, subject=PRIVATE_LOGIN).status_code == 201     # makes decisions + audit
    for path in SCREENS:
        text = web.get(path, subject="ops").content.decode()
        assert PRIVATE_LOGIN not in text and "pat.private" not in text, path
    decisions = body_of(web.get("staff/compliance", subject="ops"))["decisions"]
    assert any(d["actor"].startswith("Patient ") for d in decisions)
    assert any(d["actor"] == "ops" for d in decisions)                   # staff by login


def test_the_directory_lists_staff_and_organizations_only(web):
    web.staff("ops")
    web.patient(PRIVATE_LOGIN)
    web.login("guardian@example.org", trust.ActorType.PROXY, "R02")
    directory = body_of(web.get("staff/directory", subject="ops"))
    logins = {m["login"] for m in directory["staff"]}
    assert "ops" in logins and PRIVATE_LOGIN not in logins and "guardian@example.org" not in logins
    assert {o["name"] for o in directory["organizations"]} == {"North", "South"}
    ops = next(m for m in directory["staff"] if m["login"] == "ops")
    assert ops["roles"][0]["role"] == "R04" and ops["roles"][0]["organization_name"] == "North"


def test_support_staff_see_the_platform_but_not_patients(web):
    web.login("sup", trust.ActorType.SUPPORT, "R11")
    for path in ("staff/dashboard", "staff/directory", "staff/compliance"):
        assert web.get(path, subject="sup").status_code == 200, path
    refused = web.get("staff/patients", subject="sup")
    assert refused.status_code == 403 and body_of(refused)["code"] == "POLICY_DENIED"


def test_patients_employers_and_other_non_staff_are_refused_everywhere(web):
    web.patient("pat")
    web.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    web.login("doc", trust.ActorType.CLINICIAN, "R03")
    for who in ("pat", "hr", "doc"):
        for path in SCREENS:
            r = web.get(path, subject=who)
            assert r.status_code == 403, (who, path)
            assert body_of(r)["code"] == "POLICY_DENIED"


def test_the_counts_match_what_exists(web):
    web.staff("ops")
    web.patient("p1")
    web.patient("p2")
    counts = body_of(web.get("staff/dashboard", subject="ops"))["counts"]
    directory = body_of(web.get("staff/directory", subject="ops"))
    assert counts["patients"] == 2
    assert counts["staff"] == len(directory["staff"])
    assert counts["roles_in_force"] >= 3                     # two patients plus the staff roles


def test_the_staff_screens_work_with_a_real_signed_token(web, settings):
    from modules.trust import auth
    web.staff("ops")
    with tenant_transaction(web.clinic.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.FACILITY_USER, issuer="local",
                                     subject="realops")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=web.clinic.org,
                          role_code="R04", by="system")
        trust.set_password(actor.public_id, login="realops", password="Correct-Horse-9",
                           by="system")
    token = auth.login(tenant_name=web.clinic.tenant_name, login="realops",
                       password="Correct-Horse-9").token
    settings.DEBUG, settings.DEV_AUTH_STUB = False, False     # dev stub off: only the token works
    for path in SCREENS:
        r = web.client.get(API + path, HTTP_AUTHORIZATION=f"Bearer {token}")
        assert r.status_code == 200, path
