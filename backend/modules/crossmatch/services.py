"""Matching a patient between separate customers (DEV-363). Every function here runs in Zoiko's
platform space; the caller passes in what it read in the customer's own space.

Rules held:
- Only customers that chose to take part are compared, and only patients who did not say no.
- Only fingerprints cross: HMAC under a key derived for the platform (foundation.crypto.blind_index
  with the platform's id), never the details themselves.
- A possible match needs the date of birth AND the surname to agree; a phone adds to it, alone it
  is not enough.
- Each organization answers for its own patient. Only when BOTH say "same" does either learn the
  other's name. Either saying "not the same" closes it. Nothing is ever merged.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.crypto import blind_index
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record

from .models import CaseState, CrossCase, Participation, SharedKey

ANSWERS = ("same", "not_same")
MAX_CANDIDATES = 10


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]+", " ", plain).strip()


def fingerprints(*, name: str, birth_date: str, phone: str) -> dict[str, str]:
    """The fingerprints of one patient's details, under the platform's own key."""
    ctx = require_context()
    out: dict[str, str] = {}
    tokens = _norm(name).split()
    if tokens and re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth_date or ""):
        out["birth_surname"] = blind_index(f"{birth_date}|{tokens[-1]}", tenant_id=ctx.tenant_id,
                                           context="crossmatch:birth_surname")
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) >= 7:
        out["phone"] = blind_index(digits[-10:], tenant_id=ctx.tenant_id,
                                   context="crossmatch:phone")
    return out


# ------------------------------------------------------------------ taking part
def participating(customer_ref: str) -> bool:
    return Participation.objects.filter(customer_ref=customer_ref, active=True,
                                        **_scope()).exists()


def set_participation(*, customer_ref: str, customer_name: str, active: bool, by: str) -> bool:
    """Take part, or stop. Stopping removes every fingerprint of this customer at once."""
    if not isinstance(active, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["active"])
    with transaction.atomic():
        row, _ = Participation.objects.get_or_create(
            customer_ref=customer_ref,
            defaults={"customer_name": customer_name, "changed_by_ref": by}, **_scope())
        if row.active != active or row.customer_name != customer_name:
            row.active, row.customer_name, row.changed_by_ref = active, customer_name, by
            row.entity_version += 1
            row.save()
        if not active:
            SharedKey.objects.filter(customer_ref=customer_ref, **_scope()).delete()
        record(actor_ref=by, action=f"crossmatch.{'joined' if active else 'left'}",
               object_type="customer", object_ref=customer_ref, outcome=AuditOutcome.ALLOWED)
    return active


def forget(customer_ref: str, person_ref: str) -> None:
    """This patient said no, or their details are gone: their fingerprints go."""
    SharedKey.objects.filter(customer_ref=customer_ref, person_ref=person_ref, **_scope()).delete()


# ------------------------------------------------------------------ fingerprints and matches
def share(*, customer_ref: str, person_ref: str, name: str, birth_date: str, phone: str
          ) -> list[str]:
    """Replace this patient's fingerprints and open a case for each new possible match in another
    taking-part customer. Returns the case references opened."""
    if not participating(customer_ref):
        return []
    keys = fingerprints(name=name, birth_date=birth_date, phone=phone)
    opened: list[str] = []
    with transaction.atomic():
        forget(customer_ref, person_ref)
        for kind, key in keys.items():
            SharedKey.objects.create(customer_ref=customer_ref, person_ref=person_ref, kind=kind,
                                     match_key=key, **_scope())
        if "birth_surname" not in keys:
            return []
        active = set(Participation.objects.filter(active=True, **_scope())
                     .values_list("customer_ref", flat=True))
        others = (SharedKey.objects.filter(kind="birth_surname", match_key=keys["birth_surname"],
                                           customer_ref__in=active, **_scope())
                  .exclude(customer_ref=customer_ref)
                  .values_list("customer_ref", "person_ref"))[:MAX_CANDIDATES]
        for other_customer, other_person in others:
            factors = ["birth_date", "surname"]
            if "phone" in keys and SharedKey.objects.filter(
                    customer_ref=other_customer, person_ref=other_person, kind="phone",
                    match_key=keys["phone"], **_scope()).exists():
                factors.append("phone")
            sides = sorted([(customer_ref, person_ref), (other_customer, other_person)])
            try:
                with transaction.atomic():
                    case = CrossCase.objects.create(
                        customer_a_ref=sides[0][0], person_a_ref=sides[0][1],
                        customer_b_ref=sides[1][0], person_b_ref=sides[1][1], factors=factors,
                        **_scope())
            except IntegrityError:
                continue                                        # already known: not again
            record(actor_ref="system", action="crossmatch.opened", object_type="cross_match",
                   object_ref=case.public_id, outcome=AuditOutcome.ALLOWED)
            opened.append(case.public_id)
    return opened


def _side(case: CrossCase, customer_ref: str) -> str:
    if case.customer_a_ref == customer_ref:
        return "a"
    if case.customer_b_ref == customer_ref:
        return "b"
    raise ZoikoError(ErrorCode.NOT_FOUND)                        # not this customer's case


def case_for(case: CrossCase, customer_ref: str, names: dict[str, str]) -> dict[str, Any]:
    """One case as one customer sees it: their own patient, never the other's."""
    mine = _side(case, customer_ref)
    other = "b" if mine == "a" else "a"
    other_customer = getattr(case, f"customer_{other}_ref")
    return {
        "id": case.public_id, "state": case.state, "factors": list(case.factors),
        "person": getattr(case, f"person_{mine}_ref"),
        "my_answer": getattr(case, f"{mine}_answer"),
        "their_answer_given": bool(getattr(case, f"{other}_answer")),
        # Only once both said "same" does either side learn who the other is.
        "other_organization": (names.get(other_customer, "Another organization")
                               if case.state == CaseState.CONFIRMED else None),
        "created_at": case.created_at.isoformat(),
    }


