"""Issuing a referral and deciding on it.

Access works as in scheduling: both writes need a policy RECEIPT for exactly this actor,
operation, organization and patient. On top of the receipt, ONLY a doctor (R03) may issue a
referral. Receiving staff (R04, R13) may mark it received; only the named owner of the receiving
inbox, or the fallback, who must be a doctor (R03), may accept, ask for more or decline (DEV-109).
Every change is audited and emits an outbox event in the same transaction.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Q

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.catalog import api as catalog
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import OPEN_STATES, Referral, ReferralInbox, ReferralState, Urgency

WRITE_REFERRAL = "write:referral"
ISSUING_ROLES = frozenset({"R03"})
DECIDING_ROLES = frozenset({"R03"})                 # acceptance is clinical (GHE-14 T-39)
ACKNOWLEDGING_ROLES = frozenset({"R04", "R13"})     # receipt only: it does not accept care
INBOX_ADMIN_ROLES = frozenset({"R13"})
# With no answer within the receiving clinic's response target (DEV-120) a referral is flagged and
# goes to the fallback; it is never closed on its own (T-41).
DECISIONS = {"accepted": ReferralState.ACCEPTED, "needs_info": ReferralState.NEEDS_INFO,
             "declined": ReferralState.DECLINED}
REASON_REQUIRED = frozenset({"needs_info", "declined"})
MAX_TEXT = 500


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _own(referral_ref: str) -> Referral:
    found = Referral.objects.filter(public_id=referral_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def referral_dict(r: Referral) -> dict[str, Any]:
    return {
        "public_id": r.public_id, "person_ref": r.person_ref, "referrer_ref": r.referrer_ref,
        "from_organization_ref": r.from_organization_ref, "service_ref": r.service_ref,
        "destination_organization_ref": r.destination_organization_ref, "reason": r.reason,
        "urgency": r.urgency, "state": r.state, "decided_by_ref": r.decided_by_ref,
        "decision_reason": r.decision_reason,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat(),
        "owner_ref": r.owner_ref,
        "acknowledged_by_ref": r.acknowledged_by_ref,
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        "responsible_ref": r.responsible_ref or r.referrer_ref,
        "escalated": r.state in OPEN_STATES and utc_now() - r.created_at > dt.timedelta(
            hours=trust.response_target_hours(r.destination_organization_ref)),
    }


def _event(event_type: str, r: Referral, *, actor_ref: str = "") -> None:
    # Which referral changed (and, for a decision, which clinic login decided it): nothing about the
    # patient or the reason.
    payload = {"referral_ref": r.public_id, "state": r.state}
    if actor_ref:
        payload["actor_ref"] = actor_ref
    emit(event_type=event_type, aggregate_type="referral", aggregate_ref=r.public_id,
         payload=payload)


def _clean(value: str, field: str) -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= MAX_TEXT:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return value


def _inbox(organization_ref: str) -> ReferralInbox | None:
    return ReferralInbox.objects.filter(organization_ref=organization_ref, **_scope()).first()


def inbox_dict(organization_ref: str) -> dict[str, str] | None:
    """Who answers referrals at this site, or None when nobody is named yet."""
    box = _inbox(organization_ref)
    if box is None:
        return None
    return {"organization_ref": organization_ref, "owner_ref": box.owner_ref,
            "fallback_ref": box.fallback_ref}


def _answerers(inbox: ReferralInbox) -> list[str]:
    return [ref for ref in (inbox.owner_ref, inbox.fallback_ref) if ref]


def _give_links(referral: Referral, inbox: ReferralInbox) -> None:
    """The owner and the fallback may read this patient while the referral is open: a care link
    of the 'referral' kind, tied to the receiving site. It ends when the referral is decided."""
    for doctor in _answerers(inbox):
        try:
            with transaction.atomic():
                trust.ensure_care_relationship(
                    actor_public_id=doctor, person_public_id=referral.person_ref,
                    basis=trust.CareBasis.REFERRAL,
                    organization_ref=referral.destination_organization_ref, by="system")
        except ZoikoError:
            continue                  # a doctor who can no longer hold a link simply gets none


def set_inbox(
    *, organization_ref: str, owner_ref: str, fallback_ref: str, actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, str]:
    """Name who answers referrals sent to this site. Only the clinic's administrator, and both
    people must be doctors there. Referrals already waiting move to the new owner."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_REFERRAL,
        organization_public_id=organization_ref)
    if decision.role_code not in INBOX_ADMIN_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    doctors = set(trust.clinicians_at(organization_ref))
    if owner_ref not in doctors:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["owner"])
    if fallback_ref and (fallback_ref not in doctors or fallback_ref == owner_ref):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["fallback"])
    with transaction.atomic():
        box = _inbox(organization_ref)
        if box is None:
            box = ReferralInbox(organization_ref=organization_ref, set_by_ref=actor_public_id,
                                owner_ref=owner_ref, fallback_ref=fallback_ref, **_scope())
        else:
            box.owner_ref, box.fallback_ref = owner_ref, fallback_ref
            box.set_by_ref = actor_public_id
            box.entity_version += 1
        box.save()
        waiting = Referral.objects.filter(
            destination_organization_ref=organization_ref, state__in=OPEN_STATES, **_scope())
        for r in waiting:
            r.owner_ref = owner_ref
            r.save(update_fields=["owner_ref", "updated_at"])
            _give_links(r, box)
        record(actor_ref=actor_public_id, action="referral.inbox_set", object_type="referral_inbox",
               object_ref=box.public_id, outcome=AuditOutcome.ALLOWED)
        return {"organization_ref": organization_ref, "owner_ref": owner_ref,
                "fallback_ref": fallback_ref}


