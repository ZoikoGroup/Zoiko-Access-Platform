import json
import uuid

import pytest
from django.test import Client, override_settings

from modules.trust.roles_seed import DEFAULT_ROLES

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

DEV = {"DEBUG": True, "DEV_AUTH_STUB": True}


def post(client, action, **body):
    return client.post(f"/dev/api/{action}", json.dumps(body), content_type="application/json")


def setup_tenant(client):
    name = f"T {uuid.uuid4().hex[:8]}"
    t = post(client, "tenant", name=name, type="provider_org").json()["public_id"]
    org = post(client, "organization", tenant=t, name="North").json()["public_id"]
    actor = post(client, "actor", tenant=t, actor_type="employer_user",
                 issuer="idp", subject="hr").json()["public_id"]
    post(client, "assign-role", tenant=t, actor=actor, organization=org, role_code="R08")
    return t, org, actor


def test_the_console_is_invisible_unless_both_dev_flags_are_on():
    client = Client()
    for flags in ({"DEBUG": False, "DEV_AUTH_STUB": True}, {"DEBUG": True, "DEV_AUTH_STUB": False},
                  {"DEBUG": False, "DEV_AUTH_STUB": False}):
        with override_settings(**flags):
            assert client.get("/").status_code == 404
            assert client.get("/dev/api/state").status_code == 404
            assert post(client, "tenant", name="x", type="payer").status_code == 404


@override_settings(**DEV)
def test_the_page_loads_and_contains_the_controls():
    response = Client().get("/")
    assert response.status_code == 200 and b"Access check" in response.content


@override_settings(**DEV)
def test_a_full_hand_driven_flow_works_and_denials_are_explained():
    client = Client()
    t, org, actor = setup_tenant(client)
    bad = post(client, "decide", tenant=t, actor=actor, operation="read:person",
               purpose="PROGRAM_ADMIN", organization=org).json()
    assert (bad["allowed"], bad["reason"]) == (False, "HARD_PROHIBITION")
    good = post(client, "decide", tenant=t, actor=actor, operation="read:billing",
                purpose="PROGRAM_ADMIN", organization=org).json()
    assert (good["allowed"], good["role"]) == (True, "R08")

    state = client.get("/dev/api/state", {"tenant": t}).json()
    assert state["chain"]["ok"] is True
    assert [d["allowed"] for d in state["decisions"]] == [True, False]
    assert state["overview"]["actors"][0]["roles"][0]["role"] == "R08"
    assert len(state["overview"]["roles"]) == len(DEFAULT_ROLES)
    assert state["events"][0]["action"] == "access.decide"
    assert any(x["public_id"] == t for x in state["tenants"])


@override_settings(**DEV)
def test_bad_input_gets_a_clean_error_not_a_crash():
    client = Client()
    t, org, actor = setup_tenant(client)
    r = post(client, "actor", tenant=t, actor_type="alien", issuer="i", subject="s")
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert post(client, "organization", tenant=t, name="   ").status_code == 422
    assert post(client, "organization", tenant="nope", name="X").status_code == 404
    assert post(client, "organization", name="X").status_code == 422        # no tenant given
    raw = client.post("/dev/api/organization", "{not json", content_type="application/json")
    assert raw.status_code == 422
    assert client.post("/dev/api/unknown", "{}", content_type="application/json"
                       ).status_code == 404
    assert client.get("/dev/api/decide").status_code == 404                  # POST only


@override_settings(**DEV)
def test_one_tenants_screen_never_shows_another_tenants_people():
    client = Client()
    t1, _, actor1 = setup_tenant(client)
    t2, org2, _ = setup_tenant(client)
    r = post(client, "decide", tenant=t2, actor=actor1, operation="read:billing",
             purpose="PROGRAM_ADMIN", organization=org2).json()
    assert (r["allowed"], r["reason"]) == (False, "ACTOR_UNKNOWN")
    people = client.get("/dev/api/state", {"tenant": t2}).json()["overview"]["actors"]
    assert actor1 not in [a["public_id"] for a in people]
    assert t1 != t2


@override_settings(**DEV)
def test_the_patient_flow_through_the_console_opens_and_closes_the_right_doors():
    client = Client()
    t, org, _ = setup_tenant(client)

    def actor(kind, subject, role):
        a = post(client, "actor", tenant=t, actor_type=kind, issuer="i", subject=subject)
        a = a.json()["public_id"]
        post(client, "assign-role", tenant=t, actor=a, organization=org, role_code=role)
        return a

    patient, doctor = actor("patient", "pat", "R01"), actor("clinician", "doc", "R03")
    mine = post(client, "person", tenant=t).json()["public_id"]
    other = post(client, "person", tenant=t).json()["public_id"]
    assert post(client, "link-person", tenant=t, person=mine, actor=patient).status_code == 200
    link = post(client, "care-start", tenant=t, actor=doctor, person=mine,
                basis="referral").json()["public_id"]

    def ask(who, purpose, person):
        return post(client, "decide", tenant=t, actor=who, operation="read:person",
                    purpose=purpose, subject=person).json()

    assert ask(patient, "SELF_SERVICE", mine)["allowed"] is True
    assert ask(patient, "SELF_SERVICE", other)["reason"] == "RELATIONSHIP_UNVERIFIED"
    assert ask(doctor, "DIRECT_CARE", mine)["allowed"] is True
    assert ask(doctor, "DIRECT_CARE", other)["allowed"] is False
    post(client, "care-contest", tenant=t, link=link, contested=True)
    assert ask(doctor, "DIRECT_CARE", mine)["allowed"] is False
    post(client, "care-contest", tenant=t, link=link, contested=False)
    post(client, "care-end", tenant=t, link=link)
    assert ask(doctor, "DIRECT_CARE", mine)["allowed"] is False

    state = client.get("/dev/api/state", {"tenant": t}).json()
    assert len(state["overview"]["persons"]) == 2 and state["chain"]["ok"] is True
    assert state["overview"]["care"][0]["status"] == "ended"
    bad = post(client, "care-start", tenant=t, actor=patient, person=mine, basis="referral")
    assert bad.status_code == 403                              # a patient login has no care role
