"""Inbound FHIR intake and quarantine handling (DEV-315, docs/FHIR-DESIGN.md, GHE-13 T-25, T-60).

A partner sends a signed FHIR resource to POST /api/v1/fhir.
Transport signature (HMAC-SHA256), skew, and rate limit are verified before anything in the body
is trusted.

The resource is validated against the pinned FHIR R4 profile (foundation.fhir.validate) BEFORE
anything is stored.

- Valid resources:
  * DiagnosticReport: mapped to exchange.partner_step('deliver') if referencing an existing
    ServiceRequest and patient consent is active.
  * ServiceRequest: validated and acknowledged.
- Invalid or unmappable resources:
  * Stored in FhirQuarantine (encrypted payload, issues list, state='held').
  * Creates NO clinical state: no request moves, no result appears, no one is notified.
  * Clinic staff inspect held messages via the staff app, and can mark them as discarded
    or request resend.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
from typing import Any

from django.db import IntegrityError, transaction

from foundation import fhir as fhir_validator
from foundation.cell_context import require_context, tenant_transaction
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.exchange import api as exchange
from modules.policy import api as policy
from modules.trust import api as trust

from .delivery import sign
from .inbound import (
    _DELIVERY,
    _KEY,
    _SIGNATURE,
    MAX_SKEW,
    _secret_context,
)
from .models import (
    ClientState,
    FhirQuarantine,
    FhirQuarantineState,
    PartnerClient,
)

MAX_FHIR_BYTES = 256 * 1024  # 256 KB
WRITE = "write:integration"

_RESULT_REF_CLEAN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
Answer = tuple[int, dict[str, Any], dict[str, str]]


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _quarantine_context(delivery_id: str) -> str:
    return f"integration:fhir_quarantine:{delivery_id}"


def _reply(status: int, **body: Any) -> Answer:
    return status, body, {}


def quarantine_dict(q: FhirQuarantine) -> dict[str, Any]:
    return {
        "public_id": q.public_id,
        "delivery_id": q.delivery_id,
        "partner_label": q.client.label,
        "partner_client_id": q.client.public_id,
        "resource_type": q.resource_type,
        "partner_ref": q.partner_ref,
        "body_digest": q.body_digest,
        "issues": list(q.issues),
        "state": q.state,
        "created_at": q.created_at.isoformat(),
        "decided_at": q.decided_at.isoformat() if q.decided_at else None,
        "decided_by": q.decided_by_ref,
        "decision_note": q.decision_note,
    }


def list_quarantined(*, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List quarantined FHIR messages for the current clinic."""
    rows = FhirQuarantine.objects.select_related("client").filter(**_scope())
    if state:
        if state not in FhirQuarantineState.values:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["state"])
        rows = rows.filter(state=state)
    return [quarantine_dict(q) for q in rows.order_by("-created_at")[: min(limit, 200)]]


