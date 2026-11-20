"""Employer programme links. Every function runs with the PLATFORM tenant bound; the caller (an API
view) has checked the caller's rights in their own space. An employer never learns who a member is,
which clinic they use or what they used: only totals, computed elsewhere, and only for groups of the
minimum size or more."""

from __future__ import annotations

import datetime as dt
import re
import secrets
from typing import Any

from django.db import IntegrityError, transaction

from foundation import pagination
from foundation.cell_context import require_context
from foundation.crypto import blind_index
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import CODE_DAYS, LinkState, ProgrammeLink

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0, O, 1, I or L
_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
_PERSON = re.compile(r"^[A-Za-z0-9_-]{8,22}$")
_CONTEXT = "programme:code"


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _audit(actor: str, action: str, link: ProgrammeLink) -> None:
    record(
        actor_ref=actor,
        action=action,
        object_type="programme_link",
        object_ref=link.public_id,
        outcome=AuditOutcome.ALLOWED,
    )


def _normal(code: Any) -> str:
    if not isinstance(code, str):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    text = re.sub(r"[\s-]", "", code).upper()
    if len(text) != 10 or any(ch not in _ALPHABET for ch in text):
        raise ZoikoError(ErrorCode.NOT_FOUND)  # the same answer as a wrong code
    return text


def _key(code: str) -> str:
    return blind_index(code, tenant_id=require_context().tenant_id, context=_CONTEXT)


def employer_dict(link: ProgrammeLink) -> dict[str, Any]:
    """What the employer sees: its own member reference and where the code stands. Never the
    clinic, the patient or the code."""
    return {
        "id": link.public_id,
        "member_ref": link.member_ref,
        "state": link.state,
        "expires_at": link.expires_at.isoformat(),
        "redeemed_at": link.redeemed_at.isoformat() if link.redeemed_at else None,
    }


def patient_dict(link: ProgrammeLink) -> dict[str, Any]:
    """What the patient sees: which employer they are linked to and since when. Never the
    member reference the employer holds."""
    return {
        "id": link.public_id,
        "employer": link.employer_name,
        "person": link.person_ref,
        "linked_at": link.redeemed_at.isoformat() if link.redeemed_at else None,
    }


