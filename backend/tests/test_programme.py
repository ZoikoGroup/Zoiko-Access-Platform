"""Employer programme links and visit counts: one-time codes, totals only (DEV-312)."""

import json

import pytest
from django.db import connection
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests.test_api import API, book
from tests.test_clinician import Practice
from tests.test_exchange import Partner
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.SPONSOR_MIN_GROUP = 3


class Employer(World):
    """An employer customer with one login (R08)."""

    def __init__(self, subject="hr"):
        super().__init__(tenant_type=trust.TenantType.EMPLOYER)
        self.client = Client()
        self.subject = subject
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(
                actor_type=trust.ActorType.EMPLOYER_USER, issuer="idp", subject=subject
            )
            trust.assign_role(
                actor_public_id=actor.public_id,
                organization_public_id=self.org,
                role_code="R08",
                by="system",
            )

    def call(self, method, path, body=None, subject=None):
        header = {
            "HTTP_AUTHORIZATION": f"Bearer dev:{self.tenant_public_id}:idp:"
            f"{subject or self.subject}"
        }
        if method == "get":
            return self.client.get(API + path, **header)
        return self.client.post(
            API + path, json.dumps(body or {}), content_type="application/json", **header
        )


@pytest.fixture
def world():
    platform = Platform()
    clinic = Practice()
    employer = Employer()
    refs = [f"emp-{i:04d}" for i in range(6)]
    assert employer.call("post", "portal/program/enrol", {"refs": refs}).status_code in (200, 201)
    return platform, clinic, employer, refs


def body(r):
    return json.loads(r.content)


def code_for(employer, member="emp-0000"):
    r = employer.call("post", "programme/codes", {"member_ref": member})
    assert r.status_code == 201, r.content
    return body(r)["code"]["code"]


def redeem(clinic, code, subject="pat"):
    return clinic.post("programme/redeem", {"code": code}, subject=subject)


def activity(employer):
    return body(employer.call("get", "programme/activity"))


def patients_booked(clinic, n):
    """n patients each book their own time, counting 'pat' as the first."""
    names = ["pat"] + [f"p{i}" for i in range(2, n + 1)]
    for name in names[1:]:
        clinic.patient(name)
    clinic.add_times(clinic.doc, count=n + 2)
    for name in names:  # a booked time leaves the open list
        r = book(clinic, clinic.slot_of("doc", 0), subject=name)
        assert r.status_code in (200, 201), r.content
    return names


# ------------------------------------------------------------------ the codes
def test_the_employer_makes_a_one_time_code_for_an_enrolled_member_and_sees_only_its_state(world):
    _, _, employer, _ = world
    code = code_for(employer)
    assert len(code) == 11 and code[5] == "-"
    listed = body(employer.call("get", "programme/codes"))["codes"]
    assert [(c["member_ref"], c["state"]) for c in listed] == [("emp-0000", "issued")]
    assert code not in json.dumps(listed)  # shown once, never again
    assert employer.call("post", "programme/codes", {"member_ref": "nobody-1"}).status_code == 422
    newer = code_for(employer)  # replaces the unused one
    assert newer != code
    assert redeem(world[1], code).status_code == 404


def test_the_code_is_stored_only_as_a_fingerprint(world):
    _, _, employer, _ = world
    code = code_for(employer)
    with connection.cursor() as cur:
        cur.execute("select code_key from programme_programmelink")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and code.replace("-", "") not in stored and code not in stored


def test_a_patient_uses_a_code_once_and_every_bad_code_gets_the_same_answer(world):
    _, clinic, employer, _ = world
    code = code_for(employer)
    for bad in ("", "AAAAA-AAAAA", "nonsense", "A" * 40, 7, None, code[:-1] + "!"):
        assert redeem(clinic, bad).status_code == 404
    got = redeem(clinic, code)
    assert got.status_code == 201
    assert "member" not in json.dumps(body(got)) and "emp-0000" not in json.dumps(body(got))
    clinic.patient("p2")
    assert redeem(clinic, code, "p2").status_code == 404  # already used
    listed = body(employer.call("get", "programme/codes"))["codes"]
    assert listed[0]["state"] == "redeemed"
    assert clinic.person not in json.dumps(listed)  # never who


def test_a_patient_already_counted_for_an_employer_cannot_link_twice(world):
    _, clinic, employer, _ = world
    redeem(clinic, code_for(employer, "emp-0000"))
    assert redeem(clinic, code_for(employer, "emp-0001")).status_code == 409


def test_an_expired_code_does_not_work(world):
    from datetime import timedelta

    from foundation.cell_context import tenant_transaction as tx
    from modules.programme.models import ProgrammeLink

    platform, clinic, employer, _ = world
    code = code_for(employer)
    with tx(platform.ctx):
        link = ProgrammeLink.objects.get()
        link.expires_at = link.expires_at - timedelta(days=31)
        link.save(update_fields=["expires_at"])
    assert redeem(clinic, code).status_code == 404


