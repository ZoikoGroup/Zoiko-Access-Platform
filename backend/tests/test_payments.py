"""Paying a bill through a payment provider (DEV-139 skeleton): switched off unless the settings
name a provider, the pretend provider only where the settings allow it, a success recorded through
the same "paid" path staff use, a failure or an unknown answer leaving the bill open, and only the
patient who owes a bill able to pay it."""
import json

import pytest
from django.db import connection

from foundation.cell_context import tenant_transaction
from modules.billing import payments
from modules.billing.models import PaymentAttempt
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_billing import act, fee_in_place, invoice, issue, mine
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture(autouse=True)
def registry():
    """Each test starts with the registry as the app made it and leaves it that way."""
    before = {k: payments.get(k) for k in payments.registered()}
    yield
    payments.clear()
    for provider in before.values():
        payments._providers[provider.key] = provider


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


@pytest.fixture
def on(settings):
    settings.BILLING_PAYMENT_PROVIDER = "pretend"
    payments.register(payments.pretend_provider)


def open_bill(p, amount=5000):
    fee_in_place(p, amount)
    return issue(p, invoice(p))


def pay_online(p, inv, key="key-00000001", who="pat"):
    return p.post(f"billing/invoices/{inv['public_id']}/payments", {"idempotency_key": key},
                  subject=who)


def bill_of(p, inv, who="pat"):
    return next(i for i in mine(p, who) if i["public_id"] == inv["public_id"])


# ------------------------------------------------------------------ switched off by default
def test_payments_are_switched_off_by_default_and_nothing_is_recorded(p, settings):
    assert settings.BILLING_PAYMENT_PROVIDER == ""
    inv = open_bill(p)
    r = pay_online(p, inv)
    assert r.status_code == 422
    body = json.loads(r.content)
    assert body["code"] == "VALIDATION_FAILED" and "payment_provider" in json.dumps(body)
    assert bill_of(p, inv)["status"] == "issued"
    with tenant_transaction(p.clinic.ctx):
        assert PaymentAttempt.objects.count() == 0


def test_a_provider_named_but_not_registered_refuses_too(p, settings):
    settings.BILLING_PAYMENT_PROVIDER = "acme-pay"
    inv = open_bill(p)
    assert pay_online(p, inv).status_code == 422


def test_production_like_settings_refuse_the_pretend_provider(p, settings):
    settings.BILLING_PRETEND_PROVIDER = False
    with pytest.raises(RuntimeError):
        payments.register(payments.pretend_provider)
    # Even if it was registered before the setting changed, it is never used.
    settings.BILLING_PAYMENT_PROVIDER = "pretend"
    assert payments.active() is None
    assert pay_online(p, open_bill(p)).status_code == 422


# ------------------------------------------------------------------ the pretend provider
def test_a_pretend_success_marks_the_bill_paid_through_the_usual_path(p, on):
    inv = open_bill(p, 5000)
    r = pay_online(p, inv)
    assert r.status_code == 201, r.content
    made = json.loads(r.content)["payment"]
    assert made["status"] == "succeeded" and made["amount_minor"] == 5000
    assert made["provider"] == "pretend" and made["attempts"] == 1 and not made["replayed"]
    paid = bill_of(p, inv)
    assert paid["status"] == "paid" and paid["payment_ref"] == made["provider_ref"]
    history = json.loads(p.get(f"staff/billing/invoices/{inv['public_id']}",
                               subject="ops").content)["invoice"]
    assert [h["kind"] for h in history["history"]] == ["created", "issued", "paid"]
    assert history["chain_ok"] is True
    with tenant_transaction(p.clinic.ctx):
        actions = list(AuditEvent.objects.filter(action__startswith="billing.payment")
                       .values_list("action", flat=True).order_by("chain_seq"))
        assert AuditEvent.objects.filter(action="billing.invoice_paid").count() == 1
    assert actions == ["billing.payment_started", "billing.payment_succeeded"]


def test_a_pretend_failure_leaves_the_bill_open_and_another_try_is_allowed(p, on):
    inv = open_bill(p, 5013)                                      # ends in 13: the pretend "no"
    r = pay_online(p, inv)
    assert r.status_code == 201
    assert json.loads(r.content)["payment"]["status"] == "failed"
    assert bill_of(p, inv)["status"] == "issued"
    again = pay_online(p, inv, key="key-00000002")
    assert again.status_code == 201                               # a failed try does not block
    assert bill_of(p, inv)["status"] == "issued"


def test_the_same_key_gives_back_the_same_payment_and_asks_nobody_twice(p, on):
    inv = open_bill(p)
    first = json.loads(pay_online(p, inv).content)["payment"]
    r = pay_online(p, inv)
    assert r.status_code == 200
    again = json.loads(r.content)["payment"]
    assert again["replayed"] and again["public_id"] == first["public_id"]
    assert again["attempts"] == 1
    with tenant_transaction(p.clinic.ctx):
        assert PaymentAttempt.objects.count() == 1


