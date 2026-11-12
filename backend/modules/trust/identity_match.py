"""Possible duplicate patient records, and the people who decide (GHE-06 sections 04 and W03).

Rules this follows, from the specification:
- A possible match is HELD for a human. Nothing is ever merged automatically, and no data is merged
  at all: a confirmed link records the decision and nothing else.
- Two reviewers when it is critical (a wrong link could harm someone), and nobody reviews a case
  about themselves or someone they act for (separation of duties).
- "Not the same" is remembered: the pair is not proposed again unless new evidence appears.
- A confirmed link can be sent back and split, with its whole history kept.
- The review screen shows how the records COMPARED and only masked details (initials, birth year,
  last digits), never full names, to keep demographic exposure small.

The thresholds below are our own proposed starting point (policy version m1): the specification says
they must be tuned and validated on real data per market, which has not been done (DEV-129).
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q

from foundation.cell_context import require_context
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.crypto import blind_index
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now

from .models import (
    OPEN_MATCH_STATES,
    CareRelationship,
    MatchAction,
    MatchCase,
    MatchDecision,
    MatchState,
    Person,
    PersonProfile,
    PersonSite,
    ProxyGrant,
    ProxyStatus,
    Status,
)
from .services import _audit

POLICY_VERSION = "m1-2026-11-proposed"
MAX_CANDIDATES = 25
NOTES = ("same_details", "different_people", "twins", "data_entry_error", "other")
ACTIONS = ("request_recheck", "confirm_same", "mark_not_same", "split")
CLOSED_SHOWN_DAYS = 30


# --------------------------------------------------------------------------- comparing details


def _norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in plain if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def name_tokens(name: str) -> list[str]:
    return _norm(name).split()


def compare_names(a: str, b: str) -> str:
    """same: the same words in any order; close: nearly the same (a typo or a variant spelling);
    differ; missing."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return "missing"
    if sorted(ta) == sorted(tb):
        return "same"
    if difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.85:
        return "close"
    return "differ"


def same_surname(a: str, b: str) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    return bool(ta and tb and ta[-1] == tb[-1])


def transposed_variants(birth_date: str) -> set[str]:
    """Every birth date that is this one with two neighbouring digits swapped, or with the day and
    month swapped: the slips a person makes copying a date (kept as text; some are not real
    dates)."""
    try:
        year, month, day = birth_date.split("-")
    except ValueError:
        return set()
    digits = year + month + day
    found = {f"{year}-{day}-{month}"}
    for i in range(len(digits) - 1):
        if digits[i] != digits[i + 1]:
            swapped = digits[:i] + digits[i + 1] + digits[i] + digits[i + 2:]
            found.add(f"{swapped[:4]}-{swapped[4:6]}-{swapped[6:]}")
    found.discard(birth_date)
    return found


def compare_birth(a: str, b: str) -> str:
    if not a or not b:
        return "missing"
    if a == b:
        return "same"
    return "transposed" if b in transposed_variants(a) else "differ"


def _phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 7 else ""


def compare_phone(a: str, b: str) -> str:
    da, db = _phone_digits(a), _phone_digits(b)
    if not da or not db:
        return "missing"
    return "same" if da == db else "differ"


@dataclass(frozen=True)
class Evaluation:
    factors: dict[str, str]
    flags: list[str]
    confidence: str


def evaluate(a: dict[str, str], b: dict[str, str]) -> Evaluation | None:
    """Do these two sets of details look like one person? None means no. The rule is
    deterministic and written down: a matching (or nearly matching) name with a matching (or
    slipped) birth date; or a matching birth date with a matching phone; or a matching name with
    a matching phone. Anything else is not a candidate."""
    name = compare_names(a.get("name", ""), b.get("name", ""))
    birth = compare_birth(a.get("birth_date", ""), b.get("birth_date", ""))
    phone = compare_phone(a.get("phone", ""), b.get("phone", ""))
    possible = (
        (name in ("same", "close") and birth in ("same", "transposed"))
        or (birth == "same" and phone == "same")
        or (name == "same" and phone == "same"))
    if not possible:
        return None
    flags: list[str] = []
    if birth == "same" and name in ("close", "differ") and same_surname(
            a.get("name", ""), b.get("name", "")):
        flags.append("twin")                      # same birth date, same family name: maybe twins
    if birth == "transposed":
        flags.append("transposition")
    if birth == "same" and phone == "same" and name == "differ" and "twin" not in flags:
        flags.append("name_change")
    if phone == "same" and birth == "differ":
        flags.append("shared_phone")
    confidence = "high" if (name == "same" and birth == "same") else "medium"
    return Evaluation({"name": name, "birth_date": birth, "phone": phone}, flags, confidence)