def inbox_member_left(organization_ref: str, actor_ref: str) -> bool:
    """A doctor lost the doctor role at this site (professional.privilege.revoked.v1). If they were
    the named owner or the fallback of its referral inbox, take them out: the fallback becomes the
    owner, or nobody is named (the staff page then says so and no referral can be accepted until
    someone is). Waiting referrals move with it, and the access the referrals gave the leaver ends.
    Returns True if the inbox changed. A doctor who has the role again is left alone."""
    box = _inbox(organization_ref)
    if box is None or actor_ref not in (box.owner_ref, box.fallback_ref):
        return False
    if actor_ref in trust.clinicians_at(organization_ref):
        return False                       # they hold the role again: the event is out of date
    with transaction.atomic():
        if actor_ref == box.owner_ref:
            box.owner_ref, box.fallback_ref = box.fallback_ref, ""      # the fallback steps up
        else:
            box.fallback_ref = ""
        box.entity_version += 1
        box.save()
        waiting = list(Referral.objects.filter(
            destination_organization_ref=organization_ref, state__in=OPEN_STATES, **_scope()))
        for r in waiting:
            if r.owner_ref == actor_ref:
                r.owner_ref = box.owner_ref
                r.save(update_fields=["owner_ref", "updated_at"])
            if box.owner_ref:
                _give_links(r, box)
            trust.end_care_links(
                actor_public_id=actor_ref, person_public_id=r.person_ref,
                basis=trust.CareBasis.REFERRAL, organization_ref=organization_ref, by="system")
        record(actor_ref="system", action="referral.inbox_changed", object_type="referral_inbox",
               object_ref=box.public_id, outcome=AuditOutcome.ALLOWED, reason_code="role_ended")
        return True


def acknowledge(
    *, referral_ref: str, actor_public_id: str, decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Receiving staff mark a referral as received. This is NOT acceptance of care (T-38): the
    state stays 'sent' and only the named doctor can accept."""
    with transaction.atomic():
        referral = _own(referral_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_REFERRAL,
            organization_public_id=referral.destination_organization_ref,
            subject_ref=referral.person_ref)
        if decision.role_code not in ACKNOWLEDGING_ROLES:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if referral.state not in OPEN_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if referral.acknowledged_at is not None:
            return referral_dict(referral)                 # repeating is harmless
        referral.acknowledged_by_ref = actor_public_id
        referral.acknowledged_at = utc_now(clock)
        referral.entity_version += 1
        referral.save(update_fields=["acknowledged_by_ref", "acknowledged_at",
                                     "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="referral.acknowledge", object_type="referral",
               object_ref=referral.public_id, outcome=AuditOutcome.ALLOWED)
        return referral_dict(referral)


def issue(
    *, person_ref: str, service_ref: str, reason: str, urgency: str, actor_public_id: str,
    from_organization_ref: str, decision: policy.Decision,
) -> dict[str, Any]:
    """A doctor sends a patient to a service (of any organization of this customer)."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_REFERRAL,
        organization_public_id=from_organization_ref, subject_ref=person_ref)
    if decision.role_code not in ISSUING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    reason = _clean(reason, "reason")
    if urgency not in Urgency.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["urgency"])
    service = catalog.get_service(service_ref)
    if not catalog.is_offered(service):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["service"])
    try:
        with transaction.atomic():
            referral = Referral.objects.create(
                person_ref=person_ref, referrer_ref=actor_public_id,
                from_organization_ref=from_organization_ref, service_ref=service_ref,
                destination_organization_ref=service["organization_ref"], reason=reason,
                urgency=urgency, responsible_ref=actor_public_id, **_scope())
            inbox = _inbox(service["organization_ref"])
            if inbox is not None:
                referral.owner_ref = inbox.owner_ref
                referral.save(update_fields=["owner_ref", "updated_at"])
                _give_links(referral, inbox)
            trust.link_person_to_site(
                person_ref, service["organization_ref"], "referral")
            record(actor_ref=actor_public_id, action="referral.issue", object_type="referral",
                   object_ref=referral.public_id, outcome=AuditOutcome.ALLOWED)
            _event("referral.issued.v1", referral)
            return referral_dict(referral)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None   # already referred there