def quarantine_step(
    *,
    quarantine_ref: str,
    step: str,
    actor_public_id: str,
    decision: policy.Decision,
    organization_ref: str | None,
    note: Any = "",
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A clinic staff member marks a quarantined message as discarded or requests a resend."""
    policy.check_receipt(
        decision,
        actor_public_id=actor_public_id,
        operation=WRITE,
        organization_public_id=organization_ref,
    )
    targets = {
        "discard": FhirQuarantineState.DISCARDED,
        "re-request": FhirQuarantineState.RESEND_REQUESTED,
    }
    if step not in targets:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    clean_note = " ".join(str(note or "").split())
    if clean_note and not (3 <= len(clean_note) <= 300):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])

    with transaction.atomic():
        row = (
            FhirQuarantine.objects.select_for_update()
            .filter(public_id=quarantine_ref, **_scope())
            .first()
        )
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state != FhirQuarantineState.HELD:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)

        row.state = targets[step]
        row.decided_by_ref = actor_public_id
        row.decision_note = clean_note
        row.decided_at = utc_now(clock)
        row.entity_version += 1
        row.save()

        record(
            actor_ref=actor_public_id,
            action=f"integration.fhir_quarantine_{step}",
            object_type="fhir_quarantine",
            object_ref=row.public_id,
            outcome=AuditOutcome.ALLOWED,
        )
        return quarantine_dict(row)


def _clean_result_ref(val: Any) -> str:
    s = str(val or "").strip()
    if _RESULT_REF_CLEAN.match(s):
        return s
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "_", s)
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"res_{cleaned}"
    return cleaned[:40]


def _map_flag(codes: Any) -> str:
    if not isinstance(codes, list):
        return "normal"
    for item in codes:
        if not isinstance(item, dict):
            continue
        codings = item.get("coding") or []
        for c in codings:
            code = str(c.get("code") or "").strip().lower()
            if code in ("crit", "critical", "aa", "hh", "ll"):
                return "critical"
            if code in ("a", "abnormal", "h", "l", "pos", "positive", "high", "low"):
                return "abnormal"
            if code in ("n", "normal", "neg", "negative"):
                return "normal"
    return "normal"


def _quarantine(
    *,
    client: PartnerClient,
    delivery_id: str,
    resource_type: str,
    partner_ref: str,
    digest: str,
    raw_body: bytes,
    issues: list[dict[str, str]],
) -> FhirQuarantine:
    try:
        with transaction.atomic():
            q = FhirQuarantine.objects.create(
                client=client,
                delivery_id=delivery_id,
                resource_type=resource_type[:64],
                partner_ref=partner_ref[:64],
                body_digest=digest,
                payload_enc=encrypt(
                    raw_body.decode("utf-8", "replace"),
                    tenant_id=client.tenant_id,
                    context=_quarantine_context(delivery_id),
                ),
                issues=issues,
                state=FhirQuarantineState.HELD,
                **_scope(),
            )
            record(
                actor_ref=client.public_id,
                action="integration.fhir_quarantined",
                object_type="fhir_quarantine",
                object_ref=q.public_id,
                outcome=AuditOutcome.ALLOWED,
            )
            return q
    except IntegrityError:
        existing = FhirQuarantine.objects.filter(
            client=client, delivery_id=delivery_id, **_scope()
        ).first()
        if existing is not None:
            return existing
        raise


def receive_fhir(
    *, raw_body: bytes, signature: str, delivery_id: str, clock: Clock | None = None
) -> Answer:
    """Verify signed partner request, validate FHIR payload, and intake or quarantine."""
    now = utc_now(clock)
    if len(raw_body) > MAX_FHIR_BYTES:
        return _reply(413, error="body_too_large")
    parsed = _SIGNATURE.match(signature or "")
    key = _KEY.match(parsed.group(2)) if parsed else None
    if not parsed or not key or not _DELIVERY.match(delivery_id or ""):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    stamp, key_id, given = int(parsed.group(1)), parsed.group(2), parsed.group(3)
    if abs(now - dt.datetime.fromtimestamp(stamp, dt.timezone.utc)) > MAX_SKEW:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    try:
        ctx = trust.context_for(key.group(1))
    except ZoikoError:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None

    with tenant_transaction(ctx):
        client = (
            PartnerClient.objects.select_for_update()
            .filter(key_id=key_id, state=ClientState.ACTIVE, **_scope())
            .first()
        )
        if client is None:
            raise ZoikoError(ErrorCode.UNAUTHENTICATED)
        try:
            secret = decrypt(
                client.secret_enc,
                tenant_id=client.tenant_id,
                context=_secret_context(client.public_id),
            )
        except CryptoError:
            raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None
        if not hmac.compare_digest(sign(secret, stamp, delivery_id, raw_body), given):
            raise ZoikoError(ErrorCode.UNAUTHENTICATED)
        return _accept_fhir(client, raw_body, delivery_id, now, clock)


def _accept_fhir(
    client: PartnerClient,
    raw_body: bytes,
    delivery_id: str,
    now: dt.datetime,
    clock: Clock | None,
) -> Answer:
    digest = hashlib.sha256(raw_body).hexdigest()

    # Rate limiting
    recent = FhirQuarantine.objects.filter(
        client=client, created_at__gte=now - dt.timedelta(minutes=1), **_scope()
    ).count()
    if recent >= client.rate_per_minute:
        return 429, {"error": "rate_limited"}, {"Retry-After": "60"}

    # Idempotency check across quarantine
    seen_q = FhirQuarantine.objects.filter(
        client=client, delivery_id=delivery_id, **_scope()
    ).first()
    if seen_q is not None:
        if seen_q.body_digest == digest:
            return _reply(202, received=True, duplicate=True, quarantined=True, id=seen_q.public_id)
        return _reply(409, error="delivery_id_reused_with_different_content")

    client.last_used_at = now
    client.save(update_fields=["last_used_at", "updated_at"])

    # 1. Parse JSON
    try:
        data = json.loads(raw_body)
    except Exception:
        q = _quarantine(
            client=client,
            delivery_id=delivery_id,
            resource_type="unknown",
            partner_ref="",
            digest=digest,
            raw_body=raw_body,
            issues=[{"path": "", "code": "format", "text": "invalid JSON payload"}],
        )
        return _reply(202, received=True, quarantined=True, id=q.public_id, error="invalid_json")

    # 2. Strict FHIR R4 validation (DEV-315)
    issues = fhir_validator.validate(data)
    resource_type = str(data.get("resourceType", "unknown"))[:64]
    partner_ref = str(data.get("id", ""))[:64]
    if issues:
        q = _quarantine(
            client=client,
            delivery_id=delivery_id,
            resource_type=resource_type,
            partner_ref=partner_ref,
            digest=digest,
            raw_body=raw_body,
            issues=[{"path": i.path, "code": i.code, "text": i.text} for i in issues],
        )
        return _reply(
            202,
            received=True,
            quarantined=True,
            id=q.public_id,
            issues=[{"path": i.path, "code": i.code, "text": i.text} for i in issues],
        )

    # 3. Route valid resource
    if resource_type == "DiagnosticReport":
        return _handle_diagnostic_report(client, data, delivery_id, digest, raw_body, clock)

    if resource_type == "ServiceRequest":
        record(
            actor_ref=client.public_id,
            action="integration.fhir_service_request_received",
            object_type="fhir_resource",
            object_ref=delivery_id,
            outcome=AuditOutcome.ALLOWED,
        )
        return _reply(
            202,
            received=True,
            quarantined=False,
            resource_type=resource_type,
            id=partner_ref,
        )

    q = _quarantine(
        client=client,
        delivery_id=delivery_id,
        resource_type=resource_type,
        partner_ref=partner_ref,
        digest=digest,
        raw_body=raw_body,
        issues=[
            {
                "path": "resourceType",
                "code": "value",
                "text": f"unsupported resource type {resource_type}",
            }
        ],
    )
    return _reply(
        202,
        received=True,
        quarantined=True,
        id=q.public_id,
        error="unsupported_resource",
    )


def _handle_diagnostic_report(
    client: PartnerClient,
    data: dict[str, Any],
    delivery_id: str,
    digest: str,
    raw_body: bytes,
    clock: Clock | None,
) -> Answer:
    based_on = data.get("basedOn") or []
    ref_str = based_on[0]["reference"] if based_on else ""
    if not ref_str.startswith("ServiceRequest/"):
        q = _quarantine(
            client=client,
            delivery_id=delivery_id,
            resource_type="DiagnosticReport",
            partner_ref=str(data.get("id", ""))[:64],
            digest=digest,
            raw_body=raw_body,
            issues=[
                {
                    "path": "basedOn[0]",
                    "code": "value",
                    "text": "must point to a ServiceRequest",
                }
            ],
        )
        return _reply(202, received=True, quarantined=True, id=q.public_id, error="bad_based_on")

    request_public_id = ref_str.split("/", 1)[1]
    summary = str(data.get("conclusion") or "")
    flag = _map_flag(data.get("conclusionCode"))
    result_ref = _clean_result_ref(data.get("id") or delivery_id)

    from modules.exchange.models import ExchangeRequest, State

    ctx = require_context()
    clinic_tenant_public = trust.tenant_public_id(ctx.tenant_id)

    with tenant_transaction():  # platform space for ExchangeRequest
        req = ExchangeRequest.objects.filter(public_id=request_public_id).first()
        if req is None or req.from_tenant_ref != clinic_tenant_public:
            q = _quarantine(
                client=client,
                delivery_id=delivery_id,
                resource_type="DiagnosticReport",
                partner_ref=result_ref,
                digest=digest,
                raw_body=raw_body,
                issues=[
                    {
                        "path": "basedOn[0]",
                        "code": "not_found",
                        "text": "referenced ServiceRequest not found for this clinic",
                    }
                ],
            )
            return _reply(
                202,
                received=True,
                quarantined=True,
                id=q.public_id,
                error="request_not_found",
            )

        try:
            # Advance to in_progress if currently accepted
            if req.state == State.ACCEPTED:
                exchange.partner_step(
                    request_ref=request_public_id,
                    tenant_ref=req.to_tenant_ref,
                    step="start",
                    actor_ref=client.public_id,
                    clock=clock,
                )

            done = exchange.partner_step(
                request_ref=request_public_id,
                tenant_ref=req.to_tenant_ref,
                step="deliver",
                actor_ref=client.public_id,
                result_ref=result_ref,
                summary=summary,
                flag=flag,
                clock=clock,
            )
        except ZoikoError as ze:
            reason_text = "cannot deliver result: patient agreement missing or invalid state"
            if ze.code == ErrorCode.NOT_FOUND:
                reason_text = "referenced request not found"
            q = _quarantine(
                client=client,
                delivery_id=delivery_id,
                resource_type="DiagnosticReport",
                partner_ref=result_ref,
                digest=digest,
                raw_body=raw_body,
                issues=[{"path": "status", "code": "conflict", "text": reason_text}],
            )
            return _reply(
                202,
                received=True,
                quarantined=True,
                id=q.public_id,
                error="exchange_conflict",
            )

    record(
        actor_ref=client.public_id,
        action="integration.fhir_report_delivered",
        object_type="exchange_request",
        object_ref=request_public_id,
        outcome=AuditOutcome.ALLOWED,
    )
    return _reply(
        202,
        received=True,
        quarantined=False,
        request_id=request_public_id,
        state="result_available",
        changed=done.get("changed", True),
    )
