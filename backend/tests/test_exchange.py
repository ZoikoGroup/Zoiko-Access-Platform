"""The status exchange between a clinic and a partner: references and status only (DEV-163)."""

import json
import os
from typing import Any

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
if not django.apps.apps.ready:
    django.setup()

from django.test import Client

from foundation import fhir
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
        header: dict[str, Any] = {
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


def give_profile(clinic, name="Asha Rao", birth="1990-04-12"):
    r = clinic.post(
        f"people/{clinic.person}/profile", {"name": name, "birth_date": birth}, subject="pat"
    )
    assert r.status_code in (200, 201), r.content


def agree(clinic, request_id, step="agree", subject="pat"):
    return clinic.post(f"exchange/mine/{request_id}/{step}", {}, subject=subject)


def made(clinic, lab, consent=True, **over):
    """Send a request; by default the patient has already agreed to share their name."""
    r = send(clinic, lab, **over)
    assert r.status_code == 201, r.content
    request_id = body(r)["request"]["id"]
    if consent:
        give_profile(clinic)
        assert agree(clinic, request_id).status_code == 200
    return request_id


def review_result(clinic, request_id, version=1, subject="doc"):
    """A doctor marks the result reviewed, naming the version they read (DEV-373)."""
    return clinic.post(f"exchange/requests/{request_id}/review", {"version": version},
                       subject=subject)


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
        {"token": "9434765919"},  # DEV-222: an NHS number
        {"token": "NHS-943-476-5919"},
        {"label": "Bloods for 943 476 5919"},
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
    assert review_result(clinic, request_id).status_code == 409  # nothing to review yet
    deliver_all_the_way(lab, request_id)
    assert review_result(clinic, request_id, subject="ops").status_code == 403  # staff cannot
    boss = clinic.login("boss", trust.ActorType.FACILITY_USER, "R13")
    assert boss and review_result(clinic, request_id, subject="boss").status_code == 403
    assert review_result(clinic, request_id, subject="doc2").status_code == 403  # no relationship
    done = review_result(clinic, request_id)
    assert body(done)["request"]["state"] == "reviewed"
    assert review_result(clinic, request_id).status_code == 200  # repeating is harmless


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
            ("consent_granted", "patient"),
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
    review_result(clinic, request_id, subject="doc")
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
    review_result(clinic, request_id, subject="doc")
    assert attention(clinic) == 0


# ------------------------------------------------------------------ telling the patient
def notices(clinic):
    from tests.test_notifications import inbox_of

    return inbox_of(clinic)["notifications"]


def test_the_patient_is_told_once_when_a_doctor_reviews_a_result(world):
    from tests.test_notifications import deliver

    _, clinic, lab = world
    deliver()  # setting the clinic up announced its new times
    request_id = made(clinic, lab)
    deliver()  # sending it asked the patient for their agreement (a separate notice)
    before = len(notices(clinic))
    deliver_all_the_way(lab, request_id)
    deliver()
    assert len(notices(clinic)) == before  # a result being ready tells the patient nothing yet
    review = f"exchange/requests/{request_id}/review"
    assert clinic.post(review, {"version": 1}, subject="doc").status_code == 200
    clinic.post(review, {"version": 1}, subject="doc")  # repeating it must not tell them twice
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
    assert review_result(clinic, request_id, subject="ops").status_code == 403
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
        # DEV-193: open partner requests need a plan for the patients concerned.
        {
            "action": action,
            "reason": "Unpaid and unreachable",
            "continuity_plan": "The clinic's own team calls each patient and books another lab.",
        },
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


# ---------------------------------------------- the patient's agreement (DEV-226)
def partner_row(lab, request_id):
    rows = body(lab.call("get", "exchange/incoming"))["requests"]
    return next(r for r in rows if r["id"] == request_id)


def test_without_the_patients_agreement_the_partner_learns_nothing_and_cannot_proceed(world):
    _, clinic, lab = world
    request_id = made(clinic, lab, consent=False)
    row = partner_row(lab, request_id)
    assert row["consent"] == "pending" and row["identity"] is None
    for name in ("accept", "start"):
        assert step(lab, request_id, name).status_code == 409
    assert clinic.person not in json.dumps(row)


def test_after_agreeing_the_partner_sees_only_name_and_birth_date_for_that_order(world):
    _, clinic, lab = world
    give_profile(clinic, "Asha Rao", "1990-04-12")
    request_id = made(clinic, lab, consent=False)
    done = agree(clinic, request_id)
    assert done.status_code == 200 and body(done)["request"]["consent"] == "granted"
    row = partner_row(lab, request_id)
    assert row["identity"] == {"name": "Asha Rao", "birth_date": "1990-04-12"}
    assert set(row["identity"]) == {"name", "birth_date"} and clinic.person not in json.dumps(row)
    other = made(clinic, lab, consent=False, token="ORD-0099")
    assert partner_row(lab, other)["identity"] is None  # another order is not covered
    assert step(lab, request_id, "accept").status_code == 200


def test_the_name_is_not_stored_in_plain_text(world):
    from django.db import connection

    _, clinic, lab = world
    give_profile(clinic, "Zuleikha Brandtmann", "1981-02-03")
    request_id = made(clinic, lab, consent=False)
    assert agree(clinic, request_id).status_code == 200
    with connection.cursor() as cur:
        cur.execute("select shared_identity_enc from exchange_exchangerequest")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "Zuleikha" not in stored and "1981" not in stored


def test_declining_shares_nothing_and_cancels_the_order(world):
    _, clinic, lab = world
    give_profile(clinic)
    request_id = made(clinic, lab, consent=False)
    done = agree(clinic, request_id, "decline")
    assert done.status_code == 200
    assert (
        body(done)["request"]["consent"] == "declined"
        and body(done)["request"]["state"] == "cancelled"
    )
    assert partner_row(lab, request_id)["identity"] is None
    assert agree(clinic, request_id).status_code == 409  # a refusal is not reversed quietly


def test_withdrawing_erases_what_was_shared_and_cancels_unfinished_work(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    assert partner_row(lab, request_id)["identity"]
    done = agree(clinic, request_id, "withdraw")
    assert done.status_code == 200 and body(done)["request"]["state"] == "cancelled"
    row = partner_row(lab, request_id)
    assert row["identity"] is None and row["consent"] == "withdrawn"
    assert agree(clinic, request_id, "withdraw").status_code == 200  # repeating is harmless


def test_an_agreement_ends_after_thirty_days_and_can_be_given_again(world):
    from datetime import timedelta

    from foundation.cell_context import tenant_transaction as tx
    from modules.exchange import models as xm
    from modules.exchange.models import ExchangeRequest

    platform, clinic, lab = world
    request_id = made(clinic, lab)
    with tx(platform.ctx):
        row = ExchangeRequest.objects.get(public_id=request_id)
        assert row.consent_expires_at is not None and row.consent_at is not None
        assert (row.consent_expires_at - row.consent_at) == timedelta(days=xm.CONSENT_DAYS)
        row.consent_expires_at = row.consent_at - timedelta(minutes=1)
        row.save(update_fields=["consent_expires_at"])
    shown = partner_row(lab, request_id)
    assert shown["consent"] == "expired" and shown["identity"] is None
    assert step(lab, request_id, "accept").status_code == 409
    assert agree(clinic, request_id).status_code == 200
    assert partner_row(lab, request_id)["identity"]


def test_only_the_patient_or_a_guardian_can_answer_not_staff_or_a_partner(world):
    _, clinic, lab = world
    request_id = made(clinic, lab, consent=False)
    assert agree(clinic, request_id, subject="ops").status_code in (403, 404)
    assert agree(clinic, request_id, subject="doc").status_code in (403, 404)
    assert lab.call("post", f"exchange/mine/{request_id}/agree").status_code in (403, 404)
    assert partner_row(lab, request_id)["identity"] is None


def test_a_patient_needs_a_name_on_file_before_agreeing(world):
    _, clinic, lab = world
    request_id = made(clinic, lab, consent=False)
    assert agree(clinic, request_id).status_code == 422


def test_the_patient_sees_what_they_are_asked_to_share_and_the_clinic_sees_the_state(world):
    _, clinic, lab = world
    give_profile(clinic)
    request_id = made(clinic, lab, consent=False)
    mine = body(clinic.get("exchange/mine", subject="pat"))["requests"][0]
    assert mine["consent"] == "pending"
    sent = body(clinic.get("exchange/requests", subject="ops"))["requests"][0]
    assert sent["consent"] == "pending" and "identity" not in sent
    agree(clinic, request_id)
    assert (
        body(clinic.get("exchange/requests", subject="ops"))["requests"][0]["consent"] == "granted"
    )


def test_the_worker_erases_the_stored_name_once_the_agreement_has_run_out(world):
    from datetime import timedelta

    from django.db import connection

    from foundation.cell_context import tenant_transaction as tx
    from modules.exchange import jobs
    from modules.exchange.models import ExchangeRequest

    platform, clinic, lab = world
    give_profile(clinic, "Marguerite Delacroix", "1975-06-07")
    old = made(clinic, lab, consent=False)
    fresh = made(clinic, lab, consent=False, token="ORD-0077")
    assert agree(clinic, old).status_code == 200 and agree(clinic, fresh).status_code == 200
    with tx(platform.ctx):
        row = ExchangeRequest.objects.get(public_id=old)
        assert row.consent_at is not None
        row.consent_expires_at = row.consent_at - timedelta(minutes=1)
        row.save(update_fields=["consent_expires_at"])
    assert jobs.erase_expired_all() == 1
    assert jobs.erase_expired_all() == 0  # nothing left to do
    with connection.cursor() as cur:
        cur.execute("select public_id, shared_identity_enc from exchange_exchangerequest")
        stored = {p: e for p, e in cur.fetchall()}
    assert stored[old] == "" and stored[fresh] != ""
    assert partner_row(lab, fresh)["identity"] and partner_row(lab, old)["identity"] is None
    history = body(clinic.get(f"exchange/requests/{old}/history", subject="ops"))["history"]
    assert history[-1]["what"] == "identity_erased"


def test_the_patient_is_told_once_that_a_request_needs_their_agreement(world):
    from tests.test_notifications import deliver

    _, clinic, lab = world
    deliver()
    made(clinic, lab, consent=False)
    deliver()
    deliver()  # the worker running again must not tell them twice
    told = [n for n in notices(clinic) if n["kind"] == "exchange_consent"]
    assert len(told) == 1 and told[0]["title"] == "A request needs your agreement"
    text = json.dumps(told[0])
    assert "Full blood count" not in text and "ORD-0001" not in text and lab.tenant_name not in text
    assert "name and date of birth" in told[0]["body"]


def test_a_request_held_for_three_days_is_escalated_to_the_administrator(world):
    from datetime import timedelta

    from foundation.cell_context import tenant_transaction as tx
    from modules.exchange import jobs
    from modules.exchange.models import HOLD_ESCALATE_DAYS, ExchangeRequest
    from modules.trust import api as trust_api

    platform, clinic, lab = world
    clinic.login("boss", trust_api.ActorType.FACILITY_USER, "R13")
    young, old = made(clinic, lab), made(clinic, lab, token="ORD-0050")
    _suspend(platform, lab)

    def attention(subject):
        return body(clinic.get("staff/dashboard", subject=subject))["attention"]

    assert HOLD_ESCALATE_DAYS == 3 and jobs.escalate_holds_all() == 0  # just held: too early
    with tx(platform.ctx):
        row = ExchangeRequest.objects.get(public_id=old)
        assert row.on_hold_at is not None
        row.on_hold_at = row.on_hold_at - timedelta(days=3, minutes=1)
        row.save(update_fields=["on_hold_at"])
    assert jobs.escalate_holds_all() == 1 and jobs.escalate_holds_all() == 0  # once only
    assert attention("boss")["partner_hold_escalated"] == 1  # the administrator's number
    assert attention("ops")["partner_hold_escalated"] == 0  # not shown to other staff
    rows = {r["id"]: r for r in body(clinic.get("exchange/requests", subject="ops"))["requests"]}
    assert rows[old]["hold_escalated"] is True and rows[young]["hold_escalated"] is False
    hist = body(clinic.get(f"exchange/requests/{old}/history", subject="ops"))["history"]
    assert hist[-1]["what"] == "hold_escalated"
    _suspend(platform, lab, "reinstate")  # released: no longer escalated
    assert attention("boss")["partner_hold_escalated"] == 0


# ---------------------------------------- a result summary and the lab's rating (DEV-307)
SUMMARY = "Haemoglobin 82 g/L (low). Platelets and white cells within range."


def delivered_with(clinic, lab, flag="abnormal", summary=SUMMARY, **over):
    request_id = made(clinic, lab, **over)
    for name in ("accept", "start"):
        assert step(lab, request_id, name).status_code == 200
    r = step(lab, request_id, "deliver", result_ref="RES-0042", summary=summary, flag=flag)
    assert r.status_code == 200, r.content
    return request_id


def result(clinic, request_id, subject="doc"):
    return clinic.get(f"exchange/requests/{request_id}/result", subject=subject)


def test_a_treating_doctor_opens_the_summary_and_the_rating(world):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, "critical")
    got = body(result(clinic, request_id))["result"]
    assert (
        got["summary"] == SUMMARY and got["flag"] == "critical" and got["result_ref"] == "RES-0042"
    )
    row = body(clinic.get("exchange/requests", subject="doc"))["requests"][0]
    assert row["result_flag"] == "critical" and row["has_summary"] is True
    assert "Haemoglobin" not in json.dumps(row)  # the list says there is one, not what


def test_nobody_else_can_open_the_summary(world):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab)
    assert result(clinic, request_id, "ops").status_code == 403  # clinic staff
    assert result(clinic, request_id, "doc2").status_code == 403  # a doctor not treating them
    assert result(clinic, request_id, "pat").status_code == 403  # the patient
    assert lab.call("get", f"exchange/requests/{request_id}/result").status_code == 403