def decide(
    *, referral_ref: str, outcome: str, reason: str, actor_public_id: str,
    decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """The receiving organization accepts, asks for more information, or declines."""
    with transaction.atomic():
        referral = _own(referral_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_REFERRAL,
            organization_public_id=referral.destination_organization_ref,
            subject_ref=referral.person_ref)
        if decision.role_code not in DECIDING_ROLES:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        inbox = _inbox(referral.destination_organization_ref)
        named = {referral.owner_ref} | (set(_answerers(inbox)) if inbox else set())
        if actor_public_id not in named - {""}:
            raise ZoikoError(ErrorCode.POLICY_DENIED)     # only the named owner or the fallback
        if outcome not in DECISIONS:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["decision"])
        reason = reason.strip() if outcome not in REASON_REQUIRED else _clean(reason, "reason")
        if len(reason) > MAX_TEXT:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
        if referral.state not in OPEN_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)         # already accepted or declined
        referral.state = DECISIONS[outcome]
        if outcome == "accepted":
            referral.responsible_ref = actor_public_id    # the receiving doctor takes it on
        elif outcome == "declined":
            referral.responsible_ref = referral.referrer_ref   # it stays with the sender (T-40)
        referral.decided_by_ref = actor_public_id
        referral.decision_reason = reason
        referral.decided_at = utc_now(clock)
        referral.entity_version += 1
        referral.save(update_fields=["state", "decided_by_ref", "decision_reason", "decided_at",
                                     "responsible_ref", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action=f"referral.{outcome}", object_type="referral",
               object_ref=referral.public_id, outcome=AuditOutcome.ALLOWED)
        if outcome in ("accepted", "declined") and inbox is not None:
            # The access the referral gave ends, except the accepting doctor's own.
            for doctor in _answerers(inbox):
                if outcome == "declined" or doctor != actor_public_id:
                    trust.end_care_links(
                        actor_public_id=doctor, person_public_id=referral.person_ref,
                        basis=trust.CareBasis.REFERRAL,
                        organization_ref=referral.destination_organization_ref, by=actor_public_id)
        _event("referral.decided.v1", referral, actor_ref=actor_public_id)
        return referral_dict(referral)


def get_referral(referral_ref: str) -> dict[str, Any]:
    return referral_dict(_own(referral_ref))


def list_referrals(
    *, referrer_ref: str = "", person_refs: list[str] | None = None,
    destination_organization_ref: str = "", limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest first. Exactly the filters given are applied; the caller decides which are allowed."""
    rows = Referral.objects.filter(**_scope())
    if referrer_ref:
        rows = rows.filter(referrer_ref=referrer_ref)
    if person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    if destination_organization_ref:
        rows = rows.filter(destination_organization_ref=destination_organization_ref)
    return [referral_dict(r) for r in rows.order_by("-created_at")[:limit]]


def list_for_answerer(actor_ref: str, limit: int = 200) -> list[dict[str, Any]]:
    """Referrals this doctor is named to answer: as owner of the referral, or as the fallback of
    the site it was sent to. Newest first."""
    fallback_sites = list(ReferralInbox.objects.filter(
        fallback_ref=actor_ref, **_scope()).values_list("organization_ref", flat=True))
    rows = Referral.objects.filter(**_scope()).filter(
        Q(owner_ref=actor_ref) | Q(destination_organization_ref__in=fallback_sites))
    return [referral_dict(r) for r in rows.order_by("-created_at")[:limit]]


def referral_counts() -> dict[str, int]:
    rows = Referral.objects.filter(**_scope())
    return {"total": rows.count(), "open": rows.filter(state__in=OPEN_STATES).count()}


def report(organization_ref: str, since: dt.datetime, until: dt.datetime) -> dict[str, int]:
    """Counts of referrals SENT TO this organization in the period, by where they stand now."""
    rows = Referral.objects.filter(
        destination_organization_ref=organization_ref, created_at__gte=since, created_at__lt=until,
        **_scope())
    counts = {r["state"]: r["n"] for r in rows.values("state").annotate(n=Count("id"))}
    return {"received": sum(counts.values()), **{
        k: counts.get(k, 0) for k in ("issued", "accepted", "needs_info", "declined")}}
