"""Outbound webhooks (docs GHE-13, sections 07-08, WF-09, WF-10).

A clinic administrator registers a web address and the kinds of event it wants. Zoiko then sends a
signed, reference-only message each time one happens. Delivery is at least once and never claims
exactly once; failed deliveries are retried with growing, jittered delays and finally parked in a
dead-letter list a person can redrive (DEV-127). The specification gives the controls but no
numbers, so every limit below is a proposal.
"""
from __future__ import annotations

import uuid

from django.db import models

from foundation.models import CellScopedModel


class EndpointState(models.TextChoices):
    PENDING = "pending_verification", "Waiting for the address to be verified"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    REVOKED = "revoked", "Revoked"


class DeliveryState(models.TextChoices):
    PENDING = "pending", "Waiting to be sent"
    RETRYING = "retrying", "Will be tried again"
    DELIVERED = "delivered", "Receipt acknowledged"
    DEAD_LETTER = "dead_letter", "Gave up; needs a person"


class WebhookEndpoint(CellScopedModel):
    label = models.CharField(max_length=80)
    url = models.CharField(max_length=500)
    topics = models.JSONField(default=list)                   # event-type prefixes, e.g. "booking."
    key_id = models.CharField(max_length=20)                  # public; names the signing secret
    secret_enc = models.TextField()                           # sealed signing secret
    state = models.CharField(max_length=24, choices=EndpointState.choices,
                             default=EndpointState.PENDING)
    created_by_ref = models.CharField(max_length=22)
    verified_at = models.DateTimeField(null=True, blank=True)
    failure_streak = models.PositiveSmallIntegerField(default=0)   # dead letters in a row

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="webhook_ep_scope_idx"),
            models.Index(fields=["state"], name="webhook_ep_state_idx"),
        ]


class WebhookDelivery(CellScopedModel):
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.PROTECT, related_name="+")
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=100)
    delivery_id = models.UUIDField(default=uuid.uuid4, editable=False)
    body = models.TextField()                                 # the exact bytes that are signed
    state = models.CharField(max_length=16, choices=DeliveryState.choices,
                             default=DeliveryState.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_status = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True, default="")   # class name only
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["endpoint", "event_id"], name="webhook_once_per_event"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="webhook_dl_scope_idx"),
            models.Index(fields=["state", "next_attempt_at"], name="webhook_dl_due_idx"),
        ]


# ---------------------------------------------------------------- inbound (GHE-13 WF-04, WF-08)
class ClientState(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    REVOKED = "revoked", "Revoked"


class InboundState(models.TextChoices):
    RECEIVED = "received", "Received"
    QUARANTINED = "quarantined", "Held for a person to look at"


class PartnerClient(CellScopedModel):
    """A partner's credential for SENDING to us. The key id names the customer it belongs to
    (`pc.<customer id>.<random>`), so the signature can be checked inside that customer only."""

    label = models.CharField(max_length=80)
    key_id = models.CharField(max_length=80)
    secret_enc = models.TextField()                           # sealed signing secret
    allowed_types = models.JSONField(default=list)            # event types it may send (prefixes)
    state = models.CharField(max_length=12, choices=ClientState.choices, default=ClientState.ACTIVE)
    rate_per_minute = models.PositiveSmallIntegerField(default=60)
    created_by_ref = models.CharField(max_length=22)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["key_id"], name="partner_client_key_unique")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="partner_client_scope_idx")]


class InboundEvent(CellScopedModel):
    """A message from a partner. Only checked messages are kept in full; a held one keeps its
    fingerprint and the reason. Nothing here changes any booking or record (DEV-131)."""

    client = models.ForeignKey(PartnerClient, on_delete=models.PROTECT, related_name="+")
    delivery_id = models.CharField(max_length=64)
    event_type = models.CharField(max_length=100, blank=True, default="")
    body_digest = models.CharField(max_length=64)             # SHA-256 of the exact bytes received
    body = models.TextField(blank=True, default="")           # kept only when it was accepted
    state = models.CharField(max_length=12, choices=InboundState.choices,
                             default=InboundState.RECEIVED)
    reason = models.CharField(max_length=32, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["client", "delivery_id"],
                                    name="inbound_once_per_delivery"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="inbound_scope_idx"),
            models.Index(fields=["client", "created_at"], name="inbound_rate_idx"),
        ]