def test_the_summary_is_stored_encrypted_and_never_leaks_into_other_views(world):
    from django.db import connection

    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, summary="Unmistakable sentinel zebra-quokka-5519")
    with connection.cursor() as cur:
        cur.execute("select result_summary_enc from exchange_exchangerequest")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "zebra" not in stored
    every = [
        clinic.get("exchange/requests", subject="ops"),
        clinic.get(f"exchange/requests/{request_id}/history", subject="ops"),
        clinic.get("exchange/mine", subject="pat"),
        lab.call("get", "exchange/incoming"),
        lab.call("get", f"exchange/requests/{request_id}/history"),
    ]
    assert all("zebra" not in r.content.decode() for r in every)


def test_a_summary_needs_a_valid_rating_and_a_sensible_length(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    for name in ("accept", "start"):
        step(lab, request_id, name)
    for extra in (
        {"summary": SUMMARY},
        {"summary": SUMMARY, "flag": "urgent!"},
        {"flag": "huge"},
        {"summary": "x" * 2001, "flag": "normal"},
        {"summary": 5, "flag": "normal"},
    ):
        assert step(lab, request_id, "deliver", result_ref="RES-0042", **extra).status_code == 422
    assert step(lab, request_id, "deliver", result_ref="RES-0042", flag="normal").status_code == 200
    assert result(clinic, request_id).status_code == 404  # a rating with no summary


def test_every_time_a_doctor_opens_a_summary_it_is_recorded_without_the_text(world):
    from foundation.cell_context import tenant_transaction as tx
    from modules.evidence.models import AuditEvent

    _, clinic, lab = world
    request_id = delivered_with(clinic, lab)
    result(clinic, request_id)
    result(clinic, request_id)
    with tx(clinic.clinic.ctx):
        # once in the clinic's own trail, once in the platform's (the test login sees both)
        reads = AuditEvent.objects.filter(
            action="exchange.result_read", tenant_id=clinic.clinic.ctx.tenant_id
        ).count()
        dump = json.dumps(list(AuditEvent.objects.values_list("details", flat=True)), default=str)
    assert reads == 2 and "Haemoglobin" not in dump


def test_a_result_that_is_not_delivered_has_no_summary_to_open(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    assert result(clinic, request_id).status_code == 404


# ------------------------------------------------ alerts by email (DEV-310)
def give_email(clinic, actor_ref, address, verified=True):
    from django.utils import timezone

    from modules.trust.models import Actor, Credential

    with tenant_transaction(clinic.clinic.ctx):
        Credential.objects.create(
            actor=Actor.objects.get(public_id=actor_ref),
            login=address,
            password_hash="x",
            email=address,
            email_verified_at=timezone.now() if verified else None,
        )


def mails(kind):
    from foundation.models import QueuedEmail

    return list(QueuedEmail.objects.filter(kind=kind))


def accepted(clinic, lab, **over):
    request_id = made(clinic, lab, **over)
    for name in ("accept", "start"):
        assert step(lab, request_id, name).status_code == 200
    return request_id


def test_a_critical_result_emails_the_person_who_ordered_it_once(world):
    _, clinic, lab = world
    give_email(clinic, clinic.desk, "desk@clinic.test")
    request_id = accepted(clinic, lab)
    deliver = dict(result_ref="RES-0042", summary=SUMMARY, flag="critical")
    assert step(lab, request_id, "deliver", **deliver).status_code == 200
    assert step(lab, request_id, "deliver", **deliver).status_code == 200  # a repeat
    sent = mails("critical_result")
    assert len(sent) == 1 and sent[0].to_address == "desk@clinic.test"
    text = sent[0].subject + sent[0].body
    assert "Haemoglobin" not in text and lab.tenant_name not in text and clinic.person not in text


def test_no_alert_for_a_normal_result_or_an_unconfirmed_address(world):
    _, clinic, lab = world
    give_email(clinic, clinic.desk, "desk@clinic.test", verified=False)
    first = accepted(clinic, lab)
    assert (
        step(
            lab, first, "deliver", result_ref="RES-0042", summary=SUMMARY, flag="critical"
        ).status_code
        == 200
    )  # unconfirmed
    second = accepted(clinic, lab, token="ORD-0300")
    assert (
        step(
            lab, second, "deliver", result_ref="RES-0043", summary=SUMMARY, flag="normal"
        ).status_code
        == 200
    )  # not critical
    assert mails("critical_result") == []


def test_the_administrator_is_emailed_once_a_day_about_requests_held_too_long(world):
    from datetime import timedelta

    from foundation.cell_context import tenant_transaction as tx
    from modules.comms import api as comms_api
    from modules.exchange import jobs
    from modules.exchange.models import ExchangeRequest
    from modules.trust import api as trust_api

    platform, clinic, lab = world
    boss = clinic.login("boss", trust_api.ActorType.FACILITY_USER, "R13")
    give_email(clinic, boss, "boss@clinic.test")
    old = made(clinic, lab)
    _suspend(platform, lab)
    with tx(platform.ctx):
        row = ExchangeRequest.objects.get(public_id=old)
        assert row.on_hold_at is not None
        row.on_hold_at = row.on_hold_at - timedelta(days=3, minutes=1)
        row.save(update_fields=["on_hold_at"])
    assert comms_api.send_escalation_alerts() == 0  # not escalated yet
    assert jobs.escalate_holds_all() == 1
    assert comms_api.send_escalation_alerts() == 1
    assert comms_api.send_escalation_alerts() == 0  # once a day
    sent = mails("hold_escalation")
    assert len(sent) == 1 and sent[0].to_address == "boss@clinic.test"
    assert "Haemoglobin" not in sent[0].body and lab.tenant_name not in sent[0].body


def test_a_delivered_message_a_critical_rating_and_an_opened_summary_never_count_as_review(world):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, "critical")
    assert result(clinic, request_id).status_code == 200  # opened, twice
    assert result(clinic, request_id).status_code == 200
    row = body(clinic.get("exchange/requests", subject="doc"))["requests"][0]
    assert row["state"] == "result_available" and row["reviewed_at"] is None
    # nobody but a treating doctor, using the review step itself, can mark it reviewed
    for who in ("ops", "doc2", "pat"):
        assert review_result(clinic, request_id, subject=who).status_code == 403
    assert lab.call("post", f"exchange/requests/{request_id}/review").status_code == 403
    after = body(clinic.get("exchange/requests", subject="doc"))["requests"][0]
    assert after["state"] == "result_available"
    assert review_result(clinic, request_id, subject="doc").status_code == 200


# ------------------------------------------------ the lab chooses its order system (DEV-323)
def test_a_lab_chooses_only_a_system_it_may_use_and_can_go_back_to_the_app(world):
    from modules.connector import api as connector
    from modules.connector.simulator import SimulatorAdapter

    _, clinic, lab = world
    if "simulator" not in connector.registered():
        connector.register(SimulatorAdapter())
    got = body(lab.call("get", "portal/order-system"))
    assert got["system"] == "" and "simulator" in [c["key"] for c in got["choices"]]
    assert lab.call("post", "portal/order-system", {"system": "no-such-system"}).status_code == 422
    done = lab.call("post", "portal/order-system", {"system": "simulator"})
    assert done.status_code == 200 and body(done)["system"] == "simulator"
    back = lab.call("post", "portal/order-system", {"system": ""})
    assert body(back)["system"] == ""
    assert clinic.get("portal/order-system", subject="ops").status_code == 403


# ------------------------------------------------ the patient's agreement sends the order (DEV-323)
def _lab_uses_simulator(lab):
    from modules.connector import api as connector
    from modules.connector.simulator import SimulatorAdapter

    if "simulator" not in connector.registered():
        connector.register(SimulatorAdapter())
    assert lab.call("post", "portal/order-system", {"system": "simulator"}).status_code == 200


def _orders(clinic, request_id):
    from modules.connector import api as connector

    with tenant_transaction(clinic.clinic.ctx):
        return [
            o
            for o in connector.list_operations(subject_ref=request_id)
            if o["operation_type"] == "exchange.order"
        ]


def test_a_coded_order_goes_to_the_labs_system_once_when_the_patient_agrees(world):
    _, clinic, lab = world
    _lab_uses_simulator(lab)
    give_profile(clinic)
    request_id = made(clinic, lab, consent=False, order_code="26604007")
    assert _orders(clinic, request_id) == []  # nothing before the agreement
    assert agree(clinic, request_id).status_code == 200
    assert agree(clinic, request_id).status_code == 200  # a repeat sends nothing new
    sent = _orders(clinic, request_id)
    assert len(sent) == 1 and sent[0]["connector_key"] == "simulator"


def test_what_is_sent_is_references_only_and_names_nobody(world):
    from modules.connector.models import ExternalOperation

    _, clinic, lab = world
    _lab_uses_simulator(lab)
    give_profile(clinic, "Zuleikha Brandtmann", "1981-02-03")
    request_id = made(clinic, lab, consent=False, order_code="26604007")
    agree(clinic, request_id)
    with tenant_transaction(clinic.clinic.ctx):
        op = ExternalOperation.objects.get(subject_ref=request_id)
        payload = dict(op.request)
        assert op.requested_by_ref == clinic.patient_actor  # who asked stays true
    assert set(payload) == {
        "request_ref",
        "order_no",
        "order_code",
        "kind",
        "urgency",
        "consent_at",
    }
    text = json.dumps(payload)
    assert "Zuleikha" not in text and "1981" not in text and clinic.person not in text


def test_no_order_is_sent_without_a_code_a_chosen_system_or_an_agreement(world):
    _, clinic, lab = world
    give_profile(clinic)
    uncoded = made(clinic, lab, consent=False)
    agree(clinic, uncoded)
    assert _orders(clinic, uncoded) == []  # no code: stays in the app
    coded = made(clinic, lab, consent=False, order_code="26604007", token="ORD-0400")
    agree(clinic, coded)
    assert _orders(clinic, coded) == []  # the lab chose no system
    _lab_uses_simulator(lab)
    declined = made(clinic, lab, consent=False, order_code="26604007", token="ORD-0401")
    agree(clinic, declined, "decline")
    assert _orders(clinic, declined) == []  # the patient said no


def test_a_bad_order_code_is_refused_when_the_clinic_sends(world):
    _, clinic, lab = world
    for bad in ("26604008", "abc", "1234", 5):
        assert send(clinic, lab, order_code=bad).status_code == 422, bad



# ------------------------------------------------ corrections and versions (DEV-325, FHIR step 5)
FIXED = "Haemoglobin 128 g/L (normal). The earlier figure was another sample."


def correct(lab, request_id, **over):
    data = dict(result_ref="RES-0043", summary=FIXED, flag="normal", correction="corrected")
    data.update(over)
    return step(lab, request_id, "correct", **data)


def test_a_correction_is_a_new_version_and_the_first_stays_readable(world):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, "abnormal")
    assert review_result(clinic, request_id, subject="doc").status_code == 200
    r = correct(lab, request_id)
    assert r.status_code == 200, r.content
    got = body(r)["request"]
    assert got["result_version"] == 2 and got["result_correction"] == "corrected"
    assert got["state"] == "result_available"  # reviewed before, so it must be reviewed again
    now = body(result(clinic, request_id))["result"]
    assert now["summary"] == FIXED and now["version"] == 2 and now["flag"] == "normal"
    [first] = now["earlier"]
    assert first["version"] == 1 and first["summary"] == SUMMARY and first["flag"] == "abnormal"
    assert first["reviewed"] is True and first["result_ref"] == "RES-0042"
    hist = body(clinic.get(f"exchange/requests/{request_id}/history", subject="ops"))["history"]
    assert hist[-1]["what"] == "correct"


