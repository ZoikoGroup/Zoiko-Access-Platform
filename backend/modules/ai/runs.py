"""Running an AI feature, and the human approval of anything it wants to change.

Every run starts at the permit gate (default deny). Prompt and answer TEXT is never stored: only
fingerprints. A run in SHADOW mode acts on nothing. In live mode, a read tool may be named but a
WRITE tool only creates a pending approval: nothing is carried out until the named human (two,
where the permit says so) approves, and never the person who proposed it. The answer is always
routed to human review: this system has no way to show model output as a verified clinical fact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import (
    AiActionApproval,
    AiRequest,
    AiResponseEvidence,
    AiUsePermit,
    ApprovalState,
    Decision,
    PermitState,
)
from .permits import FORBIDDEN_TOOLS, check_permit, tool_digest

Provider = Callable[[str], str]
_PROVIDERS: dict[str, tuple[Provider, str | None, str | None, str | None]] = {}
MAX_INPUT_CHARS = 8000


def register_provider(
    feature_id: str,
    provider: Provider,
    *,
    model_digest: str | None = None,
    prompt_digest: str | None = None,
    tool_digest: str | None = None,
) -> None:
    """Plug in what actually answers.

    A real model passes the digests of the model, prompt and tool policy it runs; the gate refuses
    to call it if they differ from the permit (GHE-23). The built-in template is not a model and
    passes none.
    """
    _PROVIDERS[feature_id] = (provider, model_digest, prompt_digest, tool_digest)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def run(
    feature_id: str,
    *,
    actor_ref: str,
    purpose_code: str,
    input_text: str,
    tools: list[dict[str, Any]] | None = None,
    correlation_id: str = "",
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Run one AI step. `tools` lists what the model wants to use: {"tool": name, "payload": {}}."""
    if not actor_ref or not 1 <= len(input_text or "") <= MAX_INPUT_CHARS or not purpose_code:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["input"])
    permit = check_permit(feature_id, clock=clock)  # default deny
    registered = _PROVIDERS.get(feature_id)
    if registered is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    provider, provider_model, provider_prompt, provider_tools = registered
    wanted = list(tools or [])
    shadow = permit.state == PermitState.SHADOW
    names = [str(t.get("tool", "")) for t in wanted]
    flags: list[str] = []
    if len(wanted) > permit.max_steps:
        flags.append("too_many_steps")
    for name in names:
        if name in FORBIDDEN_TOOLS:
            flags.append("forbidden_tool")
        elif name not in permit.allowed_tools:
            flags.append("tool_not_on_list")
    if shadow and wanted:
        flags.append("shadow_acts_on_nothing")
    if (
        provider_model not in (None, permit.model_digest)
        or provider_prompt not in (None, permit.prompt_digest)
        or provider_tools not in (None, permit.tool_digest)
    ):
        flags.append("digest_mismatch")  # what answers is not what the permit approved
    if permit.tool_digest and permit.tool_digest != tool_digest(
        permit.allowed_tools, permit.write_tools, permit.two_person_tools
    ):
        flags.append("tool_policy_changed")  # the tool lists are not what the permit was made with
    output = provider(input_text) if not flags else ""
    if not flags and not output.strip():
        flags.append("empty_output")
    decision = (
        Decision.REJECTED
        if any(f != "empty_output" for f in flags)
        else Decision.QUARANTINED
        if flags
        else Decision.HUMAN_REVIEW
    )
    with transaction.atomic():
        req = AiRequest.objects.create(
            permit_ref=permit.public_id,
            actor_ref=actor_ref,
            purpose_code=purpose_code,
            mode="shadow" if shadow else "live",
            input_digest=digest(input_text),
            requested_tools=names,
            correlation_id=correlation_id[:64],
            **_scope(),
        )
        AiResponseEvidence.objects.create(
            request_ref=req.public_id,
            output_digest=digest(output),
            model_digest=permit.model_digest,
            prompt_digest=permit.prompt_digest,
            tool_digest=permit.tool_digest,
            validator_flags=flags,
            decision=decision,
            **_scope(),
        )
        approvals: list[str] = []
        if decision == Decision.HUMAN_REVIEW:  # a shadow run with tools was rejected above
            for item in wanted:
                if item["tool"] in permit.write_tools:
                    a = AiActionApproval.objects.create(
                        request_ref=req.public_id,
                        permit_ref=permit.public_id,
                        tool=item["tool"],
                        payload_digest=digest(json.dumps(item.get("payload", {}), sort_keys=True)),
                        proposed_by_ref=actor_ref,
                        needs_two=item["tool"] in permit.two_person_tools,
                        **_scope(),
                    )
                    approvals.append(a.public_id)
        record(
            actor_ref=actor_ref,
            action="ai.run",
            object_type="ai_request",
            object_ref=req.public_id,
            outcome=AuditOutcome.ALLOWED if decision != Decision.REJECTED else AuditOutcome.DENIED,
            reason_code=decision.lower(),
        )
    return {
        "request_id": req.public_id,
        "mode": req.mode,
        "decision": decision,
        "flags": flags,
        "output": output if decision == Decision.HUMAN_REVIEW else "",
        "approvals": approvals,
    }


def decide(
    approval_ref: str, *, approver_ref: str, approve: bool, clock: Clock | None = None
) -> dict[str, Any]:
    """A named human approves or rejects a proposed write. Never the one who proposed it.

    Approving needs the permit to still be AUTHORIZED and unexpired: a suspended or expired
    feature cannot have old proposals approved. Refusing a proposal is always allowed.
    """
    with transaction.atomic():
        a = (
            AiActionApproval.objects.select_for_update()
            .filter(public_id=approval_ref, **_scope())
            .first()
        )
        if a is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if a.state != ApprovalState.PENDING:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if not approver_ref or approver_ref == a.proposed_by_ref or approver_ref in a.approver_refs:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if not approve:
            a.state, a.decided_by_ref = ApprovalState.REJECTED, approver_ref
        else:
            permit = AiUsePermit.objects.filter(public_id=a.permit_ref, **_scope()).first()
            now = utc_now(clock)
            if (
                permit is None
                or permit.state != PermitState.AUTHORIZED
                or permit.expiry is None
                or permit.expiry <= now
            ):
                raise ZoikoError(ErrorCode.POLICY_DENIED)
            a.approver_refs = [*a.approver_refs, approver_ref]
            if len(a.approver_refs) >= (2 if a.needs_two else 1):
                a.state, a.decided_by_ref = ApprovalState.APPROVED, approver_ref
        a.save(update_fields=["state", "approver_refs", "decided_by_ref", "updated_at"])
        record(
            actor_ref=approver_ref,
            action=f"ai.approval.{'yes' if approve else 'no'}",
            object_type="ai_approval",
            object_ref=a.public_id,
            outcome=AuditOutcome.ALLOWED,
        )
    return {"id": a.public_id, "state": a.state, "tool": a.tool, "approvals": len(a.approver_refs)}
