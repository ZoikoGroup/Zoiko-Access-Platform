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
import logging
import re
from typing import Any

from django.db import IntegrityError, transaction

from foundation import fhir as fhir_validator
from foundation import pagination
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

# A message may carry a PDF report of up to 5 MB as base64 (DEV-349); the message itself is still
# held to the checker's 256 KB, measured without the PDF.
MAX_FHIR_BYTES = 7_500_000
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


def list_quarantined(
    *, state: str | None = None, limit: int = 100, page: pagination.PageRequest | None = None
) -> list[dict[str, Any]]:
    """List quarantined FHIR messages for the current clinic."""
    rows = FhirQuarantine.objects.select_related("client").filter(**_scope())
    if state:
        if state not in FhirQuarantineState.values:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["state"])
        rows = rows.filter(state=state)
    return [quarantine_dict(q) for q in pagination.apply(rows, page, cap=min(limit, 200))]


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
    *, raw_body: bytes, signature: str, delivery_id: str, clock: Clock | None = None,
    fmt: str = "fhir",
) -> Answer:
    """Verify signed partner request, validate FHIR payload, and intake or quarantine. With
    `fmt="hl7v2"` the body is an HL7 v2 ORU^R01 message, read into the same FHIR shape first
    (DEV-340)."""
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
        return _accept_fhir(client, raw_body, delivery_id, now, clock, fmt)


def parse_payload(text: str) -> Any:
    """Public: a received body as FHIR data (JSON, or HL7 v2 read into FHIR). ValueError if not."""
    return _parse_payload(text)


def _parse_payload(text: str) -> Any:
    """A message body as data: FHIR JSON, or an HL7 v2 message read into FHIR (DEV-340). Raises
    ValueError when it is neither."""
    if text.startswith("MSH"):
        from .hl7 import to_fhir

        return to_fhir(text)
    return json.loads(text)