def test_the_same_correction_twice_changes_nothing_and_a_second_one_is_version_3(world):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab)
    correct(lab, request_id)
    again = correct(lab, request_id)
    assert again.status_code == 200 and body(again)["request"]["result_version"] == 2
    third = correct(lab, request_id, summary=FIXED + " Added: ferritin normal.",
                    correction="appended")
    assert body(third)["request"]["result_version"] == 3
    earlier = body(result(clinic, request_id))["result"]["earlier"]
    assert [v["version"] for v in earlier] == [2, 1]


def test_only_a_delivered_result_can_be_corrected_and_bad_input_is_refused(world):
    _, clinic, lab = world
    request_id = made(clinic, lab)
    assert correct(lab, request_id).status_code == 409  # nothing delivered yet
    request_id = delivered_with(clinic, lab, token="ORD-0500")
    for bad in ({"flag": "huge"}, {"flag": None}, {"correction": "rewritten"},
                {"result_ref": "!"}, {"summary": "x" * 2001}):
        assert correct(lab, request_id, **bad).status_code == 422, bad
    assert body(result(clinic, request_id))["result"]["version"] == 1


def test_a_correction_to_critical_alerts_the_orderer(world):
    _, clinic, lab = world
    give_email(clinic, clinic.desk, "desk@clinic.test")
    request_id = delivered_with(clinic, lab, "normal")
    assert mails("critical_result") == []
    correct(lab, request_id, flag="critical", summary="Potassium 6.8 mmol/L.")
    assert len(mails("critical_result")) == 1


