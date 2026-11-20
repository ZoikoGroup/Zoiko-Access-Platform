"""What a payment provider looks like, and the registry of the ones this deployment may use.

No payment provider has been chosen (a lead decision), so this is a switched-off skeleton. Only the
pretend provider below exists, and only where the settings allow it (development and tests). With
BILLING_PAYMENT_PROVIDER empty, the default, nothing can be started.

A provider is told references and an amount only: the invoice's public id, the amount in whole
minor units, the currency and our idempotency key. It never sees a patient's name, and the product
never sees a card number: the provider collects card details on its own pages. A provider never
touches the database; the billing services do that.

A provider answers with one of four statuses, and they must not be confused:

  pending      the provider has it and has not finished (ask again later)
  succeeded    the provider says the money was taken
  failed       the provider says it was not (the invoice stays open)
  unknown      we could not tell (no answer, or an answer we did not understand). Never treated as
               paid or as failed: we ask the provider again
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Protocol

from django.conf import settings


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


FINAL = frozenset({PaymentStatus.SUCCEEDED, PaymentStatus.FAILED})


class PaymentProvider(Protocol):
    key: str
    # True only for a provider that moves no real money (development and tests).
    pretend: bool

    def start_payment(
        self, invoice_ref: str, amount_minor: int, currency: str, idempotency_key: str
    ) -> tuple[str, PaymentStatus]:
        """Ask the provider to take a payment. Returns the provider's own reference and status.

        Must be idempotent on `idempotency_key`: asking again with the same key returns the same
        payment, never a second one. The billing services rely on this to recover from a crash."""
        ...

    def payment_status(self, provider_ref: str) -> PaymentStatus:
        """What the provider now says about a payment it gave us the reference of."""
        ...


class PretendProvider:
    """Development and tests only: no money moves and nothing leaves the server.

    Deterministic: the same idempotency key always gives the same reference, and an amount ending
    in 13 minor units (for example 50.13) fails, so tests can see a refusal. Everything else
    succeeds at once. The outcome is written into the reference, so asking about it later needs no
    memory."""

    key = "pretend"
    pretend = True
    _OK = "pretend-ok-"
    _NO = "pretend-no-"

    def start_payment(
        self, invoice_ref: str, amount_minor: int, currency: str, idempotency_key: str
    ) -> tuple[str, PaymentStatus]:
        digest = hashlib.sha256(f"{invoice_ref}|{idempotency_key}".encode()).hexdigest()[:24]
        if amount_minor % 100 == 13:
            return self._NO + digest, PaymentStatus.FAILED
        return self._OK + digest, PaymentStatus.SUCCEEDED

    def payment_status(self, provider_ref: str) -> PaymentStatus:
        if provider_ref.startswith(self._OK):
            return PaymentStatus.SUCCEEDED
        if provider_ref.startswith(self._NO):
            return PaymentStatus.FAILED
        return PaymentStatus.UNKNOWN


pretend_provider = PretendProvider()

_providers: dict[str, PaymentProvider] = {}


def register(provider: PaymentProvider) -> None:
    """Make a provider available. Registering the same key again replaces it.

    A pretend provider is refused unless this deployment's settings say BILLING_PRETEND_PROVIDER =
    True, which only the development and test settings do. Production never has it, so no invoice
    there can be marked paid by a payment that never happened."""
    if getattr(provider, "pretend", False) and not getattr(
            settings, "BILLING_PRETEND_PROVIDER", False):
        raise RuntimeError("the pretend payment provider is only for development and tests")
    _providers[provider.key] = provider


def get(key: str) -> PaymentProvider | None:
    return _providers.get(key)


def active() -> PaymentProvider | None:
    """The provider this deployment uses (BILLING_PAYMENT_PROVIDER), or None when payments are
    switched off or the named provider is not registered here."""
    key = getattr(settings, "BILLING_PAYMENT_PROVIDER", "") or ""
    found = _providers.get(key) if key else None
    if found is not None and getattr(found, "pretend", False) and not getattr(
            settings, "BILLING_PRETEND_PROVIDER", False):
        return None                                  # checked again at use, not only at start-up
    return found


def registered() -> list[str]:
    return sorted(_providers)


def clear() -> None:
    """For tests only."""
    _providers.clear()
