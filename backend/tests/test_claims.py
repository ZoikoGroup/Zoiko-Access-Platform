"""Claims from a clinic to a payer: member id, service, amount and date only (DEV-308)."""

import datetime as dt
import json
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_exchange import Partner
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
TODAY = dt.date.today().isoformat()


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Payer(World):
    """A payer customer with one login (R07)."""

    def __init__(self, subject="pay"):
        super().__init__(tenant_type=trust.TenantType.PAYER)
        self.client = Client()
        self.subject = subject
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(
                actor_type=trust.ActorType.PAYER_USER, issuer="idp", subject=subject
            )
            trust.assign_role(
                actor_public_id=actor.public_id,
                organization_public_id=self.org,
                role_code="R07",
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
    with tenant_transaction(clinic.clinic.ctx):  # staff deal with patients the clinic has seen
        trust.link_person_to_site(clinic.person, clinic.clinic.org, "booking")
    payer = Payer()
    return platform, clinic, payer


def body(r):
    return json.loads(r.content)


def fields(clinic, pw, **over):
    return {
        "payer": pw.tenant_public_id,
        "patient": clinic.person,
        "member_id": "MEM-0001",
        "category": "general_physician",
        "label": "Consultation",
        "amount": 4500,
        "currency": "GBP",
        "service_date": TODAY,
        **over,
    }


def send(clinic, pw, subject="ops", **over):
    return clinic.post("claims/sent", fields(clinic, pw, **over), subject=subject)


def agree(clinic, claim_id, name="agree", subject="pat"):
    return clinic.post(f"claims/mine/{claim_id}/{name}", subject=subject)


def made(clinic, pw, consent=True, **over):
    """Send a claim; by default the patient has already agreed to it being sent."""
    r = send(clinic, pw, **over)
    assert r.status_code == 201, r.content
    claim_id = body(r)["claim"]["id"]
    if consent:
        assert agree(clinic, claim_id).status_code == 200
    return claim_id


def step(payer, claim_id, name, **extra):
    return payer.call("post", f"claims/incoming/{claim_id}/{name}", extra)


def incoming(payer, subject=None):
    return body(payer.call("get", "claims/incoming", subject=subject))["claims"]


def set_up_cover(payer, member_id="MEM-0001"):
    plan = payer.call(
        "post",
        "portal/plans",
        {
            "code": "GOLD",
            "name": "Gold plan",
            "covered_categories": ["general_physician"],
            "copay_percent": 20,
        },
    )
    assert plan.status_code == 201, plan.content
    got = payer.call("post", "portal/members", {"plan": "GOLD", "member_id": member_id})
    assert got.status_code == 201, got.content


# ------------------------------------------------------------------ what each side sees
def test_the_payer_sees_member_id_service_amount_and_date_and_never_the_patient(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    row = incoming(payer)[0]
    assert row["id"] == claim_id and row["member_id"] == "MEM-0001"
    assert (
        row["category"],
        row["label"],
        row["amount_minor"],
        row["currency"],
        row["service_date"],
    ) == ("general_physician", "Consultation", 4500, "GBP", TODAY)
    text = json.dumps(row)
    assert clinic.person not in text and "patient" not in text and row["state"] == "submitted"
    mine = body(clinic.get("claims/sent", subject="ops"))["claims"][0]
    assert mine["patient"] == clinic.person and mine["member_tail"] == "0001"
    assert "MEM-0001" not in json.dumps(mine)  # the clinic list shows only the tail


def test_the_member_id_is_stored_sealed(world):
    _, clinic, payer = world
    made(clinic, payer, member_id="ZQ-SENTINEL-8841")
    with connection.cursor() as cur:
        cur.execute("select member_enc from claim_claim")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "SENTINEL" not in stored


def test_the_clinic_sees_the_payers_it_may_claim_from(world):
    _, clinic, payer = world
    r = clinic.get("claims/payers", subject="ops")
    assert r.status_code == 200 and [p["id"] for p in body(r)["payers"]] == [payer.tenant_public_id]


# ------------------------------------------------------------------ the payer decides
def test_approve_then_pay_and_repeating_a_step_is_harmless(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    assert step(payer, claim_id, "pay", payment_ref="PAY-0001").status_code == 409  # not yet
    assert step(payer, claim_id, "approve").status_code == 200
    assert step(payer, claim_id, "approve").status_code == 200
    paid = step(payer, claim_id, "pay", payment_ref="PAY-0001")
    assert paid.status_code == 200 and body(paid)["claim"]["state"] == "paid"
    mine = body(clinic.get("claims/sent", subject="ops"))["claims"][0]
    assert mine["state"] == "paid" and mine["payment_ref"] == "PAY-0001"
    assert step(payer, claim_id, "decline", reason="other").status_code == 409


def test_a_decline_needs_one_of_the_fixed_reasons(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    for reason in (None, "because", "Not covered", ""):
        assert step(payer, claim_id, "decline", reason=reason).status_code == 422
    done = step(payer, claim_id, "decline", reason="not_covered")
    assert done.status_code == 200 and body(done)["claim"]["decision_reason"] == "not_covered"
    mine = body(clinic.get("claims/sent", subject="ops"))["claims"][0]
    assert mine["state"] == "declined" and mine["decision_reason"] == "not_covered"
    assert step(payer, claim_id, "approve").status_code == 409


def test_a_payment_needs_a_reference(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    step(payer, claim_id, "approve")
    for ref in (None, "", "x", "bad ref!"):
        assert step(payer, claim_id, "pay", payment_ref=ref).status_code == 422


def test_the_payer_runs_a_cover_check_on_its_own_plans(world):
    _, clinic, payer = world
    set_up_cover(payer)
    covered = made(clinic, payer)
    check = body(payer.call("get", f"claims/incoming/{covered}/check"))["check"]
    assert check["covered"] is True and check["plan"] == "GOLD"
    other = made(clinic, payer, member_id="MEM-9999", category="dental", service_date=TODAY)
    unknown = body(payer.call("get", f"claims/incoming/{other}/check"))["check"]
    assert unknown["covered"] is False and unknown["reason"] == "no_member"


# ------------------------------------------------------------------ the clinic
def test_the_clinic_may_withdraw_only_until_the_payer_decides(world):
    _, clinic, payer = world
    first, second = made(clinic, payer), made(clinic, payer, service_date="2026-01-05")
    done = clinic.post(f"claims/sent/{first}/withdraw", subject="ops")
    assert done.status_code == 200 and body(done)["claim"]["state"] == "withdrawn"
    assert clinic.post(f"claims/sent/{first}/withdraw", subject="ops").status_code == 200
    assert step(payer, first, "approve").status_code == 409  # withdrawn is final
    step(payer, second, "approve")
    assert clinic.post(f"claims/sent/{second}/withdraw", subject="ops").status_code == 409


def test_a_duplicate_open_claim_is_refused_but_a_withdrawn_one_can_be_resent(world):
    _, clinic, payer = world
    first = made(clinic, payer)
    assert send(clinic, payer).status_code == 409
    clinic.post(f"claims/sent/{first}/withdraw", subject="ops")
    assert send(clinic, payer).status_code == 201


@pytest.mark.parametrize(
    "over",
    [
        {"member_id": "x"},
        {"member_id": "bad id!"},
        {"category": "General"},
        {"label": "!!"},
        {"label": "Consultation 9434765919"},
        {"amount": 0},
        {"amount": -5},
        {"amount": 12.5},
        {"amount": "100"},
        {"amount": 100_000_001},
        {"currency": "gbp"},
        {"currency": "GB"},
        {"service_date": "yesterday"},
        {"service_date": "2999-01-01"},
        {"patient": ""},
    ],
)
def test_bad_claims_are_refused(world, over):
    _, clinic, payer = world
    assert send(clinic, payer, **over).status_code == 422, over


def test_an_unknown_payer_is_refused(world):
    _, clinic, payer = world
    assert send(clinic, payer, payer="nonexistent0000").status_code == 422
    assert send(clinic, payer, payer=clinic.clinic.tenant_public_id).status_code == 422


# ------------------------------------------------------------------ who may do what
def test_a_patient_and_a_doctor_cannot_send_or_list_claims(world):
    _, clinic, payer = world
    for who in ("pat", "doc"):
        assert send(clinic, payer, subject=who).status_code == 403
        assert clinic.get("claims/sent", subject=who).status_code == 403


def test_a_payer_cannot_use_the_clinic_routes_nor_a_clinic_the_payer_routes(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    assert payer.call("get", "claims/sent").status_code == 403
    assert payer.call("post", "claims/sent", fields(clinic, payer)).status_code == 403
    assert payer.call("post", f"claims/sent/{claim_id}/withdraw").status_code == 403
    assert clinic.get("claims/incoming", subject="ops").status_code == 403
    assert clinic.post(f"claims/incoming/{claim_id}/approve", subject="ops").status_code == 403


def test_another_payer_and_a_partner_see_and_change_nothing(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    rival = Payer("pay2")
    assert incoming(rival) == []
    assert step(rival, claim_id, "approve").status_code == 404
    assert rival.call("get", f"claims/incoming/{claim_id}/check").status_code == 404
    assert rival.call("get", f"claims/{claim_id}/history").status_code == 404
    lab = Partner("R05", "lab1")
    assert lab.call("get", "claims/incoming").status_code == 403
    assert lab.call("get", "claims/payers").status_code == 403


def test_another_clinic_cannot_withdraw_or_read_the_claim(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    other = Practice()
    assert other.post(f"claims/sent/{claim_id}/withdraw", subject="ops").status_code == 404
    assert other.get(f"claims/{claim_id}/history", subject="ops").status_code == 404
    assert body(other.get("claims/sent", subject="ops"))["claims"] == []


def test_both_sides_read_the_history_as_sides_not_names(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    step(payer, claim_id, "approve")
    step(payer, claim_id, "pay", payment_ref="PAY-0002")
    expected = [
        ("sent", "clinic"),
        ("consent_agree", "patient"),
        ("approve", "payer"),
        ("pay", "payer"),
    ]
    for r in (
        clinic.get(f"claims/{claim_id}/history", subject="ops"),
        payer.call("get", f"claims/{claim_id}/history"),
    ):
        assert [(s["what"], s["side"]) for s in body(r)["history"]] == expected


def test_a_patient_the_clinic_has_no_record_of_is_refused(world):
    _, clinic, payer = world
    assert send(clinic, payer, patient="no-such-patient-1").status_code == 403


# ------------------------------------------------ the patient agrees, claim by claim (DEV-311)
def test_the_payer_sees_nothing_until_the_patient_agrees(world):
    _, clinic, payer = world
    set_up_cover(payer)
    claim_id = made(clinic, payer, consent=False)
    assert incoming(payer) == []
    assert step(payer, claim_id, "approve").status_code == 404
    assert payer.call("get", f"claims/incoming/{claim_id}/check").status_code == 404
    assert payer.call("get", f"claims/{claim_id}/history").status_code == 404
    assert body(clinic.get("claims/sent", subject="ops"))["claims"][0]["consent"] == "pending"
    assert agree(clinic, claim_id).status_code == 200
    assert agree(clinic, claim_id).status_code == 200  # repeating is harmless
    assert [c["id"] for c in incoming(payer)] == [claim_id]
    assert step(payer, claim_id, "approve").status_code == 200


def test_the_patient_sees_what_would_be_sent_but_never_the_member_id(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer, consent=False)
    r = clinic.get("claims/mine", subject="pat")
    row = body(r)["claims"][0]
    assert row["id"] == claim_id and row["consent"] == "pending" and row["amount_minor"] == 4500
    assert (row["label"], row["currency"], row["service_date"]) == ("Consultation", "GBP", TODAY)
    assert row["payer"] == payer.tenant_name
    assert "MEM-0001" not in r.content.decode() and clinic.person not in r.content.decode()


def test_declining_cancels_the_claim_and_the_payer_never_sees_it(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer, consent=False)
    done = agree(clinic, claim_id, "decline")
    assert done.status_code == 200 and body(done)["claim"]["consent"] == "declined"
    mine = body(clinic.get("claims/sent", subject="ops"))["claims"][0]
    assert mine["state"] == "withdrawn" and mine["consent"] == "declined"
    assert incoming(payer) == []
    assert agree(clinic, claim_id).status_code == 409  # a refusal is not reversed
    assert send(clinic, payer).status_code == 201  # the clinic may ask again


def test_taking_the_agreement_back_cancels_an_undecided_claim_but_not_a_decided_one(world):
    _, clinic, payer = world
    first = made(clinic, payer)
    second = made(clinic, payer, service_date="2026-01-05")
    done = agree(clinic, first, "withdraw")
    assert done.status_code == 200 and body(done)["claim"]["consent"] == "withdrawn"
    assert incoming(payer)[0]["id"] == second  # the first one is gone
    assert body(clinic.get("claims/sent", subject="ops"))["claims"][1]["state"] == "withdrawn"
    step(payer, second, "approve")
    assert agree(clinic, second, "withdraw").status_code == 409  # too late: decided


def test_only_the_patient_can_answer_for_a_claim(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer, consent=False)
    for who in ("ops", "doc"):
        assert agree(clinic, claim_id, subject=who).status_code in (403, 404)
    clinic.patient("other")
    assert agree(clinic, claim_id, subject="other").status_code == 404
    assert payer.call("post", f"claims/mine/{claim_id}/agree").status_code in (403, 404)
    assert agree(clinic, claim_id, "delete").status_code == 404
    assert body(clinic.get("claims/mine", subject="other"))["claims"] == []
    assert incoming(payer) == []


# ------------------------------------------------ the patient is told a claim needs them (DEV-313)
def test_the_patient_is_told_once_that_a_claim_needs_their_agreement(world):
    from tests.test_notifications import deliver, inbox_of

    _, clinic, payer = world
    deliver()
    made(clinic, payer, consent=False)
    deliver()
    deliver()  # the worker running again must not tell twice
    told = [n for n in inbox_of(clinic)["notifications"] if n["kind"] == "claim_consent"]
    assert len(told) == 1 and told[0]["title"] == "A claim needs your agreement"
    text = json.dumps(told[0])
    assert payer.tenant_name not in text and "Consultation" not in text and "4500" not in text
    assert "My Claims" in told[0]["body"]


# ------------------------------------------------ paging the claim lists (DEV-314)
def pages(call, path, limit=2):
    seen, cursor = [], None
    for _ in range(10):
        r = call(path + f"?limit={limit}" + (f"&cursor={cursor}" if cursor else ""))
        assert r.status_code == 200, r.content
        data = body(r)
        seen += [c["id"] for c in data["claims"]]
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return seen


def test_both_claim_lists_page_with_a_cursor_that_belongs_to_one_list_and_login(world):
    _, clinic, payer = world
    ids = [made(clinic, payer, service_date=f"2026-01-0{i}") for i in range(1, 6)]
    sent = pages(lambda p: clinic.get(p, subject="ops"), "claims/sent")
    assert sent == list(reversed(ids))
    got = pages(lambda p: payer.call("get", p), "claims/incoming")
    assert got == list(reversed(ids))
    plain = body(clinic.get("claims/sent", subject="ops"))
    assert len(plain["claims"]) == 5 and "next_cursor" not in plain  # the old shape
    cursor = body(clinic.get("claims/sent?limit=2", subject="ops"))["next_cursor"]
    assert payer.call("get", f"claims/incoming?limit=2&cursor={cursor}").status_code == 422
    assert clinic.get("claims/sent?limit=0", subject="ops").status_code == 422
    assert clinic.get("claims/sent?limit=2&cursor=junk", subject="ops").status_code == 422


def test_the_payer_pages_only_claims_the_patient_agreed_to(world):
    _, clinic, payer = world
    for i in range(1, 4):
        made(clinic, payer, service_date=f"2026-02-0{i}")
    made(clinic, payer, service_date="2026-02-09", consent=False)
    got = pages(lambda p: payer.call("get", p), "claims/incoming")
    assert len(got) == 3


# ------------------------------------------------ payment advice (DEV-347)
def test_the_payer_records_what_was_paid_and_when_and_the_clinic_sees_it(world):
    _, clinic, payer = world
    claim_id = made(clinic, payer)
    step(payer, claim_id, "approve")
    claimed = body(clinic.get("claims/sent", subject="ops"))["claims"][0]["amount_minor"]
    paid = step(payer, claim_id, "pay", payment_ref="PAY-0002", amount_paid_minor=claimed - 500,
                paid_on="2026-11-18")
    assert paid.status_code == 200, paid.content
    mine = body(clinic.get("claims/sent", subject="ops"))["claims"][0]
    assert mine["amount_paid_minor"] == claimed - 500 and mine["paid_on"] == "2026-11-18"


def test_payment_advice_defaults_to_the_full_amount_today_and_bad_advice_is_refused(world):
    import datetime as dt

    _, clinic, payer = world
    claim_id = made(clinic, payer)
    step(payer, claim_id, "approve")
    claimed = body(clinic.get("claims/sent", subject="ops"))["claims"][0]["amount_minor"]
    tomorrow = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    for bad in ({"amount_paid_minor": claimed + 1}, {"amount_paid_minor": -1},
                {"amount_paid_minor": "100"}, {"amount_paid_minor": True},
                {"paid_on": tomorrow}, {"paid_on": "yesterday"}):
        assert step(payer, claim_id, "pay", payment_ref="PAY-0003", **bad).status_code == 422, bad
    done = body(step(payer, claim_id, "pay", payment_ref="PAY-0003"))["claim"]
    assert done["amount_paid_minor"] == claimed and done["paid_on"] == dt.date.today().isoformat()