def test_earlier_versions_are_stored_encrypted(world):
    from django.db import connection

    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, summary="Unmistakable sentinel zebra-quokka-5519")
    correct(lab, request_id)
    with connection.cursor() as cur:
        cur.execute("select summary_enc from exchange_exchangeresultversion")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "zebra" not in stored



# ------------------------------------------------ a lab's FHIR report, end to end (DEV-326)
def _fhir_partner(world):
    """The clinic registers the lab's system as a signed FHIR sender (T-43), with Zoiko's checks
    done by the platform space this world already has."""
    from tests.test_fhir_api import partner

    platform, clinic, _ = world
    platform.sam = platform.staff("zsam", "R11")
    platform.tess = platform.staff("ztess", "R11")
    clinic._zoiko = platform
    clinic.login("boss", FAC, "R13")
    return partner(clinic)


def _report(request_id, rid="rep-901", status="final", flag="N", text="Haemoglobin 128 g/L."):
    return json.dumps({
        "resourceType": "DiagnosticReport",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "id": rid,
        "status": status,
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "26604007"}]},
        "subject": {"reference": "Patient/p-1"},
        "basedOn": [{"reference": f"ServiceRequest/{fhir.to_fhir_id(request_id)}"}],
        "conclusion": text,
        "conclusionCode": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": flag}]}],
    }).encode()


def test_a_labs_fhir_report_really_delivers_the_result_and_a_correction_versions_it(world):
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    r = send_fhir(sender, _report(request_id), "fhir-e2e-0001")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content
    got = body(result(clinic, request_id))["result"]
    assert got["summary"] == "Haemoglobin 128 g/L." and got["flag"] == "normal"
    r = send_fhir(sender, _report(request_id, "rep-902", "corrected", text="Haemoglobin 131 g/L."),
                  "fhir-e2e-0002")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content
    got = body(result(clinic, request_id))["result"]
    assert got["version"] == 2 and got["earlier"][0]["summary"] == "Haemoglobin 128 g/L."


def test_a_critical_fhir_report_emails_the_person_who_ordered_it(world):
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    give_email(clinic, clinic.desk, "desk@clinic.test")
    request_id = accepted(clinic, lab)
    raw = _report(request_id, flag="CRIT", text="Potassium 6.8 mmol/L.")
    assert send_fhir(sender, raw, "fhir-e2e-0003").json()["quarantined"] is False
    assert send_fhir(sender, raw, "fhir-e2e-0003").status_code == 202  # the same delivery again
    sent = mails("critical_result")
    assert len(sent) == 1 and sent[0].to_address == "desk@clinic.test"
    assert "Potassium" not in sent[0].subject + sent[0].body


