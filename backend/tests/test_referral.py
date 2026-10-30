import json

import pytest

from foundation import dispatch
from foundation.outbox import relay_once
from modules.comms import api as comms
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


class Clinic2(Practice):
    """A practice where the doctor has already seen the patient, plus a Cardiology service."""

    def __init__(self):
        super().__init__()
        self.add_times(self.doc)
        booking = book(self, self.slot_of("doc"), subject="pat").json()["booking"]
        assert self.post(f"bookings/{booking['id']}/confirm", subject="ops").status_code == 200
        made = self.post("services", {
            "organization": self.clinic.org, "name": "Cardiology", "category": "specialist",
            "duration_minutes": 30}, subject="ops")
        assert made.status_code == 201
        self.cardiology = json.loads(made.content)["service"]["id"]

    def refer(self, subject="doc", patient=None, service=None, reason="Chest pain on exertion",
              urgency="routine"):
        return self.post("clinician/referrals", {
            "patient": patient or self.person, "service": service or self.cardiology,
            "reason": reason, "urgency": urgency}, subject=subject)

    def sent(self, subject="doc"):
        r = self.get("clinician/referrals", subject=subject)
        assert r.status_code == 200
        return json.loads(r.content)["referrals"]

    def incoming(self, subject="ops"):
        r = self.get("referrals/incoming", subject=subject)
        assert r.status_code == 200
        return json.loads(r.content)["referrals"]

    def mine(self, subject="pat"):
        r = self.get("referrals", subject=subject)
        assert r.status_code == 200
        return json.loads(r.content)["referrals"]

    def decide(self, referral_id, decision, reason="", subject="ops"):
        return self.post(f"referrals/{referral_id}/decide",
                         {"decision": decision, "reason": reason}, subject=subject)


@pytest.fixture
def c():
    return Clinic2()


