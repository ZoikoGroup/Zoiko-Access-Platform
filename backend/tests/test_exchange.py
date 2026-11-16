"""The status exchange between a clinic and a partner: references and status only (DEV-163)."""

import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
FAC = trust.ActorType.FACILITY_USER


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Partner(World):
    """A partner customer (lab, pharmacy or home care) with one login."""

    def __init__(self, role="R05", subject="lab1"):
        super().__init__(tenant_type=trust.TenantType.PARTNER)
        self.client = Client()
        self.subject = subject
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(actor_type=FAC, issuer="idp", subject=subject)
            trust.assign_role(
                actor_public_id=actor.public_id,
                organization_public_id=self.org,
                role_code=role,
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
    with tenant_transaction(clinic.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=clinic.doc,
            person_public_id=clinic.person,
            basis=trust.CareBasis.CARE_TEAM,
            organization_ref=clinic.clinic.org,
            by="system",
        )
    lab = Partner("R05", "lab1")
    return platform, clinic, lab


def body(r: Any) -> Any:
    return json.loads(r.content)


def send(clinic, lab, subject="ops", **over):
    fields = {
        "partner": lab.tenant_public_id,
        "kind": "lab_order",
        "label": "Full blood count",
        "token": "ORD-0001",
        "urgency": "routine",
        "patient": clinic.person,
        **over,
    }
    return clinic.post("exchange/requests", fields, subject=subject)


def made(clinic, lab, **over):
    r = send(clinic, lab, **over)
    assert r.status_code == 201, r.content
    return body(r)["request"]["id"]


def step(lab, request_id, name, **extra):
    return lab.call("post", f"exchange/incoming/{request_id}/{name}", extra)


def deliver_all_the_way(lab, request_id):
    for name, extra in (("accept", {}), ("start", {}), ("deliver", {"result_ref": "RES-0001"})):
        assert step(lab, request_id, name, **extra).status_code == 200


# ------------------------------------------------------------------ who may send
def test_a_clinic_sees_the_partners_it_may_send_to_and_nothing_else(world):
    _, clinic, lab = world
    r = clinic.get("exchange/partners", subject="ops")
    assert r.status_code == 200
    assert [p["id"] for p in body(r)["partners"]] == [lab.tenant_public_id]


@pytest.mark.parametrize("who", ["pat"])
def test_a_patient_cannot_use_the_exchange(world, who):
    _, clinic, lab = world
    assert clinic.get("exchange/partners", subject=who).status_code == 403
    assert send(clinic, lab, subject=who).status_code == 403


def test_a_partner_cannot_send_requests(world):
    _, clinic, lab = world
    assert lab.call("get", "exchange/partners").status_code == 403
    assert (
        lab.call("post", "exchange/requests", {"partner": lab.tenant_public_id}).status_code == 403
    )


def test_clinic_staff_and_a_doctor_with_a_relationship_may_send(world):
    _, clinic, lab = world
    assert send(clinic, lab, subject="ops").status_code == 201
    assert send(clinic, lab, subject="doc", token="ORD-0002").status_code == 201


def test_a_doctor_without_a_relationship_cannot_send_for_that_patient(world):
    _, clinic, lab = world
    assert send(clinic, lab, subject="doc2").status_code == 403


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "dentist"},
        {"token": "x"},
        {"token": "has space here"},
        {"label": ""},
        {"label": "<script>"},
        {"urgency": "whenever"},
        {"patient": ""},
        {"partner": "nobody"},
    ],
)
def test_a_badly_formed_request_is_refused_and_nothing_is_sent(world, over):
    _, clinic, lab = world
    assert send(clinic, lab, **over).status_code in (403, 422)
    assert body(clinic.get("exchange/requests", subject="ops"))["requests"] == []


def test_a_clinic_cannot_send_to_itself_or_to_a_non_partner(world):
    _, clinic, _ = world
    assert send(clinic, world[2], partner=clinic.clinic.tenant_public_id).status_code == 422