def test_a_fhir_report_for_another_clinics_request_is_held(world):
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    r = send_fhir(sender, _report("req-of-nobody"), "fhir-e2e-0004")
    assert r.json()["quarantined"] is True and r.json()["error"] == "request_not_found"



# ------------------------------------------------ approved code mappings in the intake (DEV-327)
def _local_report(request_id, rid="rep-loinc-1"):
    raw = json.loads(_report(request_id, rid))
    raw["code"] = {"coding": [{"system": "http://loinc.org", "code": "57021-8",
                               "display": "CBC W Auto Differential panel"}]}
    return json.dumps(raw).encode()


def _approve_mapping(platform, system, code, snomed):
    from modules.codemap import api as codemap

    with tenant_transaction(platform.ctx):
        codemap.note_seen([{"system": system, "code": code, "display": ""}])
        [row] = [m for m in codemap.list_mappings() if m["source_code"] == code]
        codemap.propose(mapping_ref=row["id"], target_code=snomed, by="zsam-actor")
        codemap.approve(mapping_ref=row["id"], by="ziyer-actor")


def test_a_local_code_is_accepted_once_its_mapping_is_approved(world):
    from modules.evidence.models import AuditEvent
    from tests.test_fhir_api import send_fhir

    platform, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    held = send_fhir(sender, _local_report(request_id), "fhir-map-0001")
    assert held.json()["quarantined"] is True  # UK Core needs SNOMED CT; none approved yet
    _approve_mapping(platform, "http://loinc.org", "57021-8", "26604007")
    ok = send_fhir(sender, _local_report(request_id, "rep-loinc-2"), "fhir-map-0002")
    assert ok.json()["quarantined"] is False, ok.content
    assert body(result(clinic, request_id))["result"]["summary"] == "Haemoglobin 128 g/L."
    with tenant_transaction(clinic.clinic.ctx):
        assert AuditEvent.objects.filter(action="integration.fhir_code_mapped").count() == 1


def test_a_proposed_but_unapproved_mapping_is_never_used(world):
    from modules.codemap import api as codemap
    from tests.test_fhir_api import send_fhir

    platform, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    with tenant_transaction(platform.ctx):
        codemap.note_seen([{"system": "http://loinc.org", "code": "57021-8", "display": ""}])
        [row] = codemap.list_mappings()
        codemap.propose(mapping_ref=row["id"], target_code="26604007", by="zsam-actor")
    r = send_fhir(sender, _local_report(request_id), "fhir-map-0003")
    assert r.json()["quarantined"] is True


def _obs_x(oid, value, unit="g/L", low=115, high=165, flag="N", subject="Patient/p-1",
         code="26604007"):
    return {
        "resourceType": "Observation",
        "category": [fhir.LAB_VALUE_CATEGORY], "id": oid, "status": "final",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": code}],
                 "text": "Haemoglobin"},
        "subject": {"reference": subject},
        "valueQuantity": {"value": value, "unit": unit},
        "referenceRange": [{"low": {"value": low}, "high": {"value": high}}],
        "interpretation": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": flag}]}],
    }


def _bundle_of(report, *observations):
    report = {**report, "result": [{"reference": f"Observation/{o['id']}"} for o in observations]}
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": report}, *[{"resource": o} for o in observations]]}


# ------------------------------------------------ measured values of a result (DEV-330)
VALUES = [
    {"name": "Haemoglobin", "code": "26604007", "value": 128, "unit": "g/L", "low": 115,
     "high": 165, "flag": "normal"},
    {"name": "Blood group", "value": "O positive"},
]


def test_a_lab_sends_values_and_only_the_treating_doctor_reads_them(world):
    _, clinic, lab = world
    request_id = accepted(clinic, lab)
    r = step(lab, request_id, "deliver", result_ref="RES-0042", flag="normal", values=VALUES)
    assert r.status_code == 200, r.content
    row = body(clinic.get("exchange/requests", subject="doc"))["requests"][0]
    assert row["has_values"] is True and "Haemoglobin" not in json.dumps(row)
    got = body(result(clinic, request_id))["result"]
    assert got["values"][0]["value"] == 128 and got["values"][1]["value"] == "O positive"
    assert got["values"][1]["unit"] == "" and got["summary"] == ""  # values with no summary
    assert result(clinic, request_id, "ops").status_code == 403
    assert result(clinic, request_id, "pat").status_code == 403


def test_bad_values_are_refused(world):
    _, clinic, lab = world
    request_id = accepted(clinic, lab)
    for bad in ([{"name": "Hb"}], [{"value": 1}], [{"name": "Hb", "value": True}],
                [{"name": "Hb", "value": 1, "note": "free text"}],
                [{"name": "Hb", "value": 1, "flag": "weird"}],
                [{"name": "Hb", "value": 1, "code": "123"}],
                [{"name": "Hb", "value": 1}] * 51, "not a list"):
        r = step(lab, request_id, "deliver", result_ref="RES-0042", flag="normal", values=bad)
        assert r.status_code == 422, bad
    no_rating = step(lab, request_id, "deliver", result_ref="RES-0042", values=VALUES)
    assert no_rating.status_code == 422


def test_values_are_stored_encrypted_and_a_correction_keeps_the_old_ones(world):
    from django.db import connection

    _, clinic, lab = world
    request_id = accepted(clinic, lab)
    step(lab, request_id, "deliver", result_ref="RES-0042", flag="normal",
         values=[{"name": "Sentinel zebra-quokka", "value": 5519}])
    with connection.cursor() as cur:
        cur.execute("select result_values_enc from exchange_exchangerequest")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "zebra" not in stored and "5519" not in stored
    correct(lab, request_id, values=[{"name": "Haemoglobin", "value": 131, "unit": "g/L"}])
    got = body(result(clinic, request_id))["result"]
    assert got["values"][0]["value"] == 131
    assert got["earlier"][0]["values"][0]["name"] == "Sentinel zebra-quokka"


def test_a_labs_fhir_bundle_delivers_its_values(world):
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    report = json.loads(_report(request_id))
    bundle = _bundle_of(report, _obs_x("o1", 128), _obs_x("o2", 6.8, "mmol/L", 3.5, 5.3, "HH"))
    r = send_fhir(sender, json.dumps(bundle).encode(), "fhir-values-0001")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content
    got = body(result(clinic, request_id))["result"]
    assert [v["value"] for v in got["values"]] == [128, 6.8]
    assert got["values"][1]["flag"] == "critical" and got["values"][1]["unit"] == "mmol/L"



# ------------------------------------------------ the patient sees values after review (DEV-332)
def _reviewed_with_values(clinic, lab, hours_ago):
    from datetime import timedelta

    from modules.exchange.models import ExchangeRequest

    request_id = accepted(clinic, lab)
    step(lab, request_id, "deliver", result_ref="RES-0042", flag="normal", values=VALUES,
         summary="Clinician-only wording.")
    assert review_result(clinic, request_id, subject="doc").status_code == 200
    from modules.trust import api as trust_api

    platform = next(t for t in trust_api.list_tenants() if t["type"] == "platform")
    with tenant_transaction(trust_api.context_for(platform["public_id"])):
        row = ExchangeRequest.objects.get(public_id=request_id)
        assert row.reviewed_at is not None
        row.reviewed_at = row.reviewed_at - timedelta(hours=hours_ago)
        row.save(update_fields=["reviewed_at"])
    return request_id