def test_the_patient_sees_their_programme_and_can_leave_it(world):
    _, clinic, employer, _ = world
    redeem(clinic, code_for(employer))
    mine = body(clinic.get("programme/mine", subject="pat"))["programmes"]
    assert len(mine) == 1 and mine[0]["employer"] == employer.tenant_name
    assert "emp-0000" not in json.dumps(mine)
    link_id = mine[0]["id"]
    assert clinic.post(f"programme/mine/{link_id}/leave", subject="pat").status_code == 200
    assert body(clinic.get("programme/mine", subject="pat"))["programmes"] == []
    assert clinic.post(f"programme/mine/{link_id}/leave", subject="pat").status_code == 404


def test_the_employer_can_revoke_and_the_member_can_be_given_a_new_code(world):
    _, clinic, employer, _ = world
    redeem(clinic, code_for(employer))
    assert code_for_status(employer).status_code == 409  # already linked
    assert employer.call("post", "programme/codes/emp-0000/revoke").status_code == 200
    assert employer.call("post", "programme/codes/emp-0000/revoke").status_code == 404
    assert body(clinic.get("programme/mine", subject="pat"))["programmes"] == []
    assert code_for(employer)


def code_for_status(employer, member="emp-0000"):
    return employer.call("post", "programme/codes", {"member_ref": member})


# ------------------------------------------------------------------ the counts
def test_totals_by_month_appear_only_when_enough_different_people_contributed(world):
    _, clinic, employer, _ = world
    names = patients_booked(clinic, 3)
    for i, name in enumerate(names[:2]):  # two linked: below the minimum
        redeem(clinic, code_for(employer, f"emp-000{i}"), name)
    two = activity(employer)
    assert two["min_group"] == 3 and two["linked"] is None and two["linked_hidden"] is True
    assert two["months"] and all(m["bookings"] is None and m["hidden"] for m in two["months"])
    redeem(clinic, code_for(employer, "emp-0002"), names[2])  # three linked: shown
    three = activity(employer)
    assert three["linked"] == 3 and three["linked_hidden"] is False
    assert [m["bookings"] for m in three["months"]] == [3] and three["months"][0]["visits"] == 0
    text = json.dumps(three)
    assert clinic.person not in text and "doc" not in text and "emp-000" not in text


def test_visits_count_only_attended_appointments(world):
    from foundation.cell_context import tenant_transaction as tx
    from modules.scheduling.models import Booking

    _, clinic, employer, _ = world
    names = patients_booked(clinic, 3)
    for i, name in enumerate(names):
        redeem(clinic, code_for(employer, f"emp-000{i}"), name)
    with tx(clinic.clinic.ctx):
        first = Booking.objects.order_by("created_at").first()
        first.attendance = "attended"
        first.save(update_fields=["attendance"])
    month = activity(employer)["months"][0]
    assert month["bookings"] == 3 and month["visits"] == 1


def test_leaving_or_revoking_stops_a_person_counting(world):
    _, clinic, employer, _ = world
    names = patients_booked(clinic, 4)
    for i, name in enumerate(names):
        redeem(clinic, code_for(employer, f"emp-000{i}"), name)
    assert activity(employer)["months"][0]["bookings"] == 4
    employer.call("post", "programme/codes/emp-0003/revoke")  # the fourth is revoked
    assert activity(employer)["months"][0]["bookings"] == 3
    mine_id = body(clinic.get("programme/mine", subject="pat"))["programmes"][0]["id"]
    clinic.post(f"programme/mine/{mine_id}/leave", subject="pat")
    month = activity(employer)["months"][0]
    assert month["bookings"] is None and month["hidden"] is True  # two left: hidden


def test_a_patient_who_never_linked_is_never_counted(world):
    _, clinic, employer, _ = world
    names = patients_booked(clinic, 3)
    for i, name in enumerate(names[:2]):
        redeem(clinic, code_for(employer, f"emp-000{i}"), name)
    assert activity(employer)["linked"] is None  # the third did not link


# ------------------------------------------------------------------ who may do what
def test_only_the_employer_uses_the_employer_routes_and_only_a_patient_the_patient_routes(world):
    _, clinic, employer, _ = world
    for who in ("ops", "doc", "pat"):
        assert clinic.get("programme/codes", subject=who).status_code == 403
        assert clinic.get("programme/activity", subject=who).status_code == 403
        assert (
            clinic.post("programme/codes", {"member_ref": "emp-0000"}, subject=who).status_code
            == 403
        )
    lab = Partner("R05", "lab1")
    assert lab.call("get", "programme/codes").status_code == 403
    assert employer.call("post", "programme/redeem", {"code": "AAAAA-AAAAA"}).status_code == 403
    assert employer.call("get", "programme/mine").status_code in (200, 403)


def test_another_employer_sees_and_changes_nothing(world):
    _, clinic, employer, _ = world
    code_for(employer)
    rival = Employer("hr2")
    assert body(rival.call("get", "programme/codes"))["codes"] == []
    assert rival.call("post", "programme/codes/emp-0000/revoke").status_code == 404
    assert activity(rival)["months"] == []