# ------------------------------------------------------------------ what each side sees
def test_the_clinic_sees_its_request_and_the_partner_sees_it_without_the_patient(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    mine = body(clinic.get("exchange/requests", subject="ops"))["requests"]
    assert [r["id"] for r in mine] == [request_id] and mine[0]["patient"] == clinic.person
    theirs = body(lab.call("get", "exchange/incoming"))["requests"]
    assert [r["id"] for r in theirs] == [request_id]
    text = json.dumps(theirs)
    assert clinic.person not in text and "patient" not in text  # never the patient
    assert theirs[0]["token"] == "ORD-0001" and theirs[0]["label"] == "Full blood count"


def test_nothing_clinical_is_stored_in_the_exchange(world):
    from modules.exchange.models import ExchangeRequest

    fields = {f.name for f in ExchangeRequest._meta.get_fields()}
    assert not fields & {
        "name",
        "email",
        "phone",
        "birth_date",
        "diagnosis",
        "result",
        "body",
        "notes",
    }


def test_another_partner_and_another_clinic_see_nothing_of_it(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    pharmacy = Partner("R06", "rx1")
    assert body(pharmacy.call("get", "exchange/incoming"))["requests"] == []
    assert step(pharmacy, request_id, "accept").status_code in (403, 404)
    other = Practice()
    assert body(other.get("exchange/requests", subject="ops"))["requests"] == []
    assert other.post(f"exchange/requests/{request_id}/cancel", subject="ops").status_code == 404
    assert other.get(f"exchange/requests/{request_id}/history", subject="ops").status_code == 404


def test_a_doctor_lists_only_requests_for_their_own_patients(world):
    _, clinic, lab = world
    made(clinic, lab)
    assert len(body(clinic.get("exchange/requests", subject="doc"))["requests"]) == 1
    assert body(clinic.get("exchange/requests", subject="doc2"))["requests"] == []


# ------------------------------------------------------------------ the partner's steps
def test_a_request_goes_accepted_in_progress_and_result_available_in_order(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    skipped = step(lab, request_id, "deliver", result_ref="RES-0001")
    assert skipped.status_code == 409  # no skipping ahead
    assert body(step(lab, request_id, "accept"))["request"]["state"] == "accepted"
    assert body(step(lab, request_id, "start"))["request"]["state"] == "in_progress"
    done = body(step(lab, request_id, "deliver", result_ref="RES-0001"))["request"]
    assert done["state"] == "result_available" and done["result_ref"] == "RES-0001"
    shown = body(clinic.get("exchange/requests", subject="ops"))["requests"][0]
    assert shown["state"] == "result_available" and shown["result_ref"] == "RES-0001"


def test_declining_needs_a_reason_and_is_final(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    assert step(lab, request_id, "decline").status_code == 422
    r = step(lab, request_id, "decline", note="We do not run that test")
    assert body(r)["request"]["state"] == "declined"
    assert step(lab, request_id, "accept").status_code == 409
    shown = body(clinic.get("exchange/requests", subject="ops"))["requests"][0]
    assert shown["decision_note"] == "We do not run that test"


def test_a_result_needs_a_plain_reference_and_repeating_a_step_is_harmless(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    step(lab, request_id, "accept")
    step(lab, request_id, "start")
    assert step(lab, request_id, "deliver").status_code == 422
    assert step(lab, request_id, "deliver", result_ref="no spaces allowed").status_code == 422
    assert step(lab, request_id, "deliver", result_ref="RES-0001").status_code == 200
    assert step(lab, request_id, "deliver", result_ref="RES-0001").status_code == 200


def test_only_the_role_that_handles_the_kind_of_request_may_answer(world):
    _, clinic, _ = world
    rx = Partner("R06", "rx1")
    request_id = made(clinic, rx, kind="lab_order")  # a lab order sent to a pharmacy
    assert step(rx, request_id, "accept").status_code == 403
    pharmacy_request = made(clinic, rx, kind="pharmacy_request", token="ORD-0002")
    assert step(rx, pharmacy_request, "accept").status_code == 200


def test_other_kinds_of_login_cannot_answer(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    with tenant_transaction(lab.ctx):
        actor = trust.register_actor(
            actor_type=trust.ActorType.PAYER_USER, issuer="idp", subject="payer1"
        )
        trust.assign_role(
            actor_public_id=actor.public_id,
            organization_public_id=lab.org,
            role_code="R07",
            by="system",
        )
    assert step(lab, request_id, "accept").status_code == 200  # the lab itself
    r = lab.call("post", f"exchange/incoming/{request_id}/start", {}, subject="payer1")
    assert r.status_code == 403
    assert lab.call("get", "exchange/incoming", subject="payer1").status_code == 403


# ------------------------------------------------------------------ the clinic's steps
def test_a_clinic_can_cancel_until_the_result_arrives_and_not_after(world):
    _, clinic, lab = world
    one = made(clinic, lab)
    r = clinic.post(f"exchange/requests/{one}/cancel", subject="ops")
    assert body(r)["request"]["state"] == "cancelled"
    assert step(lab, one, "accept").status_code == 409
    two = made(clinic, lab, token="ORD-0002")
    deliver_all_the_way(lab, two)
    assert clinic.post(f"exchange/requests/{two}/cancel", subject="ops").status_code == 409


def test_only_a_doctor_with_a_relationship_marks_a_result_reviewed(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    review = f"exchange/requests/{request_id}/review"
    assert clinic.post(review, subject="doc").status_code == 409  # nothing to review yet
    deliver_all_the_way(lab, request_id)
    assert clinic.post(review, subject="ops").status_code == 403  # staff cannot review
    boss = clinic.login("boss", trust.ActorType.FACILITY_USER, "R13")
    assert boss and clinic.post(review, subject="boss").status_code == 403
    assert clinic.post(review, subject="doc2").status_code == 403  # no relationship
    done = clinic.post(review, subject="doc")
    assert body(done)["request"]["state"] == "reviewed"
    assert clinic.post(review, subject="doc").status_code == 200  # repeating is harmless


def test_delivery_is_never_review(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    deliver_all_the_way(lab, request_id)
    shown = body(clinic.get("exchange/requests", subject="ops"))["requests"][0]
    assert shown["state"] == "result_available" and shown["reviewed_at"] is None


# ------------------------------------------------------------------ history and numbers
def test_both_sides_read_the_history_as_sides_not_names(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    deliver_all_the_way(lab, request_id)
    for who in (
        clinic.get(f"exchange/requests/{request_id}/history", subject="ops"),
        lab.call("get", f"exchange/requests/{request_id}/history"),
    ):
        steps = body(who)["history"]
        assert [(s["what"], s["side"]) for s in steps] == [
            ("sent", "clinic"),
            ("accept", "partner"),
            ("start", "partner"),
            ("deliver", "partner"),
        ]


def test_the_partner_home_numbers_count_its_requests(world):
    _, clinic, lab = world
    first = made(clinic, lab)
    made(clinic, lab, token="ORD-0002")
    deliver_all_the_way(lab, first)
    numbers = body(lab.call("get", "portal/dashboard"))
    assert numbers["requests"] == {"new": 1, "in_progress": 0, "delivered": 1}


# ------------------------------------------------------------------ the patient's own view
def mine(clinic, subject="pat"):
    r = clinic.get("exchange/mine", subject=subject)
    assert r.status_code == 200, r.content
    return body(r)["requests"]


def test_a_patient_sees_what_was_requested_for_them_and_how_far_it_has_got(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    first = mine(clinic)
    assert [(r["label"], r["partner"], r["state"]) for r in first] == [
        ("Full blood count", lab.tenant_name, "requested")
    ]
    for name, extra in (("accept", {}), ("start", {}), ("deliver", {"result_ref": "RES-0001"})):
        step(lab, request_id, name, **extra)
    assert mine(clinic)[0]["state"] == "result_available"  # ready, not yet read by a doctor
    clinic.post(f"exchange/requests/{request_id}/review", subject="doc")
    shown = mine(clinic)[0]
    assert shown["state"] == "reviewed" and shown["reviewed_at"]


def test_a_patient_is_never_shown_an_order_number_a_reference_or_a_reason(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    step(lab, request_id, "decline", note="We do not run that test")
    shown = mine(clinic)[0]
    text = json.dumps(shown)
    for secret in ("ORD-0001", "We do not run that test", clinic.person):
        assert secret not in text
    assert not {"token", "result_ref", "decision_note", "person", "patient"} & set(shown)


def test_another_patient_staff_and_a_partner_see_none_of_it(world):
    _, clinic, lab = world
    made(clinic, lab)
    clinic.patient("other")
    assert mine(clinic, "other") == []
    assert mine(clinic, "ops") == [] and mine(clinic, "doc") == []
    assert body(lab.call("get", "exchange/mine"))["requests"] == []


def test_a_patient_of_another_clinic_sees_nothing(world):
    _, clinic, lab = world
    made(clinic, lab)
    other = Practice()
    assert mine(other) == []


# ------------------------------------------------------------------ the clinic's dashboard
def attention(clinic):
    return body(clinic.get("staff/dashboard", subject="ops"))["attention"]["partner_results"]


def test_the_clinic_dashboard_counts_results_waiting_for_a_doctor(world):
    _, clinic, lab = world
    assert attention(clinic) == 0
    request_id = made(clinic, lab)
    assert attention(clinic) == 0  # not delivered yet
    deliver_all_the_way(lab, request_id)
    assert attention(clinic) == 1  # ready, nobody has read it
    clinic.post(f"exchange/requests/{request_id}/review", subject="doc")
    assert attention(clinic) == 0


# ------------------------------------------------------------------ telling the patient
def notices(clinic):
    from tests.test_notifications import inbox_of

    return inbox_of(clinic)["notifications"]


def test_the_patient_is_told_once_when_a_doctor_reviews_a_result(world):
    from tests.test_notifications import deliver

    _, clinic, lab = world
    deliver()  # setting the clinic up announced its new times
    before = len(notices(clinic))
    request_id = made(clinic, lab)
    deliver_all_the_way(lab, request_id)
    deliver()
    assert len(notices(clinic)) == before  # a result being ready tells the patient nothing yet
    review = f"exchange/requests/{request_id}/review"
    assert clinic.post(review, subject="doc").status_code == 200
    clinic.post(review, subject="doc")  # repeating it must not tell them twice
    deliver()
    fresh = [n for n in notices(clinic) if n["kind"] == "exchange_reviewed"]
    assert len(fresh) == 1
    assert fresh[0]["title"] == "Your doctor reviewed a result"
    assert "Full blood count" not in json.dumps(fresh[0]) and "ORD-0001" not in json.dumps(fresh[0])


def test_nothing_is_announced_when_review_is_refused(world):
    from tests.test_notifications import deliver

    _, clinic, lab = world
    request_id = made(clinic, lab)
    deliver_all_the_way(lab, request_id)
    assert clinic.post(f"exchange/requests/{request_id}/review", subject="ops").status_code == 403
    deliver()
    assert [n for n in notices(clinic) if n["kind"] == "exchange_reviewed"] == []


# ------------------------------------------------------------------ a customer suspended (DEV-169)
def _suspend(platform, customer, action="suspend"):
    """Support (sam) asks, a different person (tess) approves."""
    for who in ("sam", "tess"):
        if not hasattr(platform, who):
            setattr(platform, who, platform.staff(who, "R11"))

    def go(path, subject, data):
        return platform.client.post(
            API + path,
            json.dumps(data),
            content_type="application/json",
            **platform.header(platform.tenant_public_id, subject),
        )

    r = go(
        f"platform/customers/{customer.tenant_public_id}/suspension",
        "sam",
        {"action": action, "reason": "Unpaid and unreachable"},
    )
    assert r.status_code == 201, r.content
    done = go(f"platform/suspensions/{body(r)['id']}/approve", "tess", {})
    assert done.status_code == 200, done.content


def test_suspending_the_partner_puts_its_open_requests_on_hold_for_the_clinic(world):
    platform, clinic, lab = world
    open_id = made(clinic, lab, token="ORD-0010")
    delivered = made(clinic, lab, token="ORD-0011")
    deliver_all_the_way(lab, delivered)
    _suspend(platform, lab)
    rows = {r["id"]: r for r in body(clinic.get("exchange/requests", subject="ops"))["requests"]}
    assert rows[open_id]["on_hold"] is True and rows[delivered]["on_hold"] is False
    assert body(clinic.get("staff/dashboard", subject="ops"))["attention"]["partner_on_hold"] == 1
    history = body(clinic.get(f"exchange/requests/{open_id}/history", subject="ops"))["history"]
    assert history[-1]["what"] == "on_hold"


def test_the_clinic_can_cancel_a_held_request_and_it_stops_counting(world):
    platform, clinic, lab = world
    request_id = made(clinic, lab)
    _suspend(platform, lab)
    r = clinic.post(f"exchange/requests/{request_id}/cancel", {}, subject="ops")
    assert r.status_code == 200 and body(r)["request"]["state"] == "cancelled"
    assert body(clinic.get("staff/dashboard", subject="ops"))["attention"]["partner_on_hold"] == 0


def test_reinstating_the_partner_lets_held_requests_carry_on(world):
    platform, clinic, lab = world
    request_id = made(clinic, lab)
    _suspend(platform, lab)
    _suspend(platform, lab, "reinstate")
    rows = body(clinic.get("exchange/requests", subject="ops"))["requests"]
    assert rows[0]["on_hold"] is False
    assert step(lab, request_id, "accept").status_code == 200


def test_a_held_request_cannot_be_moved_by_the_partner(world):
    platform, clinic, lab = world
    request_id = made(clinic, lab)
    other = Partner("R05", "lab2")  # a second customer is unaffected
    assert other.call("get", "exchange/incoming").status_code == 200
    with tenant_transaction(platform.ctx):
        from modules.exchange import api as exchange

        assert exchange.hold_open_for(lab.tenant_public_id, actor_ref="a1") == 1
    assert step(lab, request_id, "accept").status_code == 409


def test_suspending_the_clinic_holds_its_requests_and_the_partner_is_told_nothing_more(world):
    platform, clinic, lab = world
    made(clinic, lab)
    _suspend(platform, clinic.clinic)
    rows = body(lab.call("get", "exchange/incoming"))["requests"]
    assert rows[0]["on_hold"] is True
    assert "suspend" not in json.dumps(rows).lower() and "patient" not in rows[0]


def test_only_open_requests_are_held_and_holding_is_not_repeated(world):
    platform, clinic, lab = world
    done = made(clinic, lab, token="ORD-0020")
    deliver_all_the_way(lab, done)
    declined = made(clinic, lab, token="ORD-0021")
    assert step(lab, declined, "decline", note="Not offered here").status_code == 200
    with tenant_transaction(platform.ctx):
        from modules.exchange import api as exchange

        assert exchange.hold_open_for(lab.tenant_public_id, actor_ref="a1") == 0


def test_a_held_request_is_assigned_to_the_person_who_sent_it(world):
    platform, clinic, lab = world
    made(clinic, lab, subject="ops")
    _suspend(platform, lab)
    mine = body(clinic.get("exchange/requests", subject="ops"))["requests"][0]
    other = body(clinic.get("exchange/requests", subject="doc"))["requests"][0]
    assert mine["hold_mine"] is True and mine["hold_owner_name"] == other["hold_owner_name"]
    assert other["hold_mine"] is False
    att = body(clinic.get("staff/dashboard", subject="ops"))["attention"]
    assert att["partner_held_mine"] == 1 and att["partner_on_hold"] == 1
    _suspend(platform, lab, "reinstate")
    assert body(clinic.get("staff/dashboard", subject="ops"))["attention"]["partner_held_mine"] == 0
