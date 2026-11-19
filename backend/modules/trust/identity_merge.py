"""Merging a confirmed duplicate patient record into the surviving one, and undoing it (GHE-06).

A merge is a SEPARATE, deliberate step after reviewers confirm two records are the same person:
confirming alone still moves nothing. Rules this follows:

- Only a case in the "linked" state (so, with two reviewers where it was critical) may be merged,
  and nobody merges a case about themselves or someone they act for.
- Nothing is deleted. The ended record is kept with a pointer to the survivor, and a journal lists
  every row that moved, so the merge can be undone exactly.
- Patient-facing records move to the survivor (bookings, waiting list entries, referrals, message
  threads, notifications, insurance records), as does the clinic connection and, if the survivor
  has none, the secondary's login. A row that would clash with one the survivor already has stays
  where it is and is counted.
- ACCESS is not carried over. Guardian grants and doctor care links belong to a person's own record
  and were approved for it: they stay on the ended record and must be approved again for the
  survivor. The survivor keeps its own identity check and its own details. Consent and sharing
  choices, billing, data-rights requests and partner exchanges are left alone.
- Two records that both have a login are not merged: one login must be retired first.
- Unmerge moves back exactly what moved, and nothing created since. The case returns to "under
  review" so the decision is looked at again.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation import person_refs
from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now

from .identity_match import _announce, _is_party, _log, _scope, _visible, case_dict
from .models import (
    CareRelationship,
    MatchCase,
    MatchState,
    MergeState,
    Person,
    PersonMerge,
    PersonMergeItem,
    PersonSite,
    ProxyGrant,
    ProxyStatus,
    Status,
)
from .services import _audit

SITE_KIND = "trust.PersonSite"
MAX_CHAIN = 5


def _lock_case(case_ref: str, organization_ref: str, actor_public_id: str) -> MatchCase:
    case = (MatchCase.objects.select_for_update(of=("self",))
            .select_related("person_a", "person_b").filter(public_id=case_ref, **_scope()).first())
    if case is None or not _visible(case, organization_ref):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if _is_party(actor_public_id, case):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return case


def _survivor(case: MatchCase, wanted: str) -> tuple[Person, Person]:
    """(survivor, the one that ends). The reviewer may name which; otherwise the older record."""
    a, b = case.person_a, case.person_b
    if wanted:
        if wanted not in (a.public_id, b.public_id):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["primary"])
        return (a, b) if a.public_id == wanted else (b, a)
    older, newer = sorted((a, b), key=lambda p: (p.created_at, str(p.id)))
    return older, newer


def _summary(merge: PersonMerge) -> dict[str, Any]:
    return {
        "id": merge.public_id, "state": merge.state,
        "kept": merge.primary.public_id, "ended": merge.secondary.public_id,
        "login_moved": merge.login_actor_id is not None, "left_behind": merge.left_behind,
        "access_not_carried": merge.access_not_carried,
        "moved": merge.items.count() if merge.state == MergeState.MERGED else 0,
    }


def merge(
    *, case_ref: str, actor_public_id: str, organization_ref: str, primary_ref: str = "",
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Merge the two records of a confirmed link. Raises CONFLICT_VERSION when the case is not a
    confirmed link, or is merged already."""
    now = utc_now(clock)
    scope = _scope()
    with transaction.atomic():
        case = _lock_case(case_ref, organization_ref, actor_public_id)
        if case.state != MatchState.LINKED or PersonMerge.objects.filter(
                case=case, state=MergeState.MERGED, **scope).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        keep, end = _survivor(case, primary_ref)
        # Lock both records, in a fixed order, then look again.
        locked = {p.pk: p for p in Person.objects.select_for_update().filter(
            pk__in=[keep.pk, end.pk], **scope).order_by("id")}
        keep, end = locked[keep.pk], locked[end.pk]
        if any(p.status != Status.ACTIVE or p.merged_into_id for p in (keep, end)):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if keep.linked_actor_id and end.linked_actor_id:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["login"])

        login_actor = None
        if end.linked_actor_id and not keep.linked_actor_id:
            login_actor = end.linked_actor_id
            end.linked_actor = None
            end.save(update_fields=["linked_actor", "updated_at"])   # free the one-per-person slot
            keep.linked_actor_id = login_actor
            keep.save(update_fields=["linked_actor", "updated_at"])

        moved, left = person_refs.repoint(src=end.public_id, dst=keep.public_id, scope=scope)
        have = set(PersonSite.objects.filter(person=keep, **scope).values_list(
            "organization_ref", flat=True))
        for site in PersonSite.objects.filter(person=end, **scope):
            if site.organization_ref in have:
                left += 1
                continue
            site.person = keep
            site.save(update_fields=["person", "updated_at"])
            moved.append((SITE_KIND, str(site.pk)))
        not_carried = (
            CareRelationship.objects.filter(person=end, status=Status.ACTIVE, **scope).count()
            + ProxyGrant.objects.filter(person=end, **scope).exclude(
                status__in=(ProxyStatus.REJECTED, ProxyStatus.REVOKED, ProxyStatus.EXPIRED)
            ).count())

        end.status, end.merged_into = Status.ENDED, keep
        end.entity_version += 1
        end.save()
        done = PersonMerge.objects.create(
            case=case, primary=keep, secondary=end, merged_by_ref=actor_public_id, merged_at=now,
            login_actor_id=login_actor, left_behind=left, access_not_carried=not_carried, **scope)
        PersonMergeItem.objects.bulk_create(
            [PersonMergeItem(merge=done, kind=kind, row_id=row, **scope) for kind, row in moved])
        case.entity_version += 1
        case.save()
        _log(case, "merge", actor_public_id, "", now)
        _audit(actor_public_id, "match.merge", "match_case", case.public_id,
               moved=len(moved), left_behind=left, access_not_carried=not_carried)
        _announce(case, "merged")
        return {"case": case_dict(case), "merge": _summary(done)}