def mine_values(clinic, request_id, subject="pat"):
    return clinic.get(f"exchange/mine/{request_id}/values", subject=subject)


def mine_trend(clinic, request_id, key, subject="pat"):
    return clinic.get(f"exchange/mine/{request_id}/trend?key={key}", subject=subject)



def test_the_patient_sees_values_a_day_after_the_doctor_reviewed_them(world):
    _, clinic, lab = world
    early = _reviewed_with_values(clinic, lab, hours_ago=2)
    assert mine_values(clinic, early).status_code == 404  # inside the day
    [row] = [r for r in body(clinic.get("exchange/mine"))["requests"] if r["id"] == early]
    assert row["values_ready"] is False
    late = _reviewed_with_values(clinic, lab, hours_ago=25)
    got = body(mine_values(clinic, late))["result"]
    assert got["values"][0]["name"] == "Haemoglobin" and "summary" not in got
    assert "Clinician-only" not in mine_values(clinic, late).content.decode()
    [row] = [r for r in body(clinic.get("exchange/mine"))["requests"] if r["id"] == late]
    assert row["values_ready"] is True


def test_values_wait_for_review_and_a_correction_waits_again(world):
    _, clinic, lab = world
    request_id = accepted(clinic, lab)
    step(lab, request_id, "deliver", result_ref="RES-0042", flag="normal", values=VALUES)
    assert mine_values(clinic, request_id).status_code == 404  # delivered, not reviewed
    shown = _reviewed_with_values(clinic, lab, hours_ago=30)
    assert mine_values(clinic, shown).status_code == 200
    correct(lab, shown, values=[{"name": "Haemoglobin", "value": 131}])
    assert mine_values(clinic, shown).status_code == 404  # the new version needs review


def test_nobody_but_the_patient_reads_their_values(world):
    _, clinic, lab = world
    request_id = _reviewed_with_values(clinic, lab, hours_ago=30)
    clinic.patient("other")
    assert mine_values(clinic, request_id, "other").status_code == 404
    assert mine_values(clinic, request_id, "ops").status_code == 404


def test_the_patient_sees_their_value_trend_for_reviewed_results(world):
    _, clinic, lab = world
    early = _reviewed_with_values(clinic, lab, hours_ago=2)
    late1 = _reviewed_with_values(clinic, lab, hours_ago=25)
    _reviewed_with_values(clinic, lab, hours_ago=50)            # a third, older result

    # A trend asked of one ready result gives points from all this patient's ready reviewed results
    # A value with a SNOMED CT code is found by its code (as for the doctor's trend, DEV-339) ...
    res = mine_trend(clinic, late1, "26604007")
    assert res.status_code == 200
    points = body(res)["points"]
    assert [p["value"] for p in points] == [128, 128]      # the two ready ones, not the early one
    # ... one without a code by its name
    assert [p["value"] for p in body(mine_trend(clinic, late1, "Blood group"))["points"]] == [
        "O positive", "O positive"]

    # Accessing trend with an unready / unreviewed result is 404
    assert mine_trend(clinic, early, "26604007").status_code == 404

    # Other roles or other patients cannot see this patient's trend
    clinic.patient("other_pat")
    assert mine_trend(clinic, late1, "26604007", "other_pat").status_code == 404
    assert mine_trend(clinic, late1, "26604007", "ops").status_code == 404



# ------------------------------------------------ held messages tried again (DEV-338)
def test_the_hourly_retry_takes_in_a_message_once_its_code_is_approved(world):
    from modules.integration.jobs_retry import retry_all_tenants
    from tests.test_fhir_api import send_fhir

    platform, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    held_id = send_fhir(sender, _local_report(request_id), "fhir-retry-0001").json()["id"]
    assert retry_all_tenants() == 0  # nothing approved yet: still held
    _approve_mapping(platform, "http://loinc.org", "57021-8", "26604007")
    assert retry_all_tenants() == 1
    assert body(result(clinic, request_id))["result"]["summary"] == "Haemoglobin 128 g/L."
    [row] = [i for i in clinic.get("staff/fhir/quarantine?state=released", subject="boss")
             .json()["items"] if i["public_id"] == held_id]
    assert row["decision_note"] == "approved code mapping"
    assert retry_all_tenants() == 0  # taken in once


def test_staff_can_try_again_and_a_message_still_wrong_stays_held(world):
    from tests.test_fhir_api import send_fhir

    platform, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    held_id = send_fhir(sender, _local_report(request_id), "fhir-retry-0002").json()["id"]
    again = clinic.post(f"staff/fhir/quarantine/{held_id}/retry", {}, subject="boss")
    assert again.status_code == 200 and again.json()["item"]["state"] == "held"
    _approve_mapping(platform, "http://loinc.org", "57021-8", "26604007")
    done = clinic.post(f"staff/fhir/quarantine/{held_id}/retry", {}, subject="boss")
    assert done.json()["item"]["state"] == "released"
    assert clinic.post(f"staff/fhir/quarantine/{held_id}/retry", {},
                       subject="boss").status_code == 409  # decided already
    assert clinic.post(f"staff/fhir/quarantine/{held_id}/retry", {},
                       subject="ops").status_code == 403


def test_values_with_local_codes_in_a_bundle_are_mapped_too(world):
    from tests.test_fhir_api import send_fhir

    platform, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    local = _obs_x("o1", 128)
    local["code"] = {"coding": [{"system": "http://loinc.org", "code": "718-7"}],
                     "text": "Haemoglobin"}
    bundle = _bundle_of(json.loads(_report(request_id)), local)
    first = send_fhir(sender, json.dumps(bundle).encode(), "fhir-retry-0003")
    assert first.json()["quarantined"] is True
    _approve_mapping(platform, "http://loinc.org", "718-7", "26604007")
    second = send_fhir(sender, json.dumps(bundle).encode(), "fhir-retry-0004")
    assert second.json()["quarantined"] is False, second.content
    assert body(result(clinic, request_id))["result"]["values"][0]["value"] == 128



# ------------------------------------------------ a value over time (DEV-339)
def _delivered(clinic, lab, token, hb, flag="normal"):
    request_id = made(clinic, lab, token=token)
    for name in ("accept", "start"):
        step(lab, request_id, name)
    step(lab, request_id, "deliver", result_ref=f"RES-{token}", flag=flag, values=[
        {"name": "Haemoglobin", "code": "26604007", "value": hb, "unit": "g/L", "low": 115,
         "high": 165},
        {"name": "Note", "value": "haemolysed" if hb < 100 else "fine"}])
    return request_id


def trend(clinic, request_id, key, subject="doc"):
    return clinic.get(f"exchange/requests/{request_id}/trend?key={key}", subject=subject)


def test_a_doctor_sees_a_value_across_the_patients_results_oldest_first(world):
    _, clinic, lab = world
    _delivered(clinic, lab, "ORD-0601", 98, "abnormal")
    _delivered(clinic, lab, "ORD-0602", 112)
    last = _delivered(clinic, lab, "ORD-0603", 131)
    got = body(trend(clinic, last, "26604007"))["points"]
    assert [p["value"] for p in got] == [98, 112, 131] and got[0]["unit"] == "g/L"
    by_name = body(trend(clinic, last, "note"))["points"]  # no code: matched by name
    assert [p["value"] for p in by_name] == ["haemolysed", "fine", "fine"]
    correct(lab, last, values=[{"name": "Haemoglobin", "code": "26604007", "value": 129}])
    assert [p["value"] for p in body(trend(clinic, last, "26604007"))["points"]] == [98, 112, 129]