def list_for(customer_ref: str) -> list[dict[str, Any]]:
    from django.db.models import Q

    names = dict(Participation.objects.filter(**_scope())
                 .values_list("customer_ref", "customer_name"))
    rows = CrossCase.objects.filter(
        Q(customer_a_ref=customer_ref) | Q(customer_b_ref=customer_ref), **_scope()
    ).order_by("-created_at")[:200]
    return [case_for(c, customer_ref, names) for c in rows]


def get_case(case_ref: str, customer_ref: str) -> dict[str, Any]:
    case = CrossCase.objects.filter(public_id=case_ref, **_scope()).first()
    if case is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return case_for(case, customer_ref, {})


def answer(*, case_ref: str, customer_ref: str, answer: str, by: str) -> dict[str, Any]:
    """This customer's answer for its own patient. The caller has checked that `by` may decide
    about that patient at this customer."""
    if answer not in ANSWERS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["answer"])
    with transaction.atomic():
        case = CrossCase.objects.select_for_update().filter(public_id=case_ref, **_scope()).first()
        if case is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        mine = _side(case, customer_ref)
        if case.state != CaseState.OPEN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        setattr(case, f"{mine}_answer", answer)
        setattr(case, f"{mine}_answered_by_ref", by)
        if answer == "not_same":
            case.state = CaseState.NOT_SAME
        elif case.a_answer == "same" and case.b_answer == "same":
            case.state = CaseState.CONFIRMED
        case.entity_version += 1
        case.save()
        record(actor_ref=by, action=f"crossmatch.{answer}", object_type="cross_match",
               object_ref=case.public_id, outcome=AuditOutcome.ALLOWED, reason_code=case.state)
        names = dict(Participation.objects.filter(**_scope())
                     .values_list("customer_ref", "customer_name"))
        return case_for(case, customer_ref, names)