def unmerge(
    *, case_ref: str, actor_public_id: str, organization_ref: str, clock: Clock | None = None,
) -> dict[str, Any]:
    """Undo a merge: move back what it moved. The case goes back to review. Raises
    CONFLICT_VERSION when the case has no merge in force."""
    now = utc_now(clock)
    scope = _scope()
    with transaction.atomic():
        case = _lock_case(case_ref, organization_ref, actor_public_id)
        done = (PersonMerge.objects.select_for_update(of=("self",))
                .select_related("primary", "secondary")
                .filter(case=case, state=MergeState.MERGED, **scope).first())
        if done is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        keep = Person.objects.select_for_update().get(pk=done.primary_id, **scope)
        end = Person.objects.select_for_update().get(pk=done.secondary_id, **scope)
        items = list(PersonMergeItem.objects.filter(merge=done, **scope))

        sites = [i.row_id for i in items if i.kind == SITE_KIND]
        for site in PersonSite.objects.filter(pk__in=sites, person=keep, **scope):
            if PersonSite.objects.filter(
                    person=end, organization_ref=site.organization_ref, **scope).exists():
                continue
            site.person = end
            site.save(update_fields=["person", "updated_at"])
        person_refs.restore(
            items=[(i.kind, i.row_id) for i in items if i.kind != SITE_KIND],
            src=keep.public_id, dst=end.public_id, scope=scope)

        if done.login_actor_id and keep.linked_actor_id == done.login_actor_id:
            keep.linked_actor = None
            keep.save(update_fields=["linked_actor", "updated_at"])
            end.linked_actor_id = done.login_actor_id
        end.status, end.merged_into = Status.ACTIVE, None
        end.entity_version += 1
        end.save()

        done.state, done.unmerged_by_ref, done.unmerged_at = (
            MergeState.UNMERGED, actor_public_id, now)
        done.entity_version += 1
        done.save()
        case.state, case.first_reviewer_ref = MatchState.SPLIT_REVIEW, ""
        case.entity_version += 1
        case.save()
        _log(case, "unmerge", actor_public_id, "", now)
        _audit(actor_public_id, "match.unmerge", "match_case", case.public_id)
        _announce(case, "unmerged")
        return {"case": case_dict(case), "merge": _summary(done)}


def merge_of(case_refs: list[str]) -> dict[str, dict[str, Any]]:
    """For each of these cases, the merge in force (if any): what a reviewer sees on the board."""
    if not case_refs:
        return {}
    rows = PersonMerge.objects.select_related("case", "primary", "secondary").filter(
        case__public_id__in=case_refs, state=MergeState.MERGED, **_scope())
    return {m.case.public_id: _summary(m) for m in rows}


def canonical_person_ref(person_ref: str) -> str:
    """The record this patient lives under now: follows "merged into" to the survivor. A patient
    that was never merged (or does not exist) comes back unchanged."""
    ctx = require_context()
    current = person_ref
    for _ in range(MAX_CHAIN):
        row = Person.objects.filter(
            public_id=current, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
        ).select_related("merged_into").first()
        if row is None or row.merged_into is None:
            return current
        current = row.merged_into.public_id
    return current


__all__ = ["canonical_person_ref", "merge", "merge_of", "unmerge"]
