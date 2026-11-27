"""Who answers a referral (GHE-14 WF-16, WF-17, T-38 to T-41). DEV-109.

Receipt by staff is not acceptance of care; only the named owner of the receiving inbox, or the
fallback, who must be a doctor, accepts, asks for more or declines; a decline stays with the
sender; no answer escalates and is never closed on its own."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.referral.models import Referral
from modules.trust import api as trust
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def c():
    return Clinic2()


def inbox(c, subject="boss"):
    r = c.get("staff/referral-inbox", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def name(c, owner, fallback="", subject="boss"):
    return c.post("staff/referral-inbox", {"owner": owner, "fallback": fallback}, subject=subject)


def answerer_list(c, subject="doc2"):
    r = c.get("clinician/referral-inbox", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["referrals"]


def acknowledge(c, referral_id, subject="ops"):
    return c.post(f"referrals/{referral_id}/acknowledge", {}, subject=subject)


# ------------------------------------------------------------------ naming the owner
def test_the_administrator_names_an_owner_and_everyone_can_see_who(c):
    box = inbox(c)
    assert box["owner"]["id"] == c.doc2 and box["fallback"] is None and box["can_manage"] is True
    assert {d["id"] for d in box["doctors"]} == {c.doc, c.doc2}
    assert inbox(c, "ops")["can_manage"] is False        # receiving staff can read, not change


def test_only_the_clinic_administrator_can_name_the_owner(c):
    for who in ("ops", "doc", "doc2", "pat"):
        assert name(c, c.doc, subject=who).status_code == 403
    assert inbox(c)["owner"]["id"] == c.doc2


def test_the_owner_and_fallback_must_be_different_doctors_of_this_clinic(c):
    assert name(c, c.patient_actor).status_code == 422          # a patient is not a doctor
    assert name(c, c.desk).status_code == 422                   # nor is a receptionist
    assert name(c, c.doc2, fallback=c.doc2).status_code == 422  # not the same person twice
    assert name(c, c.doc2, fallback=c.patient_actor).status_code == 422
    assert inbox(c)["fallback"] is None


# ------------------------------------------------------------------ receipt is not acceptance
def test_staff_receipt_does_not_accept_care(c):
    made = issued(c)
    done = acknowledge(c, made["id"])
    assert done.status_code == 200
    seen = c.incoming()[0]
    assert seen["state"] == "issued" and seen["acknowledged_at"]      # seen, not accepted
    assert seen["responsible"]["id"] == c.doc                          # still the sender's
    assert acknowledge(c, made["id"]).status_code == 200               # repeating is harmless


def test_only_receiving_staff_can_mark_receipt_and_not_after_a_decision(c):
    made = issued(c)
    for who in ("doc", "doc2", "pat"):
        assert acknowledge(c, made["id"], who).status_code == 403
    c.decide(made["id"], "accepted")
    assert acknowledge(c, made["id"]).status_code == 409


# ------------------------------------------------------------------ who may decide
def test_without_a_named_owner_nobody_can_accept():
    c = Clinic2(name_inbox=False)
    made = issued(c)
    for who in ("doc", "doc2", "ops", "boss"):
        assert c.decide(made["id"], "accepted", subject=who).status_code == 403
    assert c.incoming()[0]["owner"] is None and c.incoming()[0]["state"] == "issued"


def test_the_fallback_may_decide_and_a_stranger_doctor_may_not(c):
    third = c.login("doc3", trust.ActorType.CLINICIAN, "R03")
    fourth = c.login("doc4", trust.ActorType.CLINICIAN, "R03")
    assert name(c, c.doc2, fallback=third).status_code == 200
    made = issued(c)
    assert c.decide(made["id"], "accepted", subject="doc4").status_code == 403
    assert fourth and c.decide(made["id"], "accepted", subject="doc3").status_code == 200


def test_the_owner_sees_the_referral_without_ever_having_treated_the_patient(c):
    made = issued(c)
    assert [r["id"] for r in answerer_list(c)] == [made["id"]]
    assert answerer_list(c, "doc") == []                       # the sender is not an answerer


def test_changing_the_owner_moves_the_waiting_referrals_to_the_new_one(c):
    third = c.login("doc3", trust.ActorType.CLINICIAN, "R03")
    made = issued(c)
    assert name(c, third).status_code == 200
    assert [r["id"] for r in answerer_list(c, "doc3")] == [made["id"]]
    assert c.incoming()[0]["owner"]["id"] == third
    assert c.decide(made["id"], "accepted", subject="doc3").status_code == 200


# ------------------------------------------------------------------ the access a referral gives
def test_declining_ends_the_doctors_access_to_the_patient(c):
    made = issued(c)
    assert len(answerer_list(c)) == 1
    assert c.decide(made["id"], "declined", "Not our speciality").status_code == 200
    assert answerer_list(c) == []          # the link the referral gave has ended


def test_accepting_keeps_the_acceptors_access_but_ends_the_fallbacks(c):
    third = c.login("doc3", trust.ActorType.CLINICIAN, "R03")
    name(c, c.doc2, fallback=third)
    made = issued(c)
    assert len(answerer_list(c, "doc3")) == 1
    assert c.decide(made["id"], "accepted").status_code == 200
    assert len(answerer_list(c, "doc2")) == 1 and answerer_list(c, "doc3") == []


# ------------------------------------------------------------------ responsibility and escalation
def test_a_decline_leaves_responsibility_with_the_sender_and_an_accept_moves_it(c):
    first = issued(c)
    c.decide(first["id"], "declined", "Wrong clinic")
    assert c.sent()[0]["responsible"]["id"] == c.doc
    again = issued(c)
    c.decide(again["id"], "accepted")
    assert [r for r in c.sent() if r["id"] == again["id"]][0]["responsible"]["id"] == c.doc2


def test_an_unanswered_referral_is_flagged_but_never_closed(c):
    made = issued(c)
    assert c.incoming()[0]["escalated"] is False
    with tenant_transaction(c.clinic.ctx):
        Referral.objects.filter(public_id=made["id"]).update(
            created_at=utc_now() - dt.timedelta(hours=25))
    seen = c.incoming()[0]
    assert seen["escalated"] is True and seen["state"] == "issued"       # flagged, still open
    assert answerer_list(c)[0]["escalated"] is True
    assert c.decide(made["id"], "accepted").status_code == 200           # and still answerable
    assert c.incoming()[0]["escalated"] is False