def test_only_a_treating_doctor_sees_a_trend_and_the_key_is_checked(world):
    _, clinic, lab = world
    request_id = _delivered(clinic, lab, "ORD-0604", 120)
    assert trend(clinic, request_id, "26604007", "ops").status_code == 403
    assert trend(clinic, request_id, "26604007", "doc2").status_code == 403
    assert trend(clinic, request_id, "26604007", "pat").status_code == 403
    assert clinic.get(f"exchange/requests/{request_id}/trend", subject="doc").status_code == 422


# ------------------------------------------------ HL7 v2 lab results, end to end (DEV-340)
def _send_hl7(sender, text, delivery):
    import time

    from django.test import Client

    from modules.integration.delivery import signature_header

    raw = text.encode()
    header = signature_header(sender["secret"], sender["key_id"], int(time.time()), delivery, raw)
    return Client().post("/api/v1/hl7", raw, content_type="x-application/hl7-v2+er7",
                         HTTP_ZOIKO_SIGNATURE=header, HTTP_ZOIKO_DELIVERY_ID=delivery)


def test_a_labs_hl7_result_reaches_the_doctor_with_its_values(world):
    from tests.test_hl7 import oru

    _, clinic, lab = world
    sender = _fhir_partner(world)
    give_email(clinic, clinic.desk, "desk@clinic.test")
    request_id = accepted(clinic, lab)
    r = _send_hl7(sender, oru(placer=request_id), "hl7-e2e-0001")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content
    got = body(result(clinic, request_id))["result"]
    assert got["flag"] == "critical" and [v["value"] for v in got["values"]][:2] == [128, 6.8]
    assert got["summary"] == "Potassium very high; repeat advised."
    assert len(mails("critical_result")) == 1


def test_an_unreadable_hl7_message_is_held_and_local_codes_go_for_review(world):
    from modules.codemap import api as codemap
    from tests.test_hl7 import oru

    platform, clinic, lab = world
    sender = _fhir_partner(world)
    held = _send_hl7(sender, r"MSH|^~\&|A|B|C|D|2026||ADT^A01|1|P|2.5", "hl7-e2e-0002")
    assert held.json()["quarantined"] is True and held.json()["error"] == "invalid_hl7"
    request_id = accepted(clinic, lab)
    local = oru(placer=request_id, obr4="FBC^Full blood count^L",
                obx=["OBX|1|NM|HB^Haemoglobin^L||131|g/L|115-165|N|||F"])
    assert _send_hl7(sender, local, "hl7-e2e-0003").json()["quarantined"] is True
    with tenant_transaction(platform.ctx):
        listed = {m["source_code"] for m in codemap.list_mappings()}
    assert {"FBC", "HB"} <= listed and "N" not in listed  # rating codes are not listed


# ------------------------------------------------ a lab with several sites (DEV-341)
def test_the_order_is_sent_whichever_of_the_labs_sites_chose_the_system(world):
    from modules.trust import api as trust_api

    _, clinic, lab = world
    _lab_uses_simulator(lab)
    with tenant_transaction(lab.ctx):  # the choice is on the OTHER site this time
        trust_api.set_booking_system(lab.other_org, "simulator", by="system")
        trust_api.set_booking_system(lab.org, "", by="system")
    give_profile(clinic)
    request_id = made(clinic, lab, consent=False, order_code="26604007")
    agree(clinic, request_id)
    assert len(_orders(clinic, request_id)) == 1


def test_two_sites_with_different_systems_send_nothing_rather_than_guess(world):
    from modules.trust import api as trust_api

    _, clinic, lab = world
    _lab_uses_simulator(lab)
    with tenant_transaction(lab.ctx):
        trust_api.set_booking_system(lab.other_org, "another-system", by="system")
    give_profile(clinic)
    request_id = made(clinic, lab, consent=False, order_code="26604007")
    assert agree(clinic, request_id).status_code == 200  # the agreement itself stands
    assert _orders(clinic, request_id) == []


# ------------------------------------------------ the lab's PDF report (DEV-345)
def _pdf(extra=b""):
    return b"%PDF-1.4\n1 0 obj<< /Type /Catalog /Font /AAAAAB+Arial >>endobj\n" + extra + \
        b"\ntrailer<<>>\n%%EOF\n"


def upload(lab, request_id, data, name="report.pdf"):
    return lab.client.post(
        f"{API}exchange/incoming/{request_id}/attachment?filename={name}", data,
        content_type="application/pdf",
        HTTP_AUTHORIZATION=f"Bearer dev:{lab.tenant_public_id}:idp:{lab.subject}")


def files(clinic, request_id, subject="doc"):
    return clinic.get(f"exchange/requests/{request_id}/attachments", subject=subject)


@pytest.fixture
def scanner_on(settings):
    settings.ATTACHMENT_SCANNER = "pretend"


def test_the_lab_attaches_a_pdf_and_only_the_treating_doctor_downloads_it(world, scanner_on):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab)
    r = upload(lab, request_id, _pdf(), "FBC report.pdf")
    assert r.status_code == 201, r.content
    att = r.json()["attachment"]
    assert att["scan"] == "clean" and att["filename"] == "FBC report.pdf"
    assert [a["id"] for a in files(clinic, request_id).json()["attachments"]] == [att["id"]]
    got = clinic.get(f"exchange/requests/{request_id}/attachments/{att['id']}", subject="doc")
    assert got.status_code == 200 and got.content == _pdf()
    assert got["Content-Type"] == "application/pdf" and "attachment;" in got["Content-Disposition"]
    for who in ("ops", "doc2", "pat"):
        assert clinic.get(f"exchange/requests/{request_id}/attachments/{att['id']}",
                          subject=who).status_code == 403, who
    assert body(clinic.get("exchange/requests", subject="doc"))["requests"][0]["has_attachments"]


def test_a_file_with_a_virus_is_never_opened_and_nothing_opens_before_the_scan(world, settings):
    from modules.exchange.jobs import scan_attachments_all
    from modules.exchange.models import ResultAttachment

    _, clinic, lab = world
    request_id = delivered_with(clinic, lab)
    settings.ATTACHMENT_SCANNER = "pretend"
    from foundation.scanner import EICAR

    bad = upload(lab, request_id, _pdf(EICAR)).json()["attachment"]
    assert bad["scan"] == "infected"
    assert clinic.get(f"exchange/requests/{request_id}/attachments/{bad['id']}",
                      subject="doc").status_code == 409
    settings.ATTACHMENT_SCANNER = ""  # no scanner: the file waits
    waiting = upload(lab, request_id, _pdf(b"% second")).json()["attachment"]
    assert waiting["scan"] == "pending"
    assert clinic.get(f"exchange/requests/{request_id}/attachments/{waiting['id']}",
                      subject="doc").status_code == 409
    settings.ATTACHMENT_SCANNER = "pretend"
    assert scan_attachments_all() == 1
    assert clinic.get(f"exchange/requests/{request_id}/attachments/{waiting['id']}",
                      subject="doc").status_code == 200
    from modules.trust import api as trust_api

    plat = next(t for t in trust_api.list_tenants() if t["type"] == "platform")
    with tenant_transaction(trust_api.context_for(plat["public_id"])):
        assert ResultAttachment.objects.get(public_id=bad["id"]).content_enc == ""  # not kept