def issued(c, **kw):
    r = c.refer(**kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["referral"]


# ------------------------------------------------------------------ the happy path
def test_a_doctor_refers_a_patient_and_all_three_sides_see_it(c):
    made = issued(c, urgency="urgent")
    assert made["state"] == "issued" and made["urgency"] == "urgent"
    assert made["service"]["name"] == "Cardiology" and made["referrer"]["name"] == "doc"
    assert [r["id"] for r in c.sent()] == [made["id"]]                 # the doctor
    assert [r["id"] for r in c.incoming()] == [made["id"]]             # the receiving clinic
    mine = c.mine()                                                    # the patient
    assert [r["id"] for r in mine] == [made["id"]] and mine[0]["reason"] == "Chest pain on exertion"


def test_the_clinic_accepts_and_everyone_sees_the_new_state(c):
    made = issued(c)
    r = c.decide(made["id"], "accepted")
    assert r.status_code == 200 and json.loads(r.content)["referral"]["state"] == "accepted"
    for seen in (c.sent(), c.incoming(), c.mine()):
        assert seen[0]["state"] == "accepted" and seen[0]["decided_at"]


def test_asking_for_more_information_or_declining_needs_a_reason(c):
    made = issued(c)
    for outcome in ("needs_info", "declined"):
        assert c.decide(made["id"], outcome, "").status_code == 422
        assert c.decide(made["id"], outcome, "   ").status_code == 422
    r = c.decide(made["id"], "needs_info", "Please attach the ECG")
    assert json.loads(r.content)["referral"]["decision_reason"] == "Please attach the ECG"
    # still open: the clinic may decide again once the information arrives
    assert c.decide(made["id"], "accepted").status_code == 200


def test_a_decided_referral_cannot_be_decided_again(c):
    made = issued(c)
    assert c.decide(made["id"], "declined", "Not our speciality").status_code == 200
    assert c.decide(made["id"], "accepted").status_code == 409


def test_unknown_decisions_are_refused(c):
    made = issued(c)
    assert c.decide(made["id"], "maybe").status_code == 422
    assert c.decide(made["id"], "").status_code == 422


# ------------------------------------------------------------------ who may do what
def test_only_a_doctor_with_a_care_relationship_can_refer_that_patient(c):
    assert c.refer(subject="doc2").status_code == 403         # never saw this patient
    assert c.refer(subject="ops").status_code == 403          # a receptionist is not a doctor
    assert c.refer(subject="pat").status_code == 403          # nor is the patient
    assert c.sent("doc2") == [] and c.incoming() == []


def test_other_doctors_do_not_see_a_referral_they_did_not_write(c):
    issued(c)
    assert c.sent("doc2") == []


def test_only_the_receiving_clinic_staff_can_decide(c):
    made = issued(c)
    for who in ("doc", "doc2", "pat"):
        assert c.decide(made["id"], "accepted", subject=who).status_code == 403
    assert c.sent()[0]["state"] == "issued"


def test_the_incoming_list_is_for_staff_only(c):
    issued(c)
    for who in ("doc", "pat"):
        assert c.get("referrals/incoming", subject=who).status_code == 403


def test_a_patient_sees_only_their_own_referrals(c):
    issued(c)
    _, _ = c.patient("other")
    assert c.mine("other") == []


def test_another_customer_cannot_see_or_decide_a_referral(c):
    made = issued(c)
    from tests.test_api import Web
    outsider = Web()
    outsider.staff("ops")
    assert outsider.get("referrals/incoming", subject="ops").status_code == 200
    assert json.loads(outsider.get("referrals/incoming", subject="ops").content)["referrals"] == []
    assert outsider.post(f"referrals/{made['id']}/decide", {"decision": "accepted"},
                         subject="ops").status_code == 404


# ------------------------------------------------------------------ input rules
def test_bad_input_is_refused_and_nothing_is_created(c):
    assert c.refer(reason="").status_code == 422
    assert c.refer(reason="x" * 501).status_code == 422
    assert c.refer(urgency="whenever").status_code == 422
    assert c.refer(service="nosuchservice0000000000").status_code == 404
    assert c.refer(patient="nosuchperson000000000").status_code == 403
    assert c.sent() == []


def test_a_switched_off_service_cannot_be_referred_to(c):
    assert c.post(f"services/{c.cardiology}/status", {"status": "inactive"},
                  subject="ops").status_code == 200
    assert c.refer().status_code == 422


def test_the_same_patient_cannot_be_referred_twice_to_one_service_while_it_is_open(c):
    first = issued(c)
    assert c.refer().status_code == 409
    c.decide(first["id"], "declined", "Wrong clinic")
    assert c.refer().status_code == 201                       # closed: a new one is fine


# ------------------------------------------------------------------ patient notifications
def test_the_patient_is_told_when_a_referral_is_sent_and_decided(c):
    made = issued(c)
    relay_once(dispatch.dispatch)
    c.decide(made["id"], "accepted")
    relay_once(dispatch.dispatch)
    titles = [n["title"] for n in json.loads(c.get("notifications", subject="pat").content)[
        "notifications"] if n["kind"].startswith("referral")]
    assert titles == ["Referral accepted", "Referral sent"]


def test_each_step_is_told_as_it_happened_even_if_the_worker_is_behind(c):
    made = issued(c)
    c.decide(made["id"], "needs_info", "Please attach the ECG")
    c.decide(made["id"], "accepted")
    for _ in range(3):                                  # the worker catches up, one step per pass
        relay_once(dispatch.dispatch)
    titles = sorted(n["title"] for n in json.loads(c.get("notifications", subject="pat").content)[
        "notifications"] if n["kind"].startswith("referral"))
    assert titles == ["Referral accepted", "Referral needs more information", "Referral sent"]


def test_the_events_carry_no_reason_text(c):
    from foundation.cell_context import tenant_transaction
    from foundation.models import OutboxEvent
    issued(c, reason="Very private reason text")
    with tenant_transaction(c.clinic.ctx):
        payloads = [json.dumps(e.payload) for e in OutboxEvent.objects.filter(
            event_type__startswith="referral.")]
    assert payloads and not any("private" in p for p in payloads)
