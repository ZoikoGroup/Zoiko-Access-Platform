"""When a child comes of age, a parent's or guardian's access stops until the child decides.

A parent or guardian acts for a child because the child cannot act for themselves. The day the
child reaches the age of majority that reason ends, and the specification is clear that nothing
inherits across it (GHE-06: "age transition" is a trigger that recalculates proxy access). So:

  * a daily job suspends every ACTIVE parent or guardian grant whose person has reached the age of
    majority, marks it "waiting for the person's decision" (`review_reason`), and announces it;
  * the person (now an adult, with their own login) then either KEEPS the guardian's access, which
    records that they confirmed it so it is never suspended for age again, or ENDS it;
  * until they decide the guardian sees nothing: a suspended grant authorizes nothing.

Only parent and guardian grants are age-bound. A caregiver or legal agent for an adult is not.

The age of majority differs by country, and which age applies is a legal fact for the deployment to
state, not for us to guess: `ZOIKO_AGE_OF_MAJORITY` lists countries ("GB=18,SG=21") and
`ZOIKO_AGE_OF_MAJORITY_DEFAULT` (18) applies to any country not listed or not recorded.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now

from .models import Actor, Person, ProxyGrant, ProxyStatus
from .people import resume_proxy_grant, revoke_proxy_grant, suspend_proxy_grant
from .services import _audit, tenant_country

AGE_BOUND = ("parent", "guardian")
REASON = "age_of_majority"
SYSTEM = "system"


def majority_table() -> dict[str, int]:
    """Country code -> age of majority, from `ZOIKO_AGE_OF_MAJORITY` ("GB=18,SG=21")."""
    table: dict[str, int] = {}
    for part in str(getattr(settings, "AGE_OF_MAJORITY", "")).split(","):
        code, _, age = part.strip().partition("=")
        if code.strip() and age.strip().isdigit() and 10 <= int(age) <= 30:
            table[code.strip().upper()] = int(age)
    return table


def majority_age(country: str = "") -> int:
    default = int(getattr(settings, "AGE_OF_MAJORITY_DEFAULT", 18))
    return majority_table().get((country or "").strip().upper(), default)


def age_on(birth: dt.date, day: dt.date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _birth(profile: dict[str, str]) -> dt.date | None:
    try:
        return dt.date.fromisoformat(profile.get("birth_date", ""))
    except ValueError:
        return None


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def review_due(*, clock: Clock | None = None) -> int:
    """Suspend every active parent or guardian grant whose person has come of age and has not yet
    confirmed it. Runs in the worker, for the bound tenant. Returns how many were suspended."""
    now = utc_now(clock)
    today = now.date()
    ctx = require_context()
    limit = majority_age(tenant_country(ctx.tenant_id))
    grants = list(
        ProxyGrant.objects.select_related("person", "delegate").filter(
            status=ProxyStatus.ACTIVE, relationship_type__in=AGE_BOUND,
            age_reviewed_at__isnull=True, **_scope()))
    if not grants:
        return 0
    from .profile import get_person_profiles  # imported here: profile pulls in the policy engine

    profiles = get_person_profiles(sorted({g.person.public_id for g in grants}))
    done = 0
    for grant in grants:
        birth = _birth(profiles.get(grant.person.public_id, {}))
        if birth is None or age_on(birth, today) < limit:
            continue
        with transaction.atomic():
            suspend_proxy_grant(grant.public_id, by=SYSTEM, reason=REASON)
            ProxyGrant.objects.filter(pk=grant.pk).update(review_reason=REASON)
            emit(event_type="delegation.age_review.v1", aggregate_type="delegation",
                 aggregate_ref=grant.public_id,
                 payload={"grant_ref": grant.public_id, "person_ref": grant.person.public_id})
            _audit(SYSTEM, "proxy.age_review", "proxy_grant", grant.public_id, reason=REASON)
        done += 1
    return done


def waiting_for_decision(person_ref: str) -> list[ProxyGrant]:
    """The person's grants that are waiting for them to decide after coming of age."""
    return list(ProxyGrant.objects.select_related("delegate", "person").filter(
        person__public_id=person_ref, status=ProxyStatus.SUSPENDED, review_reason=REASON,
        **_scope()))


def _own_grant(grant_ref: str) -> ProxyGrant:
    found = (ProxyGrant.objects.select_for_update(of=("self",)).select_related("person", "delegate")
             .filter(public_id=grant_ref, **_scope()).first())
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _require_self(grant: ProxyGrant, actor_public_id: str) -> Actor:
    """Only the person themselves, through their own login, may decide about their own record."""
    person: Person = grant.person
    actor = Actor.objects.filter(public_id=actor_public_id, **_scope()).first()
    if actor is None or person.linked_actor_id != actor.pk:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return actor


def keep_access(*, grant_ref: str, actor_public_id: str, clock: Clock | None = None
                ) -> ProxyGrant:
    """The person, now an adult, confirms the guardian keeps the access they had. Recorded, so the
    daily job never suspends this grant for age again."""
    now = utc_now(clock)
    with transaction.atomic():
        grant = _own_grant(grant_ref)
        _require_self(grant, actor_public_id)
        if grant.status != ProxyStatus.SUSPENDED or grant.review_reason != REASON:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        resume_proxy_grant(grant.public_id, by=actor_public_id, clock=clock)
        ProxyGrant.objects.filter(pk=grant.pk).update(review_reason="", age_reviewed_at=now)
        _audit(actor_public_id, "proxy.age_review_kept", "proxy_grant", grant.public_id)
        grant.refresh_from_db()
        return grant


def end_access(*, grant_ref: str, actor_public_id: str, clock: Clock | None = None) -> ProxyGrant:
    """The person ends a guardian's access for good. Allowed at any time for their own record, not
    only after an age review."""
    with transaction.atomic():
        grant = _own_grant(grant_ref)
        _require_self(grant, actor_public_id)
        ended = revoke_proxy_grant(grant.public_id, by=actor_public_id, clock=clock)
        ProxyGrant.objects.filter(pk=grant.pk).update(review_reason="")
        ended.refresh_from_db()
        return ended