# --------------------------------------------------------------------------- fingerprints


def birth_key(tenant_id: Any, birth_date: str) -> str:
    return blind_index(birth_date, tenant_id=tenant_id, context="match:birth") if birth_date else ""


def phone_key(tenant_id: Any, phone: str) -> str:
    digits = _phone_digits(phone)
    return blind_index(digits, tenant_id=tenant_id, context="match:phone") if digits else ""


def _evidence_digest(tenant_id: Any, a: dict[str, str], b: dict[str, str]) -> str:
    """A fingerprint of what was compared, so a closed case can tell if the details changed."""
    def part(p: dict[str, str]) -> list[str]:
        return [
            blind_index(" ".join(sorted(name_tokens(p.get("name", "")))), tenant_id=tenant_id,
                        context="match:name"),
            birth_key(tenant_id, p.get("birth_date", "")), phone_key(tenant_id, p.get("phone", ""))]

    return hashlib.sha256(json.dumps(sorted([part(a), part(b)])).encode()).hexdigest()


def _plain(person: Person) -> dict[str, str]:
    from .profile import get_person_profiles  # (imported here: profile.py imports this one)

    return get_person_profiles([person.public_id]).get(
        person.public_id, {"name": "", "birth_date": "", "phone": ""})


# --------------------------------------------------------------------------- finding candidates


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _pair(a: Person, b: Person) -> tuple[Person, Person]:
    return (a, b) if str(a.id) < str(b.id) else (b, a)


def _critical(a: Person, b: Person, flags: list[str]) -> bool:
    """A wrong link could harm someone: the records might be twins or a date slip, or one of them
    already has a doctor or a guardian relying on it. (Our own proposed definition.)"""
    if {"twin", "transposition"} & set(flags):
        return True
    for person in (a, b):
        if (CareRelationship.objects.filter(
                person=person, status=Status.ACTIVE, **_scope()).exists()
                or ProxyGrant.objects.filter(
                    person=person, status=ProxyStatus.ACTIVE, **_scope()).exists()):
            return True
    return False


def _parts(row: MatchDecision) -> list[Any]:
    return [row.case.ref_no, row.action, row.state_after, row.actor_ref, row.note,
            row.occurred_at.isoformat()]


def _log(
    case: MatchCase, action: str, actor_ref: str, note: str, now: dt.datetime
) -> None:
    last = MatchDecision.objects.filter(case=case, **_scope()).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    digest = chain_digest(prev, seq, case.ref_no, action, case.state, actor_ref, note,
                          now.isoformat())
    try:
        with transaction.atomic():
            MatchDecision.objects.create(
                case=case, seq=seq, action=action, state_after=case.state, actor_ref=actor_ref,
                note=note, occurred_at=now, prev_digest=prev, record_digest=digest, **_scope())
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None


def _announce(case: MatchCase, kind: str) -> None:
    emit(event_type=f"patient.link.{kind}.v1", aggregate_type="match_case",
         aggregate_ref=case.public_id,
         payload={"case_ref": case.public_id, "state": case.state},
         dedupe_key=f"match:{case.public_id}:{case.state}:{case.entity_version}")


def _next_ref() -> str:
    return f"M-{MatchCase.objects.filter(**_scope()).count() + 1:04d}"


def _open_case(
    a: Person, b: Person, ev: Evaluation, digest: str, now: dt.datetime
) -> MatchCase | None:
    pa, pb = _pair(a, b)
    existing = MatchCase.objects.select_for_update().filter(
        person_a=pa, person_b=pb, **_scope()).first()
    if existing is not None:
        if existing.state == MatchState.CLOSED_NOT_SAME and existing.evidence_digest != digest:
            existing.state = MatchState.POSSIBLE_MATCH      # new evidence: look again
            existing.factors, existing.conflict_flags = ev.factors, ev.flags
            existing.confidence = ev.confidence
            existing.critical = _critical(pa, pb, ev.flags)
            existing.first_reviewer_ref = ""
            existing.entity_version += 1
            existing.save()
            _log(existing, MatchAction.REOPENED, "system", "", now)
            _audit("system", "match.reopen", "match_case", existing.public_id)
            _announce(existing, "review_required")
            return existing
        return None                                         # still open, linked, or not the same
    for attempt in range(5):
        try:
            with transaction.atomic():
                case = MatchCase.objects.create(
                    ref_no=f"M-{MatchCase.objects.filter(**_scope()).count() + 1 + attempt:04d}",
                    person_a=pa, person_b=pb, factors=ev.factors, conflict_flags=ev.flags,
                    confidence=ev.confidence, critical=_critical(pa, pb, ev.flags),
                    policy_version=POLICY_VERSION, evidence_digest=digest, **_scope())
        except IntegrityError:
            continue
        _log(case, MatchAction.OPENED, "system", "", now)
        _audit("system", "match.open", "match_case", case.public_id)
        _announce(case, "review_required")
        return case
    raise ZoikoError(ErrorCode.CONFLICT_VERSION)