def _accept_fhir(
    client: PartnerClient,
    raw_body: bytes,
    delivery_id: str,
    now: dt.datetime,
    clock: Clock | None,
    fmt: str = "fhir",
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

    # 1. Parse: FHIR JSON, or an HL7 v2 message read into the same FHIR shape (DEV-340)
    try:
        text = raw_body.decode("utf-8")
        if fmt == "hl7v2" and not text.startswith("MSH"):
            raise ValueError("not HL7 v2")
        data = _parse_payload(text) if fmt == "hl7v2" else json.loads(text)
    except Exception as exc:  # noqa: BLE001 - any unreadable body is held, never taken in
        hl7 = fmt == "hl7v2"
        q = _quarantine(
            client=client,
            delivery_id=delivery_id,
            resource_type="HL7v2" if hl7 else "unknown",
            partner_ref="",
            digest=digest,
            raw_body=raw_body,
            issues=[{"path": "", "code": "format",
                     "text": (str(exc)[:200] or "unreadable HL7 v2 message") if hl7
                     else "invalid JSON payload"}],
        )
        return _reply(202, received=True, quarantined=True, id=q.public_id,
                      error="invalid_hl7" if hl7 else "invalid_json")

    # A local code with a mapping two Zoiko people approved also carries its SNOMED CT code, so a
    # lab that sends only its own codes is not held for that alone (DEV-327). Nothing else changes.
    data, mapped = _with_approved_mappings(data)

    # 2. Strict FHIR R4 validation (DEV-315) with the UK Core rules the lead switched on (DEV-321)
    issues = fhir_validator.validate(data, profile=fhir_validator.UK_CORE)
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

    if mapped:  # which approved mapping let this message through, recorded without content
        record(
            actor_ref=client.public_id,
            action="integration.fhir_code_mapped",
            object_type="fhir_resource",
            object_ref=partner_ref or delivery_id,
            outcome=AuditOutcome.ALLOWED,
            reason_code="approved_code_mapping",
        )

    # 3. Route valid resource. A Bundle is a report with its measured values (DEV-330).
    if resource_type in ("DiagnosticReport", "Bundle"):
        return _handle_diagnostic_report(
            client, fhir_validator.report_of(data), delivery_id, digest, raw_body, clock,
            values=fhir_validator.values_of(data),
        )

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
    values: list[dict[str, Any]] | None = None,
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

    # The FHIR id of our request turned back into its Zoiko id ("." stands for "_", DEV-331).
    request_public_id = fhir_validator.from_fhir_id(ref_str.split("/", 1)[1])
    status = str(data.get("status") or "")
    if status in ("cancelled", "entered-in-error"):
        # A withdrawn result is never applied automatically: a person decides (DEV-325).
        q = _quarantine(
            client=client,
            delivery_id=delivery_id,
            resource_type="DiagnosticReport",
            partner_ref=str(data.get("id", ""))[:64],
            digest=digest,
            raw_body=raw_body,
            issues=[{"path": "status", "code": "business-rule",
                     "text": "a withdrawn result needs a person to act on it"}],
        )
        return _reply(202, received=True, quarantined=True, id=q.public_id, error="withdrawn")
    correction = status if status in ("amended", "corrected", "appended") else ""
    summary = str(data.get("conclusion") or "")
    flag = _map_flag(data.get("conclusionCode"))
    result_ref = _clean_result_ref(data.get("id") or delivery_id)

    ctx = require_context()
    clinic_tenant_public = trust.tenant_public_id(ctx.tenant_id)

    # The exchange lives in Zoiko's platform space: look the request up and change it THERE, so it
    # works under the database's row-level security (DEV-326). A quarantine row belongs to the
    # clinic, so it is written after the platform block, back in the clinic's space.
    done: dict[str, Any] = {}
    problem: tuple[str, str, str, str] | None = None
    with tenant_transaction(_platform_ctx()):
        req = exchange.delivery_lookup(request_public_id)
        if req is None or req["from_tenant_ref"] != clinic_tenant_public:
            problem = ("basedOn[0]", "not_found",
                       "referenced ServiceRequest not found for this clinic", "request_not_found")
        else:
            try:
                done = _apply_report(req, request_public_id, client.public_id, result_ref,
                                     summary, flag, correction, clock, values or [])
            except ZoikoError as ze:
                reason_text = "cannot deliver result: patient agreement missing or invalid state"
                if ze.code == ErrorCode.NOT_FOUND:
                    reason_text = "referenced request not found"
                problem = ("status", "conflict", reason_text, "exchange_conflict")
    if problem is not None:
        path, code, text, error = problem
        q = _quarantine(
            client=client,
            delivery_id=delivery_id,
            resource_type="DiagnosticReport",
            partner_ref=result_ref,
            digest=digest,
            raw_body=raw_body,
            issues=[{"path": path, "code": code, "text": text}],
        )
        return _reply(202, received=True, quarantined=True, id=q.public_id, error=error)
    if req is not None and done.get("changed", True) and done.get("result_flag") == "critical":
        _alert_critical(request_public_id, req["to_tenant_ref"])
    attached, refused = _attach_forms(data, request_public_id, req, client.public_id, clock)

    record(
        actor_ref=client.public_id,
        action=(
            "integration.fhir_report_corrected"
            if correction
            else "integration.fhir_report_delivered"
        ),
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
        attachments=attached,
        attachments_refused=refused,
    )


def _attach_forms(
    report: dict[str, Any], request_ref: str, req: dict[str, str] | None, actor_ref: str,
    clock: Clock | None,
) -> tuple[int, int]:
    """Attach each PDF the report carries, exactly as a lab's upload (DEV-345, DEV-349): the same
    safety checks, encryption and virus scan, the treating doctor only. A PDF that is refused does
    not undo the delivered result. Returns (attached, refused)."""
    forms = fhir_validator.pdf_forms(report)
    if not forms or req is None:
        return 0, 0
    attached = refused = 0
    with tenant_transaction(_platform_ctx()):
        for title, data in forms:
            try:
                exchange.attach_pdf(request_ref=request_ref, tenant_ref=req["to_tenant_ref"],
                                    actor_ref=actor_ref, filename=title, data=data, clock=clock)
                attached += 1
            except ZoikoError:
                refused += 1
    if refused:
        record(actor_ref=actor_ref, action="integration.fhir_attachment_refused",
               object_type="exchange_request", object_ref=request_ref,
               outcome=AuditOutcome.DENIED)
    return attached, refused


def _platform_ctx() -> Any:
    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    if platform is None:
        raise ZoikoError(ErrorCode.SERVICE_UNAVAILABLE)
    return trust.context_for(platform["public_id"])


def _apply_report(
    req: dict[str, str],
    request_ref: str,
    actor_ref: str,
    result_ref: str,
    summary: str,
    flag: str,
    correction: str,
    clock: Clock | None,
    values: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deliver the result, or a new version of one already delivered (DEV-325), with its measured
    values (DEV-330). Call with the platform tenant bound."""
    if correction and req["state"] in ("result_available", "reviewed"):
        return exchange.correct_result(
            request_ref=request_ref,
            tenant_ref=req["to_tenant_ref"],
            actor_ref=actor_ref,
            result_ref=result_ref,
            summary=summary,
            flag=flag or None,
            correction=correction,
            clock=clock,
            values=values,
        )
    if req["state"] == "accepted":
        exchange.partner_step(
            request_ref=request_ref,
            tenant_ref=req["to_tenant_ref"],
            step="start",
            actor_ref=actor_ref,
            clock=clock,
        )
    return exchange.partner_step(
        request_ref=request_ref,
        tenant_ref=req["to_tenant_ref"],
        step="deliver",
        actor_ref=actor_ref,
        result_ref=result_ref,
        summary=summary,
        flag=flag,
        clock=clock,
        values=values,
    )


def _alert_critical(request_ref: str, partner_ref: str) -> None:
    """A critical result from a lab's system emails the person who ordered it, as the partner
    app's delivery does (DEV-310, DEV-326). The delivery stands even if the alert fails."""
    from modules.comms import api as comms  # late: comms is only needed for this alert

    try:
        with tenant_transaction(_platform_ctx()):
            target = exchange.critical_target(request_ref=request_ref, tenant_ref=partner_ref)
        if target is not None:
            with tenant_transaction(trust.context_for(target["clinic"])):
                comms.alert_critical_result(target["orderer"])
    except Exception as exc:  # noqa: BLE001 - never let an alert undo a delivery
        logging.getLogger("zoiko.alerts").warning("critical alert failed: %s", type(exc).__name__)


def _map_one(resource: Any, lookup: Any) -> tuple[Any, list[dict[str, str]]]:
    """One resource with the approved SNOMED CT code added to its `code`, when it has none of its
    own and one of its local codes was mapped and approved. A copy when changed."""
    if not isinstance(resource, dict) or not isinstance(resource.get("code"), dict):
        return resource, []
    codings = resource["code"].get("coding")
    if not isinstance(codings, list) or not codings:
        return resource, []
    if any(isinstance(c, dict) and c.get("system") == fhir_validator.SNOMED for c in codings):
        return resource, []
    for c in codings:
        if not (isinstance(c, dict) and isinstance(c.get("system"), str)
                and isinstance(c.get("code"), str)):
            continue
        target = lookup(c["system"], c["code"])
        if target:
            added = {"system": fhir_validator.SNOMED, "code": target}
            copy = {**resource, "code": {**resource["code"], "coding": [*codings, added]}}
            return copy, [{"system": c["system"], "code": c["code"], "snomed": target}]
    return resource, []


def _with_approved_mappings(data: Any) -> tuple[Any, list[dict[str, str]]]:
    """The message with approved SNOMED CT codes added where a code has none of its own (DEV-322,
    DEV-327): the resource itself, or every report and value inside a Bundle (DEV-338). Returns
    the message (a copy when changed) and what was mapped. Any failure leaves it as it came."""
    if not isinstance(data, dict):
        return data, []
    from modules.codemap import api as codemap  # late: only needed here

    try:
        with tenant_transaction(_platform_ctx()):
            entries = data.get("entry")
            if data.get("resourceType") == "Bundle" and isinstance(entries, list):
                mapped: list[dict[str, str]] = []
                new_entries = []
                for e in entries:
                    if isinstance(e, dict) and "resource" in e:
                        res, found = _map_one(e["resource"], codemap.lookup)
                        mapped += found
                        new_entries.append({**e, "resource": res})
                    else:
                        new_entries.append(e)
                return ({**data, "entry": new_entries} if mapped else data), mapped
            return _map_one(data, codemap.lookup)
    except Exception as exc:  # noqa: BLE001 - mapping is a help, never a reason to fail
        logging.getLogger("zoiko.codemap").warning("mapping lookup failed: %s", type(exc).__name__)
    return data, []



# ------------------------------------------------------------ retrying held messages (DEV-338)
def _code_held(issues: list[dict[str, Any]]) -> bool:
    """Was this message held over a code (one a newly approved mapping might now fix)?"""
    return any(str(i.get("path", "")).split(".")[-1].startswith("code")
               or str(i.get("path", "")).endswith("code") for i in issues)


def _retry_row(row: FhirQuarantine, actor_ref: str, clock: Clock | None) -> dict[str, Any]:
    """Take a held message in again, as if it had just arrived: approved code mappings first, then
    the same checks. Passes: it is delivered and marked "released". Fails: it stays held, with its
    problems brought up to date. Call with the clinic's tenant bound."""
    try:
        text = decrypt(row.payload_enc, tenant_id=row.tenant_id,
                       context=_quarantine_context(row.delivery_id))
        data = _parse_payload(text)
    except (CryptoError, ValueError):
        return quarantine_dict(row)
    data, mapped = _with_approved_mappings(data)
    issues = fhir_validator.validate(data, profile=fhir_validator.UK_CORE)
    if issues:
        row.issues = [{"path": i.path, "code": i.code, "text": i.text} for i in issues]
        row.entity_version += 1
        row.save(update_fields=["issues", "entity_version", "updated_at"])
        return quarantine_dict(row)
    answer: Answer = _reply(202, quarantined=False)
    if data.get("resourceType") in ("DiagnosticReport", "Bundle"):
        answer = _handle_diagnostic_report(
            row.client, fhir_validator.report_of(data), row.delivery_id, row.body_digest,
            text.encode("utf-8"), clock, values=fhir_validator.values_of(data))
    if answer[1].get("quarantined"):
        return quarantine_dict(row)  # held again for another reason; the row says why
    row.state = FhirQuarantineState.RELEASED
    row.decided_by_ref, row.decided_at = actor_ref, utc_now(clock)
    row.decision_note = "approved code mapping" if mapped else ""
    row.entity_version += 1
    row.save(update_fields=["state", "decided_by_ref", "decided_at", "decision_note",
                            "entity_version", "updated_at"])
    record(actor_ref=actor_ref, action="integration.fhir_quarantine_released",
           object_type="fhir_quarantine", object_ref=row.public_id,
           outcome=AuditOutcome.ALLOWED)
    return quarantine_dict(row)


def retry_quarantined(
    *, quarantine_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None, clock: Clock | None = None,
) -> dict[str, Any]:
    """A clinic staff member asks for a held message to be tried again (DEV-338)."""
    policy.check_receipt(decision, actor_public_id=actor_public_id, operation=WRITE,
                         organization_public_id=organization_ref)
    with transaction.atomic():
        row = (FhirQuarantine.objects.select_for_update().select_related("client")
               .filter(public_id=quarantine_ref, **_scope()).first())
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state != FhirQuarantineState.HELD:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        return _retry_row(row, actor_public_id, clock)


def retry_code_held(limit: int = 50, clock: Clock | None = None) -> int:
    """The worker's job: try again the messages this clinic holds over a code, so a mapping
    approved since lets them in without anyone asking the partner to resend. Returns how many
    were taken in. Call with the clinic's tenant bound."""
    released = 0
    rows = (FhirQuarantine.objects.select_related("client")
            .filter(state=FhirQuarantineState.HELD, **_scope()).order_by("created_at")[:limit])
    for row in rows:
        if not _code_held(list(row.issues)):
            continue
        with transaction.atomic():
            locked = (FhirQuarantine.objects.select_for_update().select_related("client")
                      .filter(pk=row.pk, state=FhirQuarantineState.HELD).first())
            if locked is not None and _retry_row(locked, "system", clock)["state"] == "released":
                released += 1
    return released