def test_a_key_used_for_another_bill_or_a_bad_key_is_refused(p, on):
    inv = open_bill(p)
    other = issue(p, invoice(p, person=p.person))
    assert pay_online(p, inv).status_code == 201
    assert pay_online(p, other).status_code == 409                # same key, another invoice
    assert pay_online(p, other, key="short").status_code == 422
    assert pay_online(p, other, key="").status_code == 422


def test_a_paid_draft_or_cancelled_bill_cannot_be_paid_online(p, on):
    fee_in_place(p)
    draft = invoice(p)
    assert pay_online(p, draft, key="key-draft-001").status_code == 404   # never seen by patients
    void = issue(p, invoice(p, person=p.person))
    act(p, void, "void", {"reason": "error"}, "boss")
    assert pay_online(p, void, key="key-void-0001").status_code == 409
    paid = issue(p, invoice(p, person=p.person))
    assert pay_online(p, paid, key="key-paid-0001").status_code == 201
    assert pay_online(p, paid, key="key-paid-0002").status_code == 409


# ------------------------------------------------------------------ an answer that never came
class Flaky:
    """A provider whose first answer is lost; asked again with the same key, it says succeeded."""

    key = "flaky"
    pretend = False

    def __init__(self):
        self.calls = []

    def start_payment(self, invoice_ref, amount_minor, currency, idempotency_key):
        self.calls.append(idempotency_key)
        if len(self.calls) == 1:
            raise TimeoutError
        return "flaky-ref-0001", payments.PaymentStatus.SUCCEEDED

    def payment_status(self, provider_ref):
        return payments.PaymentStatus.SUCCEEDED


def test_an_unknown_answer_leaves_the_bill_open_and_asking_again_uses_the_same_key(p, settings):
    flaky = Flaky()
    payments.register(flaky)
    settings.BILLING_PAYMENT_PROVIDER = "flaky"
    inv = open_bill(p)
    made = json.loads(pay_online(p, inv).content)["payment"]
    assert made["status"] == "unknown" and bill_of(p, inv)["status"] == "issued"
    assert pay_online(p, inv, key="key-00000009").status_code == 409   # ask about the open one
    r = p.post(f"billing/payments/{made['public_id']}/refresh", {}, subject="pat")
    assert r.status_code == 200, r.content
    done = json.loads(r.content)["payment"]
    assert done["status"] == "succeeded" and done["attempts"] == 2
    assert flaky.calls == ["key-00000001", "key-00000001"]
    assert bill_of(p, inv)["payment_ref"] == "flaky-ref-0001"


# ------------------------------------------------------------------ who may
def test_only_the_patient_who_owes_the_bill_may_pay_it(p, on):
    inv = open_bill(p)
    p.patient("other")
    assert pay_online(p, inv, who="other").status_code == 404
    assert pay_online(p, inv, who="ops").status_code == 403
    assert pay_online(p, inv, who="doc").status_code == 403
    assert bill_of(p, inv)["status"] == "issued"


def test_staff_see_attempts_and_the_payer_sees_their_own(p, on):
    inv = open_bill(p)
    made = json.loads(pay_online(p, inv).content)["payment"]
    staff = json.loads(p.get("staff/billing/payments", subject="ops").content)["payments"]
    assert [a["public_id"] for a in staff] == [made["public_id"]]
    one = json.loads(p.get(f"staff/billing/payments?invoice={inv['public_id']}",
                           subject="boss").content)["payments"]
    assert len(one) == 1 and one[0]["invoice_number"] == inv["number"]
    own = json.loads(p.get(f"billing/invoices/{inv['public_id']}/payments").content)["payments"]
    assert [a["public_id"] for a in own] == [made["public_id"]]
    assert p.get("staff/billing/payments", subject="pat").status_code == 403
    assert p.get("staff/billing/payments", subject="doc").status_code == 403
    p.patient("other")
    assert p.get(f"billing/invoices/{inv['public_id']}/payments",
                 subject="other").status_code == 404
    assert p.post(f"billing/payments/{made['public_id']}/refresh", {},
                  subject="other").status_code == 404


def test_another_customer_sees_no_attempts(p, on):
    inv = open_bill(p)
    made = json.loads(pay_online(p, inv).content)["payment"]
    outsider = Web()
    outsider.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    rows = json.loads(outsider.get("staff/billing/payments", subject="admin2").content)
    assert rows["payments"] == []
    one = json.loads(outsider.get(f"staff/billing/payments?invoice={inv['public_id']}",
                                  subject="admin2").content)
    assert one["payments"] == []
    assert outsider.post(f"billing/payments/{made['public_id']}/refresh", {},
                         subject="admin2").status_code == 403


def test_an_attempt_holds_references_only(p, on):
    inv = open_bill(p)
    pay_online(p, inv)
    names = {f.name for f in PaymentAttempt._meta.get_fields()}
    assert not names & {"card", "card_number", "pan", "name", "holder", "iban", "account", "cvv"}
    with connection.cursor() as c:
        c.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'billing_paymentattempt'")
        assert c.fetchone() == (True,)
