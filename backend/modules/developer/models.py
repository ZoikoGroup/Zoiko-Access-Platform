"""The partner/developer surface (GHE-13): API clients, webhook subscriptions and their deliveries.

An API key identifies a CLIENT, not a person, and grants only the scopes it was given: holding a key
never gives access to anyone's record. Sandbox clients only ever see synthetic data.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel
from foundation.timeutil import utc_now


class Environment(models.TextChoices):
    SANDBOX = "sandbox", "Sandbox (synthetic data only)"
    PRODUCTION = "production", "Production"


class ClientStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"


class ApiClient(CellScopedModel):
    name = models.CharField(max_length=100)
    # The clinic that owns this client ("" = the whole customer, e.g. made from the command line).
    organization_ref = models.CharField(max_length=22, blank=True, default="")
    environment = models.CharField(max_length=10, choices=Environment.choices)
    key_hash = models.CharField(max_length=64)               # sha256 of the secret, never the key
    scopes = models.JSONField(default=list)                  # e.g. ["catalog:read"]
    status = models.CharField(
        max_length=8, choices=ClientStatus.choices, default=ClientStatus.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by_ref = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="apiclient_scope_idx")]


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused by the client"
    DISABLED = "disabled", "Switched off after repeated failures"


class WebhookSubscription(CellScopedModel):
    client = models.ForeignKey(ApiClient, on_delete=models.PROTECT, related_name="webhooks")
    url = models.CharField(max_length=500)
    event_prefixes = models.JSONField(default=list)          # e.g. ["booking."]
    secret_token = models.TextField()                        # the signing secret, encrypted
    status = models.CharField(
        max_length=10, choices=SubscriptionStatus.choices, default=SubscriptionStatus.ACTIVE)
    dead_streak = models.PositiveSmallIntegerField(default=0)   # deliveries given up in a row

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="webhook_scope_idx")]


class DeliveryState(models.TextChoices):
    PENDING = "pending", "Waiting to be sent"
    DELIVERED = "delivered", "Delivered"
    DEAD = "dead", "Given up"


class WebhookDelivery(CellScopedModel):
    subscription = models.ForeignKey(
        WebhookSubscription, on_delete=models.PROTECT, related_name="deliveries")
    event_id = models.CharField(max_length=36)               # the outbox event's id
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)                 # the envelope: references only
    state = models.CharField(
        max_length=10, choices=DeliveryState.choices, default=DeliveryState.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=utc_now)
    last_status = models.PositiveSmallIntegerField(null=True, blank=True)    # HTTP status
    last_error = models.CharField(max_length=64, blank=True, default="")     # exception CLASS only
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="delivery_scope_idx"),
            models.Index(fields=["state", "next_attempt_at"], name="delivery_due_idx"),
        ]
        constraints = [
            # One delivery of an event to a subscription, however many times the event arrives.
            models.UniqueConstraint(
                fields=["subscription", "event_id"], name="delivery_once_per_event"),
        ]
