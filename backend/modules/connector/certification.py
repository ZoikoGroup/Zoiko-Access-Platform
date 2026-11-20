"""Certifying a partner's interface before any clinic may use it (GHE-13 T-47, T-48; DEV-230).

    enrol (support, R11) -> sandbox --certify (safety officer, R12)--> certified
        --go live (a support officer who neither enrolled nor certified it)--> live
    sandbox | certified | live --suspend (R11 or R12, with a reason)--> suspended

Three different people stand behind a live partner: who enrolled it, who certified it and who put it
live. A certification belongs to one PROFILE VERSION of the partner's interface: an adapter that
reports a version nobody certified counts as not live (T-48), so a material change needs certifying
again. Rows live in Zoiko's platform space, like country packs. The pretend partner (development
only, synthetic data) needs no certification.

TEMPORARY (please confirm): support and safety officers stand in for the real approvers (partner
management, clinical safety, security), as they do for country packs (DEV-128).
"""
from __future__ import annotations

import re
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from . import adapters
from .models import CertStage, ConnectorCertification

_NOTE = re.compile(r"^[^\x00-\x1f]{3,300}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def profile_of(key: str) -> str:
    """The profile version the adapter in this deployment speaks ("1" when it does not say)."""
    adapter = adapters.get(key)
    if adapter is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return str(getattr(adapter, "profile_version", "1"))[:32]


def cert_dict(row: ConnectorCertification) -> dict[str, Any]:
    return {"public_id": row.public_id, "connector_key": row.connector_key,
            "profile_version": row.profile_version, "stage": row.stage,
            "enrolled_by": row.enrolled_by_ref, "certified_by": row.certified_by_ref,
            "live_by": row.live_by_ref, "note": row.note, "created_at": row.created_at.isoformat()}


def _check(decision: policy.Decision, actor: str, operation: str, org: str | None,
           roles: frozenset[str]) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=operation,
                         organization_public_id=org)
    if decision.role_code not in roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _audit(actor: str, action: str, row: ConnectorCertification) -> None:
    record(actor_ref=actor, action=f"connector.cert_{action}", object_type="connector",
           object_ref=row.public_id, outcome=AuditOutcome.ALLOWED, reason_code=row.stage)


def _current(key: str) -> ConnectorCertification:
    row = ConnectorCertification.objects.select_for_update().filter(
        connector_key=key, profile_version=profile_of(key), **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def enrol(*, key: str, actor_public_id: str, decision: policy.Decision,
          organization_ref: str | None) -> dict[str, Any]:
    """Start the adapter's current profile version in the sandbox (support only)."""
    _check(decision, actor_public_id, "write:platform", organization_ref, frozenset({"R11"}))
    if adapters.is_simulated(key):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["key"])   # needs no certificate
    version = profile_of(key)
    try:
        with transaction.atomic():
            row = ConnectorCertification(connector_key=key, profile_version=version,
                                         enrolled_by_ref=actor_public_id, **_scope())
            row.save()
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
    _audit(actor_public_id, "enrolled", row)
    return cert_dict(row)


def certify(*, key: str, actor_public_id: str, decision: policy.Decision,
            organization_ref: str | None) -> dict[str, Any]:
    """A safety officer, not the one who enrolled it, certifies the sandbox results."""
    _check(decision, actor_public_id, "attest:platform", organization_ref, frozenset({"R12"}))
    with transaction.atomic():
        row = _current(key)
        if row.stage != CertStage.SANDBOX:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if actor_public_id == row.enrolled_by_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        row.stage, row.certified_by_ref = CertStage.CERTIFIED, actor_public_id
        row.save(update_fields=["stage", "certified_by_ref", "updated_at"])
        _audit(actor_public_id, "certified", row)
        return cert_dict(row)


def go_live(*, key: str, actor_public_id: str, decision: policy.Decision,
            organization_ref: str | None) -> dict[str, Any]:
    """A support officer who neither enrolled nor certified it puts it live."""
    _check(decision, actor_public_id, "write:platform", organization_ref, frozenset({"R11"}))
    with transaction.atomic():
        row = _current(key)
        if row.stage != CertStage.CERTIFIED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if actor_public_id in (row.enrolled_by_ref, row.certified_by_ref):
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        row.stage, row.live_by_ref = CertStage.LIVE, actor_public_id
        row.save(update_fields=["stage", "live_by_ref", "updated_at"])
        _audit(actor_public_id, "live", row)
        return cert_dict(row)


def suspend(*, key: str, note: str, actor_public_id: str, decision: policy.Decision,
            organization_ref: str | None) -> dict[str, Any]:
    """Stopping is easier than starting: either kind of officer, with a reason."""
    operation = "attest:platform" if decision.role_code == "R12" else "write:platform"
    _check(decision, actor_public_id, operation, organization_ref, frozenset({"R11", "R12"}))
    text = " ".join((note or "").split())
    if not _NOTE.match(text):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
    with transaction.atomic():
        row = _current(key)
        if row.stage == CertStage.SUSPENDED:
            return cert_dict(row)
        row.stage, row.note = CertStage.SUSPENDED, text
        row.save(update_fields=["stage", "note", "updated_at"])
        _audit(actor_public_id, "suspended", row)
        return cert_dict(row)


def overview() -> list[dict[str, Any]]:
    """Every adapter this deployment has, with the certification of its current version."""
    rows = {(r.connector_key, r.profile_version): r
            for r in ConnectorCertification.objects.filter(**_scope())}
    out = []
    for key in adapters.registered():
        version = profile_of(key)
        found = rows.get((key, version))
        out.append({"connector_key": key, "profile_version": version,
                    "simulated": adapters.is_simulated(key),
                    "certification": cert_dict(found) if found else None})
    return out


def is_live(key: str) -> bool:
    """May a clinic use this partner now? The pretend partner always; a real one only when the
    version it speaks today is LIVE. Read in the platform's space; safe inside a customer's
    transaction (the customer's binding is put back after)."""
    if adapters.is_simulated(key):
        return True
    if adapters.get(key) is None:
        return False
    from modules.trust import api as trust

    platform = next((t for t in trust.list_tenants()
                     if t["type"] == "platform" and t["status"] == "active"), None)
    if platform is None:
        return False
    with tenant_transaction(trust.context_for(platform["public_id"])):
        return ConnectorCertification.objects.filter(
            connector_key=key, profile_version=profile_of(key), stage=CertStage.LIVE,
            **_scope()).exists()