def scan_person(person: Person, *, clock: Clock | None = None) -> list[str]:
    """Look for records that might be this person, after their details were saved. Opens a case
    (held for review) for each new candidate; returns their references. Never changes a record."""
    ctx = require_context()
    row = PersonProfile.objects.filter(person=person, **_scope()).first()
    if row is None or person.status != Status.ACTIVE:
        return []
    mine = _plain(person)
    wanted = Q()
    if row.birth_key:
        keys = {row.birth_key} | {birth_key(ctx.tenant_id, v)
                                  for v in transposed_variants(mine["birth_date"])}
        wanted |= Q(birth_key__in=keys)
    if row.phone_key:
        wanted |= Q(phone_key=row.phone_key)
    if not wanted:
        return []
    now = utc_now(clock)
    opened: list[str] = []
    others = (PersonProfile.objects.select_related("person")
              .filter(wanted, person__status=Status.ACTIVE, **_scope())
              .exclude(person=person).order_by("created_at")[:MAX_CANDIDATES])
    for other in others:
        theirs = _plain(other.person)
        ev = evaluate(mine, theirs)
        if ev is None:
            continue
        case = _open_case(person, other.person, ev, _evidence_digest(ctx.tenant_id, mine, theirs),
                          now)
        if case is not None:
            opened.append(case.public_id)
    return opened


def rebuild_match_keys() -> int:
    """Recompute the search fingerprints of every patient of the bound customer (after a key
    rotation, or for details saved before they existed). Returns how many were rebuilt."""
    ctx = require_context()
    count = 0
    for row in PersonProfile.objects.select_related("person").filter(**_scope()):
        plain = _plain(row.person)
        row.birth_key = birth_key(ctx.tenant_id, plain["birth_date"])
        row.phone_key = phone_key(ctx.tenant_id, plain["phone"])
        row.save(update_fields=["birth_key", "phone_key", "updated_at"])
        count += 1
    return count


# --------------------------------------------------------------------------- deciding


def _visible(case: MatchCase, organization_ref: str) -> bool:
    return PersonSite.objects.filter(
        person_id__in=[case.person_a_id, case.person_b_id], organization_ref=organization_ref,
        **_scope()).exists()


def _is_party(actor_public_id: str, case: MatchCase) -> bool:
    """Is this login one of the two patients, or acting for one of them?"""
    people = [case.person_a_id, case.person_b_id]
    return (Person.objects.filter(
        id__in=people, linked_actor__public_id=actor_public_id, **_scope()).exists()
        or ProxyGrant.objects.filter(
            person_id__in=people, delegate__public_id=actor_public_id, **_scope()).exists())


def decide(
    *, case_ref: str, action: str, actor_public_id: str, organization_ref: str, note: str = "",
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A reviewer's decision on a case. The caller has authorized the person to review patients of
    this clinic. Raises CONFLICT_VERSION for a decision the case's state does not allow."""
    if action not in ACTIONS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["action"])
    if note and note not in NOTES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
    now = utc_now(clock)
    with transaction.atomic():
        case = (MatchCase.objects.select_for_update(of=("self",))
                .select_related("person_a", "person_b")
                .filter(public_id=case_ref, **_scope()).first())
        if case is None or not _visible(case, organization_ref):
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if _is_party(actor_public_id, case):
            raise ZoikoError(ErrorCode.POLICY_DENIED)       # nobody decides about themselves
        state, new = case.state, case.state
        if action == "request_recheck":
            if state not in OPEN_MATCH_STATES:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            case.recheck_requested = True
        elif action == "mark_not_same":
            if state not in OPEN_MATCH_STATES:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            new = MatchState.CLOSED_NOT_SAME
            case.first_reviewer_ref = ""
            ctx = require_context()
            case.evidence_digest = _evidence_digest(
                ctx.tenant_id, _plain(case.person_a), _plain(case.person_b))
        elif action == "confirm_same":
            if state in (MatchState.POSSIBLE_MATCH, MatchState.SPLIT_REVIEW):
                if case.critical:
                    new, case.first_reviewer_ref = MatchState.MATCHED_PENDING_AUTH, actor_public_id
                else:
                    new = MatchState.LINKED
            elif state == MatchState.MATCHED_PENDING_AUTH:
                if actor_public_id == case.first_reviewer_ref:
                    raise ZoikoError(ErrorCode.POLICY_DENIED)   # a second, different reviewer
                new = MatchState.LINKED
            else:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        else:                                                   # split
            if state != MatchState.LINKED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            new, case.first_reviewer_ref = MatchState.SPLIT_REVIEW, ""
        case.state = new
        case.entity_version += 1
        case.save()
        _log(case, action, actor_public_id, note, now)
        _audit(actor_public_id, f"match.{action}", "match_case", case.public_id)
        if new != state:
            _announce(case, "corrected")
        return case_dict(case)


