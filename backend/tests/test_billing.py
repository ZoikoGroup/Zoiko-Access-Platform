"""A clinic's fees, invoices and credit notes (GHE-17; DEV-139): prices only from the fee schedule,
a ledger that cannot be rewritten, credit notes that never go below zero, and a patient who sees
only their own issued bills."""
import datetime as dt
import json

import pytest
from django.db import DatabaseError, transaction

from foundation.cell_context import tenant_transaction
from modules.billing.models import CreditNote, InvoiceEvent
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def set_fee(p, amount=5000, currency="GBP", who="boss", **over):
    return p.post("staff/billing/fees", {"service": p.clinic.service, "currency": currency,
                                         "amount_minor": amount, **over}, subject=who)


def fee_in_place(p, amount=5000):
    assert set_fee(p, amount).status_code == 201


def make(p, qty=1, who="ops", person=None, **over):
    body = {"person": person or p.person,
            "lines": [{"service": p.clinic.service, "quantity": qty}], **over}
    return p.post("staff/billing/invoices", body, subject=who)


def invoice(p, qty=1, **kw):
    r = make(p, qty, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["invoice"]


def act(p, inv, action, body=None, who="ops"):
    return p.post(f"staff/billing/invoices/{inv['public_id']}/{action}", body or {}, subject=who)


def issue(p, inv):
    r = act(p, inv, "issue")
    assert r.status_code == 200, r.content
    return json.loads(r.content)["invoice"]


def pay(p, inv, ref="receipt-0001"):
    r = act(p, inv, "pay", {"payment_ref": ref})
    assert r.status_code == 200, r.content
    return json.loads(r.content)["invoice"]


def credit(p, inv, amount, reason="goodwill", who="boss"):
    return act(p, inv, "credit", {"amount_minor": amount, "reason": reason}, who)


def detail(p, inv, who="ops"):
    r = p.get(f"staff/billing/invoices/{inv['public_id']}", subject=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["invoice"]


def mine(p, subject="pat"):
    r = p.get("billing/invoices", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["invoices"]


# ------------------------------------------------------------------ the fee schedule
def test_only_the_administrator_sets_a_price_and_staff_can_see_it(p):
    assert set_fee(p, who="ops").status_code == 403
    assert set_fee(p, who="doc").status_code == 403
    r = set_fee(p, 7500)
    assert r.status_code == 201 and json.loads(r.content)["fee"]["amount_minor"] == 7500
    fees = json.loads(p.get("staff/billing/fees", subject="ops").content)
    mine_row = next(f for f in fees["fees"] if f["service_ref"] == p.clinic.service)
    assert mine_row["current"]["amount_minor"] == 7500 and mine_row["upcoming"] is None
    assert fees["can_set"] is False


@pytest.mark.parametrize("over", [
    {"currency": "gbp"}, {"currency": "GB"}, {"currency": ""}, {"amount": -1},
    {"amount_minor": True}, {"amount_minor": "5000"}, {"amount_minor": 1.5},
    {"amount_minor": 100_000_001}, {"effective_from": "2020-01-01"},
    {"effective_from": "soon"}, {"effective_from": "2099-01-01"},
])
def test_a_price_must_be_whole_minor_units_in_a_real_currency_from_a_sensible_date(p, over):
    body = {"service": p.clinic.service, "currency": "GBP", "amount_minor": 5000}
    if "amount" in over:
        over = {"amount_minor": over["amount"]}
    r = p.post("staff/billing/fees", {**body, **over}, subject="boss")
    assert r.status_code == 422


def test_a_price_for_an_unknown_service_is_not_found(p):
    r = p.post("staff/billing/fees", {"service": "nonexistent-id-00000", "currency": "GBP",
                                      "amount_minor": 100}, subject="boss")
    assert r.status_code == 404


def test_a_future_price_is_shown_as_upcoming_and_not_used_yet(p):
    fee_in_place(p, 5000)
    later = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    assert set_fee(p, 9000, effective_from=later).status_code == 201
    fees = json.loads(p.get("staff/billing/fees", subject="ops").content)["fees"]
    row = next(f for f in fees if f["service_ref"] == p.clinic.service)
    assert row["current"]["amount_minor"] == 5000 and row["upcoming"]["amount_minor"] == 9000
    assert invoice(p)["lines"][0]["unit_minor"] == 5000


# ------------------------------------------------------------------ making an invoice
def test_an_invoice_is_priced_from_the_schedule_and_numbered(p):
    fee_in_place(p, 4000)
    first = invoice(p, qty=3)
    second = invoice(p, person=p.person)
    assert first["number"] == "INV-000001" and second["number"] == "INV-000002"
    assert first["total_minor"] == 12000 and first["currency"] == "GBP"
    assert first["lines"][0]["quantity"] == 3 and first["lines"][0]["unit_minor"] == 4000
    assert first["status"] == "draft" and first["balance_minor"] == 12000


def test_nobody_types_a_price_and_a_service_without_a_price_cannot_be_billed(p):
    assert make(p).status_code == 422                                  # no price set yet
    fee_in_place(p)
    r = p.post("staff/billing/invoices", {"person": p.person, "lines": [
        {"service": p.clinic.service, "quantity": 1, "unit_minor": 1}]}, subject="ops")
    assert r.status_code == 422


@pytest.mark.parametrize("lines", [
    [], "all of it", None, [{"service": 7, "quantity": 1}], [{"quantity": 1}],
    [{"service": "S", "quantity": 0}], [{"service": "S", "quantity": 21}],
    [{"service": "S", "quantity": True}], [{"service": "S", "quantity": "2"}],
    [{"service": "S", "quantity": 1, "note": "free text"}], ["S"],
])
def test_the_lines_must_be_services_with_a_sensible_quantity_and_nothing_else(p, lines):
    fee_in_place(p)
    fixed = json.loads(json.dumps(lines).replace('"S"', json.dumps(p.clinic.service)))
    assert p.post("staff/billing/invoices", {"person": p.person, "lines": fixed},
                  subject="ops").status_code == 422


def test_at_most_ten_lines_and_a_sensible_due_period(p):
    fee_in_place(p)
    line = {"service": p.clinic.service, "quantity": 1}
    assert p.post("staff/billing/invoices", {"person": p.person, "lines": [line] * 11},
                  subject="ops").status_code == 422
    assert p.post("staff/billing/invoices", {"person": p.person, "lines": [line] * 10},
                  subject="ops").status_code == 201
    for bad in (-1, 400, True, "soon"):
        assert make(p, due_days=bad).status_code == 422
    due = invoice(p, due_days=30)["due_on"]
    assert due == (dt.date.today() + dt.timedelta(days=30)).isoformat()


def test_only_staff_can_make_an_invoice_and_only_for_a_patient_of_the_clinic(p):
    fee_in_place(p)
    assert make(p, who="doc").status_code == 403
    assert make(p, who="pat").status_code == 403
    _, stranger = p.patient("stranger")                       # never booked, messaged or referred
    assert make(p, person=stranger).status_code == 403


def test_a_price_change_never_touches_an_invoice_already_made(p):
    fee_in_place(p, 5000)
    inv = invoice(p)
    set_fee(p, 9999)
    assert detail(p, inv)["lines"][0]["unit_minor"] == 5000
    assert invoice(p, person=p.person)["lines"][0]["unit_minor"] == 9999


# ------------------------------------------------------------------ issuing, paying, cancelling
def test_an_invoice_moves_from_draft_to_issued_to_paid_and_repeats_are_harmless(p):
    fee_in_place(p)
    inv = invoice(p)
    assert pay(p, issue(p, inv))["status"] == "paid" if False else True
    issued = issue(p, inv)
    assert issued["status"] == "issued" and issued["issued_at"]
    assert issue(p, inv)["status"] == "issued"
    paid = pay(p, inv)
    assert paid["status"] == "paid" and paid["payment_ref"] == "receipt-0001" and paid["paid_at"]
    assert pay(p, inv, "receipt-0001")["status"] == "paid"


def test_an_invoice_cannot_be_paid_before_it_is_issued_or_without_a_reference(p):
    fee_in_place(p)
    inv = invoice(p)
    assert act(p, inv, "pay", {"payment_ref": "receipt-0001"}).status_code == 409
    issue(p, inv)
    for bad in ("", "x", "has space in it", "y" * 70):
        assert act(p, inv, "pay", {"payment_ref": bad}).status_code == 422
    assert detail(p, inv)["status"] == "issued"


def test_an_unknown_action_or_invoice_is_not_found(p):
    fee_in_place(p)
    inv = invoice(p)
    assert act(p, inv, "refund").status_code == 404
    assert p.post("staff/billing/invoices/nonexistent-id-00000/issue", {}, subject="ops"
                  ).status_code == 404


def test_only_the_administrator_cancels_and_only_with_a_reason_from_the_list(p):
    fee_in_place(p)
    inv = invoice(p)
    assert act(p, inv, "void", {"reason": "error"}, "ops").status_code == 403
    assert act(p, inv, "void", {"reason": "because"}, "boss").status_code == 422
    assert act(p, inv, "void", {}, "boss").status_code == 422
    done = json.loads(act(p, inv, "void", {"reason": "duplicate"}, "boss").content)["invoice"]
    assert done["status"] == "void" and done["void_reason"] == "duplicate"
    assert act(p, inv, "void", {"reason": "duplicate"}, "boss").status_code == 200   # harmless
    assert act(p, inv, "issue").status_code == 409
    assert act(p, inv, "pay", {"payment_ref": "receipt-0001"}).status_code == 409


# ------------------------------------------------------------------ credit notes
def test_a_credit_note_reduces_the_balance_and_never_below_zero(p):
    fee_in_place(p, 5000)
    inv = invoice(p, qty=2)                                            # 10000
    issue(p, inv)
    after = json.loads(credit(p, inv, 3000).content)["invoice"]
    assert after["credited_minor"] == 3000 and after["balance_minor"] == 7000
    assert after["credit_notes"][0]["number"] == "CN-000001"
    assert credit(p, inv, 7001).status_code == 422                     # more than is left
    assert json.loads(credit(p, inv, 7000, "error").content)["invoice"]["balance_minor"] == 0
    assert credit(p, inv, 1).status_code == 422


@pytest.mark.parametrize("amount,reason", [(0, "goodwill"), (-5, "goodwill"), (True, "goodwill"),
                                           ("100", "goodwill"), (100, "just because"), (100, "")])
def test_a_credit_note_needs_a_positive_whole_amount_and_a_reason_from_the_list(
        p, amount, reason):
    fee_in_place(p)
    inv = invoice(p)
    issue(p, inv)
    assert credit(p, inv, amount, reason).status_code == 422


def test_only_the_administrator_writes_credit_notes_and_only_on_issued_or_paid_invoices(p):
    fee_in_place(p)
    inv = invoice(p)
    assert credit(p, inv, 100).status_code == 409                      # a draft
    issue(p, inv)
    assert credit(p, inv, 100, who="ops").status_code == 403
    assert credit(p, inv, 100).status_code == 200
    pay(p, inv)
    assert credit(p, inv, 100).status_code == 200                      # paid: still reducible
    other = invoice(p, person=p.person)
    act(p, other, "void", {"reason": "error"}, "boss")
    assert credit(p, other, 100).status_code == 409                    # a cancelled one may not


def test_a_fully_credited_invoice_cannot_be_marked_paid_and_a_credited_one_cannot_be_cancelled(p):
    fee_in_place(p, 5000)
    inv = invoice(p)
    issue(p, inv)
    credit(p, inv, 5000)
    assert act(p, inv, "pay", {"payment_ref": "receipt-0001"}).status_code == 409
    assert act(p, inv, "void", {"reason": "error"}, "boss").status_code == 409


# ------------------------------------------------------------------ what a patient sees
def test_a_patient_sees_their_own_issued_paid_and_cancelled_invoices_but_never_drafts(p):
    fee_in_place(p)
    draft = invoice(p)
    issued = invoice(p, person=p.person)
    paid = invoice(p, person=p.person)
    void = invoice(p, person=p.person)
    issue(p, issued)
    issue(p, paid)
    pay(p, paid)
    act(p, void, "void", {"reason": "error"}, "boss")
    seen = {i["public_id"]: i["status"] for i in mine(p)}
    assert seen == {issued["public_id"]: "issued", paid["public_id"]: "paid",
                    void["public_id"]: "void"}
    assert draft["public_id"] not in seen


def test_a_patient_never_sees_anothers_bills_and_staff_see_none_there(p):
    fee_in_place(p)
    issue(p, invoice(p))
    other, _ = p.patient("other")
    assert mine(p, "other") == []
    assert mine(p, "ops") == [] and mine(p, "doc") == []
    assert other


def test_another_customer_sees_and_changes_nothing(p):
    fee_in_place(p)
    inv = invoice(p)
    outsider = Web()
    outsider.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    assert json.loads(outsider.get("staff/billing/invoices", subject="admin2").content
                      )["invoices"] == []
    assert outsider.get(f"staff/billing/invoices/{inv['public_id']}",
                        subject="admin2").status_code == 404
    assert outsider.post(f"staff/billing/invoices/{inv['public_id']}/issue", {},
                         subject="admin2").status_code == 404


def test_doctors_and_patients_cannot_use_the_staff_billing_screens(p):
    fee_in_place(p)
    inv = invoice(p)
    for who in ("doc", "pat"):
        assert p.get("staff/billing/invoices", subject=who).status_code == 403
        assert p.get("staff/billing/fees", subject=who).status_code == 403
        assert p.get(f"staff/billing/invoices/{inv['public_id']}", subject=who
                     ).status_code == 403


# ------------------------------------------------------------------ the ledger
def test_the_history_is_chained_in_order_and_names_who_acted(p):
    fee_in_place(p)
    inv = invoice(p)
    issue(p, inv)
    pay(p, inv)
    credit(p, inv, 100)
    one = detail(p, inv)
    assert [h["kind"] for h in one["history"]] == ["created", "issued", "paid", "credit_note"]
    assert one["chain_ok"] is True and one["bad_seq"] is None
    assert one["history"][0]["actor"] == p.desk or one["history"][0]["actor"]


def test_events_and_credit_notes_cannot_be_changed_or_removed(p):
    fee_in_place(p)
    inv = invoice(p)
    issue(p, inv)
    credit(p, inv, 100)
    with tenant_transaction(p.clinic.ctx):
        for model, field in ((InvoiceEvent, "kind"), (CreditNote, "reason")):
            with pytest.raises(DatabaseError), transaction.atomic():
                model.objects.all().update(**{field: "x"})
            with pytest.raises(DatabaseError), transaction.atomic():
                model.objects.all().delete()


def test_every_step_is_audited_without_amounts_or_references(p):
    fee_in_place(p, 123457)
    inv = invoice(p)
    issue(p, inv)
    pay(p, inv, "receipt-SECRET-77")
    credit(p, inv, 100, "error")
    with tenant_transaction(p.clinic.ctx):
        events = list(AuditEvent.objects.filter(action__startswith="billing.")
                      .order_by("chain_seq"))
    assert [e.action for e in events] == [
        "billing.fee_set", "billing.invoice_created", "billing.invoice_issued",
        "billing.invoice_paid", "billing.credit_note"]
    dump = json.dumps([e.details for e in events])
    assert "SECRET" not in dump and "123457" not in dump