def issue(
    *,
    employer_tenant_ref: str,
    employer_name: str,
    member_ref: Any,
    actor_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Make a one-time code for one enrolled member. Shown ONCE; only a fingerprint is kept."""
    if not isinstance(member_ref, str) or not _MEMBER.match(member_ref):
        raise _bad("member_ref")
    now = utc_now(clock)
    code = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    with transaction.atomic():
        live = (
            ProgrammeLink.objects.select_for_update()
            .filter(
                employer_tenant_ref=employer_tenant_ref,
                member_ref=member_ref,
                state__in=(LinkState.ISSUED, LinkState.REDEEMED),
                **_scope(),
            )
            .first()
        )
        if live is not None:
            if live.state == LinkState.REDEEMED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # already linked: revoke first
            live.state = LinkState.REVOKED  # a new code replaces an unused one
            live.entity_version += 1
            live.save(update_fields=["state", "entity_version", "updated_at"])
        try:
            link = ProgrammeLink.objects.create(
                employer_tenant_ref=employer_tenant_ref,
                employer_name=employer_name,
                member_ref=member_ref,
                code_key=_key(code),
                expires_at=now + dt.timedelta(days=CODE_DAYS),
                created_by_ref=actor_ref,
                **_scope(),
            )
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(actor_ref, "programme.code_issued", link)
    return {**employer_dict(link), "code": f"{code[:5]}-{code[5:]}"}


def revoke(*, employer_tenant_ref: str, member_ref: Any, actor_ref: str) -> dict[str, Any]:
    """Cancel an unused code, or end a link; that patient's activity stops counting at once."""
    if not isinstance(member_ref, str) or not _MEMBER.match(member_ref):
        raise _bad("member_ref")
    with transaction.atomic():
        link = (
            ProgrammeLink.objects.select_for_update()
            .filter(
                employer_tenant_ref=employer_tenant_ref,
                member_ref=member_ref,
                state__in=(LinkState.ISSUED, LinkState.REDEEMED),
                **_scope(),
            )
            .first()
        )
        if link is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        link.state = LinkState.REVOKED
        link.entity_version += 1
        link.save(update_fields=["state", "entity_version", "updated_at"])
        _audit(actor_ref, "programme.revoked", link)
        return employer_dict(link)


def list_for_employer(*, employer_tenant_ref: str) -> list[dict[str, Any]]:
    rows = ProgrammeLink.objects.filter(
        employer_tenant_ref=employer_tenant_ref, **_scope()
    ).order_by("-created_at")
    return [employer_dict(r) for r in rows[:500]]


def page_for_employer(
    *, employer_tenant_ref: str, limit: int, cursor: str | None, actor_ref: str
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the employer's codes, newest first (DEV-320)."""
    found, nxt = pagination.page(
        ProgrammeLink.objects.filter(employer_tenant_ref=employer_tenant_ref, **_scope()),
        limit=limit,
        cursor=cursor,
        actor=actor_ref,
        lst="programme.codes",
    )
    return [employer_dict(r) for r in found], nxt


def redeem(
    *,
    code: Any,
    clinic_tenant_ref: str,
    person_ref: str,
    actor_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A patient types a code and agrees to be counted. Any wrong, used, expired or revoked code
    gets the same 'not found', so a code cannot be guessed at."""
    if not isinstance(person_ref, str) or not _PERSON.match(person_ref):
        raise _bad("patient")
    key = _key(_normal(code))
    now = utc_now(clock)
    with transaction.atomic():
        link = (
            ProgrammeLink.objects.select_for_update()
            .filter(code_key=key, state=LinkState.ISSUED, expires_at__gt=now, **_scope())
            .first()
        )
        if link is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if ProgrammeLink.objects.filter(
            employer_tenant_ref=link.employer_tenant_ref,
            person_ref=person_ref,
            state=LinkState.REDEEMED,
            **_scope(),
        ).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # this patient is already counted
        link.state, link.clinic_tenant_ref, link.person_ref = (
            LinkState.REDEEMED,
            clinic_tenant_ref,
            person_ref,
        )
        link.redeemed_at = now
        link.entity_version += 1
        link.save()
        _audit(actor_ref, "programme.linked", link)
        return patient_dict(link)


def list_for_patients(*, clinic_tenant_ref: str, person_refs: list[str]) -> list[dict[str, Any]]:
    rows = ProgrammeLink.objects.filter(
        clinic_tenant_ref=clinic_tenant_ref,
        person_ref__in=person_refs,
        state=LinkState.REDEEMED,
        **_scope(),
    ).order_by("-redeemed_at")
    return [patient_dict(r) for r in rows]


def leave(
    *, link_ref: str, clinic_tenant_ref: str, person_refs: list[str], actor_ref: str
) -> dict[str, Any]:
    """The patient stops being counted for that employer."""
    with transaction.atomic():
        link = (
            ProgrammeLink.objects.select_for_update()
            .filter(
                public_id=link_ref,
                clinic_tenant_ref=clinic_tenant_ref,
                person_ref__in=person_refs,
                state=LinkState.REDEEMED,
                **_scope(),
            )
            .first()
        )
        if link is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        link.state = LinkState.REVOKED
        link.entity_version += 1
        link.save(update_fields=["state", "entity_version", "updated_at"])
        _audit(actor_ref, "programme.left", link)
        return patient_dict(link)


def person_of(link_ref: str, clinic_tenant_ref: str) -> str:
    link = ProgrammeLink.objects.filter(
        public_id=link_ref, clinic_tenant_ref=clinic_tenant_ref, **_scope()
    ).first()
    if link is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return link.person_ref


def linked_by_clinic(*, employer_tenant_ref: str) -> dict[str, list[str]]:
    """For the platform's own counting: which patients, at which clinics, are linked to this
    employer. Never returned to the employer."""
    out: dict[str, list[str]] = {}
    rows = ProgrammeLink.objects.filter(
        employer_tenant_ref=employer_tenant_ref, state=LinkState.REDEEMED, **_scope()
    )
    for r in rows:
        out.setdefault(r.clinic_tenant_ref, []).append(r.person_ref)
    return out