# --------------------------------------------------------------------------- reading


def _mask(plain: dict[str, str]) -> dict[str, str]:
    """All a reviewer sees of a patient: initials, birth year, last two phone digits."""
    initials = " ".join(f"{t[0].upper()}." for t in name_tokens(plain.get("name", "")))
    year = (plain.get("birth_date") or "")[:4]
    digits = _phone_digits(plain.get("phone", ""))
    return {"initials": initials, "birth_year": year, "phone_end": digits[-2:] if digits else ""}


def case_dict(case: MatchCase) -> dict[str, Any]:
    return {
        "public_id": case.public_id, "ref_no": case.ref_no, "state": case.state,
        "factors": dict(case.factors), "flags": list(case.conflict_flags),
        "confidence": case.confidence, "critical": case.critical,
        "recheck_requested": case.recheck_requested,
        "first_reviewer_ref": case.first_reviewer_ref, "policy_version": case.policy_version,
        "created_at": case.created_at.isoformat(), "updated_at": case.updated_at.isoformat(),
    }


def list_cases(
    organization_ref: str, *, clock: Clock | None = None
) -> list[dict[str, Any]]:
    """The cases about patients of this clinic: everything still open, confirmed links, and what
    was closed in the last month. Masked: no names, only how the records compared."""
    scope = _scope()
    here = list(PersonSite.objects.filter(
        organization_ref=organization_ref, **scope).values_list("person_id", flat=True))
    since = utc_now(clock) - dt.timedelta(days=CLOSED_SHOWN_DAYS)
    rows = (MatchCase.objects.select_related("person_a", "person_b")
            .filter(Q(person_a_id__in=here) | Q(person_b_id__in=here), **scope)
            .filter(~Q(state=MatchState.CLOSED_NOT_SAME) | Q(updated_at__gte=since))
            .order_by("-created_at")[:200])
    from .profile import get_person_profiles

    refs = {r.person_a.public_id for r in rows} | {r.person_b.public_id for r in rows}
    plain = get_person_profiles(sorted(refs))
    out = []
    for r in rows:
        decisions = list(MatchDecision.objects.select_related("case").filter(
            case=r, **scope).order_by("seq"))
        ok, bad = verify_chain(decisions, _parts)
        out.append(case_dict(r) | {
            "people": [
                {"id": p.public_id, "at_this_clinic": p.id in here,
                 **_mask(plain.get(p.public_id, {}))} for p in (r.person_a, r.person_b)],
            "history": [{"seq": d.seq, "action": d.action, "state_after": d.state_after,
                         "actor_ref": d.actor_ref, "note": d.note,
                         "occurred_at": d.occurred_at.isoformat()} for d in decisions],
            "chain_ok": ok, "bad_seq": bad})
    return out


def open_case_people(person_refs: list[str]) -> set[str]:
    """Which of these patients are in a case that still needs a decision."""
    if not person_refs:
        return set()
    scope = _scope()
    found: set[str] = set()
    rows = MatchCase.objects.select_related("person_a", "person_b").filter(
        state__in=OPEN_MATCH_STATES, **scope).filter(
        Q(person_a__public_id__in=person_refs) | Q(person_b__public_id__in=person_refs))
    for r in rows:
        found |= {r.person_a.public_id, r.person_b.public_id}
    return found & set(person_refs)


def linked_people(person_refs: list[str]) -> set[str]:
    """Which of these patients are confirmed to be the same person as another record."""
    if not person_refs:
        return set()
    found: set[str] = set()
    for r in MatchCase.objects.select_related("person_a", "person_b").filter(
            state=MatchState.LINKED, **_scope()).filter(
            Q(person_a__public_id__in=person_refs) | Q(person_b__public_id__in=person_refs)):
        found |= {r.person_a.public_id, r.person_b.public_id}
    return found & set(person_refs)
