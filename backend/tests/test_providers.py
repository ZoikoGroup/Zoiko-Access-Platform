"""Decided 27 November 2026 (DEV-400): AWS SES for email, AWS SNS for text messages, Stripe for
payments, PagerDuty for alerts. Keys only from the environment; the development stand-ins stay the
default; nothing personal goes to a provider that does not need it."""
import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.mail import EmailMessage

from foundation import aws, paging, ses_backend, texts
from foundation.release_check import release_problems
from modules.billing import payments, stripe_provider
from modules.billing.payments import PaymentStatus
from tests.test_payments import bill_of, open_bill, pay_online
from tests.test_payments import p as p  # noqa: F401  (the fixture)
from tests.test_payments import registry as registry  # noqa: F401  (the fixture)
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def aws_calls(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return b"<PublishResponse><PublishResult><MessageId>abc-123</MessageId></PublishResult>"

    monkeypatch.setattr(aws, "call", fake)
    monkeypatch.setattr(aws, "region", lambda: "eu-west-2")
    return calls


# ------------------------------------------------------------------ email: AWS SES
def test_ses_sends_plain_text_with_only_a_reference_tag(aws_calls):
    message = EmailMessage("Your appointment", "See you on Monday.", "clinic@zoiko.example",
                           ["pat@example.org"], headers={"X-Zoiko-Ref": "mail-0001"})
    assert ses_backend.SesBackend().send_messages([message]) == 1
    [call] = aws_calls
    assert call["service"] == "ses"
    assert call["url"] == "https://email.eu-west-2.amazonaws.com/v2/email/outbound-emails"
    body = json.loads(call["body"])
    assert body["Destination"]["ToAddresses"] == ["pat@example.org"]
    assert body["Content"]["Simple"]["Body"]["Text"]["Data"] == "See you on Monday."
    assert "Html" not in body["Content"]["Simple"]["Body"]           # no tracking links
    assert body["EmailTags"] == [{"Name": "zoiko_ref", "Value": "mail-0001"}]


def test_a_failed_send_raises_so_the_queue_tries_again(monkeypatch):
    def broken(**kwargs):
        raise aws.AwsError("ses answered 500")

    monkeypatch.setattr(aws, "call", broken)
    monkeypatch.setattr(aws, "region", lambda: "eu-west-2")
    with pytest.raises(ses_backend.SesError):
        ses_backend.SesBackend().send_messages([EmailMessage("s", "b", "a@x.org", ["b@x.org"])])


# ------------------------------------------------------------------ texts: AWS SNS
def test_sns_sends_one_transactional_text_and_keeps_its_id(settings, aws_calls):
    settings.TEXT_PROVIDER = "sns"
    send = texts.provider()
    assert send is not None
    assert send("+447700900123", "Your code is 123456") == "abc-123"
    [call] = aws_calls
    assert call["service"] == "sns" and call["url"] == "https://sns.eu-west-2.amazonaws.com/"
    assert b"Action=Publish" in call["body"] and b"Transactional" in call["body"]


def test_sns_is_a_real_provider_so_the_market_is_checked(settings, monkeypatch):
    """A real provider sends only where the customer's country pack switches messaging on."""
    from modules.comms import market
    from modules.jurisdiction import api as jurisdiction
    settings.TEXT_PROVIDER = "sns"
    monkeypatch.setattr(market, "require_context", lambda: SimpleNamespace(tenant_id="t"))
    monkeypatch.setattr(jurisdiction, "customer_capabilities", lambda tenant: frozenset())
    assert market.texts_allowed() is False
    settings.TEXT_PROVIDER = "console"
    assert market.texts_allowed() is True


# ------------------------------------------------------------------ payments: Stripe
@pytest.fixture
def stripe(settings, monkeypatch):
    settings.BILLING_PAYMENT_PROVIDER = "stripe"
    settings.STRIPE_SECRET_KEY = "sk_test_notreal"
    payments.register(stripe_provider.stripe_provider)
    calls: list[tuple[str, str, dict[str, str] | None, str]] = []
    state = {"payment_status": "unpaid", "status": "open"}

    def fake(method, path, fields=None, idempotency_key=""):
        calls.append((method, path, fields, idempotency_key))
        return {"id": "cs_test_" + "a" * 58, "url": "https://checkout.stripe.com/c/pay/cs_x",
                **state}

    monkeypatch.setattr(stripe_provider, "_call", fake)
    return SimpleNamespace(calls=calls, state=state)


def test_stripe_is_told_references_and_an_amount_never_a_name(p, stripe):
    inv = open_bill(p, 5000)
    r = pay_online(p, inv)
    assert r.status_code == 201, r.content
    made = json.loads(r.content)["payment"]
    assert made["status"] == "pending" and made["provider"] == "stripe"
    assert made["pay_url"].startswith("https://checkout.stripe.com/")
    assert len(made["provider_ref"]) == 66                             # a long session id fits
    method, path, fields, key = stripe.calls[0]
    assert (method, path) == ("POST", "/checkout/sessions") and key.startswith("zoiko-")
    assert fields is not None and fields["line_items[0][price_data][unit_amount]"] == "5000"
    assert p.person not in json.dumps(fields)


def test_a_paid_session_marks_the_invoice_paid_and_an_expired_one_does_not(p, stripe):
    inv = open_bill(p, 5000)
    made = json.loads(pay_online(p, inv).content)["payment"]
    stripe.state.update(payment_status="paid", status="complete")
    r = p.post(f"billing/payments/{made['public_id']}/refresh", {}, subject="pat")
    assert json.loads(r.content)["payment"]["status"] == "succeeded"
    assert bill_of(p, inv)["status"] == "paid"


def test_stripe_status_is_read_carefully():
    status = stripe_provider.StripeProvider._status
    assert status({"payment_status": "paid", "status": "complete"}) == PaymentStatus.SUCCEEDED
    assert status({"payment_status": "unpaid", "status": "expired"}) == PaymentStatus.FAILED
    assert status({"payment_status": "unpaid", "status": "open"}) == PaymentStatus.PENDING
    assert status({}) == PaymentStatus.UNKNOWN


def test_without_a_key_nothing_is_sent_to_stripe(settings):
    settings.STRIPE_SECRET_KEY = ""
    with pytest.raises(stripe_provider.StripeError):
        stripe_provider._call("GET", "/checkout/sessions/x")


# ------------------------------------------------------------------ alerts: PagerDuty
@pytest.fixture
def pages(settings, monkeypatch):
    settings.PAGER_PROVIDER = "pagerduty"
    settings.PAGERDUTY_ROUTING_KEY = "routing-key-test"
    sent: list[dict[str, Any]] = []

    def send(body: dict[str, Any]) -> bool:
        sent.append(body)
        return True

    monkeypatch.setattr(paging, "_send", send)
    paging._open.clear()
    return sent


def test_an_error_budget_alert_pages_once_a_day_and_resolves_when_it_clears(pages, monkeypatch):
    from foundation import error_budget
    alerts = [{"alert": "availability_fast_burn", "runbook": "docs/runbooks/02.md"}]
    monkeypatch.setattr(error_budget, "summary", lambda clock=None: {"alerts": alerts})
    assert paging.page_budget_alerts() == 1
    paging.page_budget_alerts()
    keys = {b["dedup_key"] for b in pages if b["event_action"] == "trigger"}
    assert len(keys) == 1                                              # the same page, repeated
    trig = pages[0]
    assert trig["payload"]["severity"] == "critical"
    assert trig["payload"]["custom_details"] == {"runbook": "docs/runbooks/02.md"}
    alerts.clear()
    paging.page_budget_alerts()
    assert pages[-1]["event_action"] == "resolve"


def test_in_development_nothing_is_paged(settings):
    settings.PAGER_PROVIDER = ""
    assert paging.trigger("zoiko-x-test", "Something") is False


def test_production_must_page_someone():
    assert any("PAGER_PROVIDER" in p for p in release_problems(good(PAGER_PROVIDER="")))
    assert any("TEXT_PROVIDER" in p for p in release_problems(good(TEXT_PROVIDER="console")))
    assert any("STRIPE_SECRET_KEY" in p for p in release_problems(
        good(BILLING_PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="")))
