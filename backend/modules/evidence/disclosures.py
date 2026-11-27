"""The stored disclosure log (DEV-352): what left a clinic about a patient, written at the time.

A row is added when information is SENT to another organization (a referral, a request to a partner,
a claim to a payer), when the patient's agreement RELEASES their details to it, and when they take
that agreement back (WITHDRAWN). Rows are append-only, so the log still says what happened after the
record it came from has changed. It holds who, to whom and which record: never what was said.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError

from .models import Disclosure, DisclosureKind, DisclosureStage

LIMIT = 200


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def record_disclosure(
    *, person_ref: str, kind: str, record_ref: str, actor_ref: str, recipient_name: str,
    recipient_ref: str = "", organization_ref: str = "", stage: str = DisclosureStage.SENT,
) -> bool:
    """Add one line to the log. Repeating the step a record is already at adds nothing (False).
    A later step takes who it went to, and the sender, from the record's first line."""
    if kind not in DisclosureKind.values or stage not in DisclosureStage.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["kind"])
    with transaction.atomic():
        earlier = list(Disclosure.objects.filter(record_ref=record_ref, **_scope())
                       .order_by("created_at", "id"))
        if earlier and earlier[-1].stage == stage:
            return False
        if earlier:
            recipient_ref = recipient_ref or earlier[0].recipient_ref
            organization_ref = organization_ref or earlier[0].organization_ref
            recipient_name = recipient_name or earlier[0].recipient_name
        Disclosure.objects.create(
            person_ref=person_ref, kind=kind, stage=stage, record_ref=record_ref,
            organization_ref=organization_ref, recipient_ref=recipient_ref,
            recipient_name=recipient_name[:200], actor_ref=actor_ref, **_scope())
    return True


def list_disclosures(person_ref: str) -> list[dict[str, Any]]:
    """The lines about one patient, newest first."""
    rows = Disclosure.objects.filter(person_ref=person_ref, **_scope()).order_by(
        "-created_at", "-id")[:LIMIT]
    return [{"id": r.public_id, "kind": r.kind, "stage": r.stage, "record": r.record_ref,
             "to": r.recipient_name, "actor_ref": r.actor_ref, "at": r.created_at.isoformat()}
            for r in rows]
