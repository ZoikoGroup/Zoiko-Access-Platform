"""Payments through Stripe (decided 27 Nov 2026, DEV-400: Stripe for payments).

A Stripe Checkout Session per payment: the patient pays on Stripe's own page, so this product never
sees a card number. Stripe is told references and an amount only (our invoice reference, the
amount in minor units, the currency) and never a patient's name. Asking again with the same
idempotency key gives the same session (Stripe's `Idempotency-Key`), so there is never a second
charge. The secret key comes only from the environment (`STRIPE_SECRET_KEY`), never logged.

Status: `paid` is succeeded; an `expired` session is failed (the invoice stays open); anything else
is pending. No answer, or one we do not understand, is unknown (asked again later).
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

from .payments import PaymentStatus

API = "https://api.stripe.com/v1"


class StripeError(RuntimeError):
    pass


def _call(method: str, path: str, fields: dict[str, str] | None = None,
          idempotency_key: str = "") -> dict[str, Any]:
    secret = getattr(settings, "STRIPE_SECRET_KEY", "")
    if not secret:
        raise StripeError("STRIPE_SECRET_KEY is not set")
    headers = {"Authorization": "Basic " + base64.b64encode(f"{secret}:".encode()).decode()}
    data = None
    if fields is not None:
        data = urllib.parse.urlencode(fields).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(  # noqa: S310 - a fixed https address
        API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
            found = json.load(answer)
    except urllib.error.HTTPError as exc:
        raise StripeError(f"stripe answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise StripeError("stripe could not be reached") from None
    if not isinstance(found, dict):
        raise StripeError("stripe gave an unreadable answer")
    return found


class StripeProvider:
    key = "stripe"
    pretend = False

    def start_payment(
        self, invoice_ref: str, amount_minor: int, currency: str, idempotency_key: str
    ) -> tuple[str, PaymentStatus]:
        back = getattr(settings, "PATIENT_APP_URL", "").rstrip("/")
        found = _call("POST", "/checkout/sessions", {
            "mode": "payment",
            "client_reference_id": invoice_ref,
            "metadata[invoice_ref]": invoice_ref,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": str(int(amount_minor)),
            "line_items[0][price_data][product_data][name]": f"Invoice {invoice_ref}",
            "success_url": f"{back}/?paid={invoice_ref}",
            "cancel_url": f"{back}/?unpaid={invoice_ref}",
        }, idempotency_key=f"zoiko-{idempotency_key}")
        return str(found.get("id", "")), self._status(found)

    def payment_status(self, provider_ref: str) -> PaymentStatus:
        return self._status(_call("GET", f"/checkout/sessions/{urllib.parse.quote(provider_ref)}"))

    def payment_page(self, provider_ref: str) -> str | None:
        """Where the patient pays (Stripe's page), while the session is open."""
        found = _call("GET", f"/checkout/sessions/{urllib.parse.quote(provider_ref)}")
        url = found.get("url")
        return str(url) if isinstance(url, str) and url.startswith("https://") else None

    @staticmethod
    def _status(found: dict[str, Any]) -> PaymentStatus:
        if found.get("payment_status") == "paid":
            return PaymentStatus.SUCCEEDED
        if found.get("status") == "expired":
            return PaymentStatus.FAILED
        if found.get("status") in ("open", "complete"):
            return PaymentStatus.PENDING
        return PaymentStatus.UNKNOWN


stripe_provider = StripeProvider()

# ------------------------------------------------------------------ Stripe's webhook (DEV-403)
WEBHOOK_TOLERANCE = 300                      # seconds; an older signed event is refused
SESSION_EVENTS = frozenset({"checkout.session.completed",
                            "checkout.session.async_payment_succeeded",
                            "checkout.session.async_payment_failed",
                            "checkout.session.expired"})


def verify_webhook(body: bytes, header: str, now: float) -> bool:
    """Stripe-Signature: t=<time>,v1=<HMAC-SHA256 of "t.body" under STRIPE_WEBHOOK_SECRET>."""
    import hashlib
    import hmac

    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    if not secret or not header or len(header) > 1000:
        return False
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    stamps = [v for k, v in parts if k == "t"]
    signatures = [v for k, v in parts if k == "v1"]
    if len(stamps) != 1 or not stamps[0].isdigit() or not signatures:
        return False
    if abs(now - int(stamps[0])) > WEBHOOK_TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), stamps[0].encode() + b"." + body,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


def session_of(event: Any) -> str | None:
    """The checkout session an event is about, or None for anything else. Only the id is used: the
    payment's outcome is then asked of Stripe itself, never taken from the event."""
    if not isinstance(event, dict) or event.get("type") not in SESSION_EVENTS:
        return None
    found = ((event.get("data") or {}).get("object") or {}).get("id")
    return found if isinstance(found, str) and found.startswith("cs_") and len(found) <= 128 \
        else None