def test_only_a_safe_pdf_of_a_sensible_size_on_a_delivered_result(world, scanner_on):
    _, clinic, lab = world
    early = accepted(clinic, lab)
    assert upload(lab, early, _pdf()).status_code == 409  # nothing delivered yet
    request_id = delivered_with(clinic, lab, token="ORD-0801")
    for bad in (b"not a pdf at all", _pdf(b"/JavaScript (app.alert(1))"),
                _pdf(b"/OpenAction 5 0 R"), _pdf(b"/Launch"), b"%PDF-1.4 no end"):
        assert upload(lab, request_id, bad).status_code == 422, bad[:30]
    big = _pdf(b"%" + b"x" * (3 * 1024 * 1024))  # over Django's usual 2.5 MB, under ours
    assert upload(lab, request_id, big).status_code == 201
    assert upload(lab, request_id, _pdf(b"%" + b"x" * (5 * 1024 * 1024))).status_code == 422
    for i in range(4):
        assert upload(lab, request_id, _pdf(f"% {i}".encode())).status_code == 201
    assert upload(lab, request_id, _pdf(b"% sixth")).status_code == 409  # at most 5
    assert upload(lab, request_id, _pdf(), "x.pdf").status_code == 409


# ------------------------------------------------ the PDF inside a lab message (DEV-349)
def test_a_fhir_report_carrying_its_pdf_attaches_it_for_the_treating_doctor(world, scanner_on):
    import base64

    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    report = json.loads(_report(request_id))
    big = _pdf(b"%" + b"x" * (3 * 1024 * 1024))  # bigger than Django's usual 2.5 MB body
    report["presentedForm"] = [
        {"contentType": "application/pdf", "title": "FBC.pdf",
         "data": base64.b64encode(big).decode()},
        {"contentType": "application/pdf", "title": "bad.pdf",
         "data": base64.b64encode(_pdf(b"/JavaScript (x)")).decode()},
    ]
    r = send_fhir(sender, json.dumps(report).encode(), "fhir-pdf-0001")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content[:300]
    assert r.json()["attachments"] == 1 and r.json()["attachments_refused"] == 1
    [att] = files(clinic, request_id).json()["attachments"]
    assert att["filename"] == "FBC.pdf" and att["scan"] == "clean"
    got = clinic.get(f"exchange/requests/{request_id}/attachments/{att['id']}", subject="doc")
    assert got.content == big


def test_an_hl7_result_carrying_its_pdf_attaches_it(world, scanner_on):
    import base64

    from tests.test_hl7 import oru

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    data = base64.b64encode(_pdf()).decode()
    text = oru(placer=request_id) + (
        f"\rOBX|4|ED|PDF^Full report^L||^application^pdf^Base64^{data}||||||F")
    r = _send_hl7(sender, text, "hl7-pdf-0001")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content[:300]
    [att] = files(clinic, request_id).json()["attachments"]
    assert att["filename"] == "Full report.pdf"
    assert len(body(result(clinic, request_id))["result"]["values"]) == 3  # the PDF is not a value


# ------------------------------------------------------------------ preliminary results (DEV-370)
def test_a_preliminary_report_is_shown_labelled_and_can_never_be_marked_reviewed(world):
    """GHE-08 AT14 / H03: a preliminary result never satisfies "reviewed final", so it never reaches
    the patient as finished; the doctor still sees it, labelled."""
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    r = send_fhir(sender, _report(request_id, status="preliminary"), "fhir-pre-0001")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content
    got = body(result(clinic, request_id))["result"]
    assert got["preliminary"] is True and got["summary"] == "Haemoglobin 128 g/L."
    review = review_result(clinic, request_id, subject="doc")
    assert review.status_code == 409 and "preliminary" in review.content.decode()
    patient = next(x for x in mine(clinic) if x["id"] == request_id)
    assert patient["values_ready"] is False


def test_the_final_report_replaces_the_preliminary_one_and_can_then_be_reviewed(world):
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    send_fhir(sender, _report(request_id, "rep-p1", "partial", text="Partial: Hb 128."),
              "fhir-pre-0002")
    send_fhir(sender, _report(request_id, "rep-p2", "preliminary", text="Prelim: Hb 129."),
              "fhir-pre-0003")
    got = body(result(clinic, request_id))["result"]
    assert got["preliminary"] is True and got["summary"] == "Prelim: Hb 129."  # still not final
    r = send_fhir(sender, _report(request_id, "rep-f", "final", text="Haemoglobin 130 g/L."),
                  "fhir-pre-0004")
    assert r.status_code == 202 and r.json()["quarantined"] is False, r.content
    got = body(result(clinic, request_id))["result"]
    assert got["preliminary"] is False and got["correction"] == "final" and got["version"] == 3
    assert [v["preliminary"] for v in got["earlier"]] == [True, True]     # kept, as delivered
    assert clinic.post(f"exchange/requests/{request_id}/review", {"version": 3},
                       subject="doc").status_code == 200


def test_a_preliminary_critical_value_still_alerts_at_once(world):
    """Not holding preliminary results is deliberate: a dangerous value must not wait for the final
    report to reach the person who ordered the test."""
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    give_email(clinic, clinic.desk, "desk@clinic.test")
    request_id = accepted(clinic, lab)
    raw = _report(request_id, status="preliminary", flag="CRIT", text="Potassium 6.8 mmol/L.")
    assert send_fhir(sender, raw, "fhir-pre-0005").json()["quarantined"] is False
    assert len(mails("critical_result")) == 1


@pytest.mark.parametrize("status", ["unknown", ""])
def test_a_report_with_no_known_status_is_held_for_a_person(world, status):
    from tests.test_fhir_api import send_fhir

    _, clinic, lab = world
    sender = _fhir_partner(world)
    request_id = accepted(clinic, lab)
    r = send_fhir(sender, _report(request_id, status=status), f"fhir-pre-hold-{status or 'none'}")
    assert r.status_code == 202 and r.json()["quarantined"] is True


# ------------------------------------------------------------- a review names its version (DEV-373)
def test_a_review_must_name_the_version_the_doctor_read(world):
    """GHE-08 AT27: nobody can mark a result reviewed without saying which version they read, and a
    review of any version but the current one is refused."""
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, "abnormal")
    review = f"exchange/requests/{request_id}/review"
    for bad in ({}, {"version": "1"}, {"version": 0}, {"version": True}):
        assert clinic.post(review, bad, subject="doc").status_code == 422, bad
    assert clinic.post(review, {"version": 2}, subject="doc").status_code == 409


def test_a_correction_that_arrives_while_the_doctor_reads_cannot_be_reviewed_unseen(world):
    _, clinic, lab = world
    request_id = delivered_with(clinic, lab, "abnormal")       # the doctor opens version 1 ...
    correct(lab, request_id)                                    # ... and version 2 arrives
    review = f"exchange/requests/{request_id}/review"
    stale = clinic.post(review, {"version": 1}, subject="doc")
    assert stale.status_code == 409 and "version" in stale.content.decode()
    done = clinic.post(review, {"version": 2}, subject="doc")
    assert done.status_code == 200
    assert body(done)["request"]["reviewed_version"] == 2      # the evidence names the version
    assert clinic.post(review, {"version": 2}, subject="doc").status_code == 200   # harmless again
