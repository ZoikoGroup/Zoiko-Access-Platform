"""Starting, reading, answering and closing message threads.

Access works as in the other modules: every write needs a policy RECEIPT (`write:comms`) for exactly
this actor, organization and patient, and reads are for callers that already passed `read:comms`.
On top of the receipt, WHICH SIDE a person is on comes from their role: patients and guardians
(R01, R02) write as the patient; clinic operations staff (R04, R13) write as the clinic and may
only touch threads of their own organization. Nobody else may write (doctors and Zoiko staff
are not part of this first version).

The subject and every body are encrypted. Events and audit entries carry ids only, never text.
A person is limited to 30 messages an hour, so a stuck script cannot flood a clinic.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction
from django.db.models import F

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.stats import summarize
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import Message, Side, Thread, ThreadStatus

WRITE_COMMS = "write:comms"
PATIENT_ROLES = frozenset({"R01", "R02"})
STAFF_ROLES = frozenset({"R04", "R13"})
MAX_SUBJECT = 120
MAX_BODY = 2000
PER_HOUR = 30


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _audit(by: str, action: str, ref: str) -> None:
    record(actor_ref=by, action=action, object_type="message_thread", object_ref=ref,
           outcome=AuditOutcome.ALLOWED)


def _own(thread_ref: str) -> Thread:
    found = Thread.objects.filter(public_id=thread_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _side(decision: policy.Decision) -> str:
    if decision.role_code in PATIENT_ROLES:
        return Side.PATIENT
    if decision.role_code in STAFF_ROLES:
        return Side.STAFF
    raise ZoikoError(ErrorCode.POLICY_DENIED)


def _text(value: str, field: str, high: int) -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= high or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return value


def _seal(value: str, kind: str, public_id: str, field: str) -> str:
    ctx = require_context()
    return encrypt(value, tenant_id=ctx.tenant_id, context=f"{kind}:{public_id}:{field}")


def _open(token: str, kind: str, public_id: str, field: str) -> str:
    ctx = require_context()
    try:
        return decrypt(token, tenant_id=ctx.tenant_id, context=f"{kind}:{public_id}:{field}")
    except CryptoError:
        return ""                                    # unreadable: show nothing, not junk


def _too_fast(author_ref: str, now: dt.datetime) -> bool:
    return Message.objects.filter(
        author_ref=author_ref, created_at__gte=now - dt.timedelta(hours=1), **_scope()
    ).count() >= PER_HOUR


def thread_dict(t: Thread) -> dict[str, Any]:
    return {
        "public_id": t.public_id, "person_ref": t.person_ref,
        "organization_ref": t.organization_ref, "status": t.status,
        "subject": _open(t.subject_enc, "thread", t.public_id, "subject"),
        "patient_unread": t.patient_unread, "staff_unread": t.staff_unread,
        "last_message_at": t.last_message_at.isoformat(), "created_at": t.created_at.isoformat(),
    }


def _message_dict(m: Message) -> dict[str, Any]:
    return {"public_id": m.public_id, "side": m.side, "author_ref": m.author_ref,
            "body": _open(m.body_enc, "message", m.public_id, "body"),
            "at": m.created_at.isoformat()}


def _event(thread: Thread, side: str, actor_ref: str = "") -> None:
    """A message was sent: which thread and which side. Clinic staff's login is added, a patient's
    never is (only the side says it was them)."""
    payload = {"thread_ref": thread.public_id, "side": side}
    if actor_ref and side != Side.PATIENT:
        payload["actor_ref"] = actor_ref
    emit(event_type="message.sent.v1", aggregate_type="thread", aggregate_ref=thread.public_id,
         payload=payload)


def start_thread(
    *, person_ref: str, organization_ref: str, subject: str, body: str, actor_public_id: str,
    decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """A patient (or guardian) writes to their clinic."""
    now = utc_now(clock)
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_COMMS,
        organization_public_id=organization_ref, subject_ref=person_ref)
    if _side(decision) != Side.PATIENT:
        raise ZoikoError(ErrorCode.POLICY_DENIED)         # the clinic answers; it does not start
    subject, body = _text(subject, "subject", MAX_SUBJECT), _text(body, "body", MAX_BODY)
    with transaction.atomic():
        if _too_fast(actor_public_id, now):
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        thread = Thread(
            person_ref=person_ref, organization_ref=organization_ref,
            started_by_ref=actor_public_id, subject_enc="", last_message_at=now,
            staff_unread=1, **_scope())
        thread.subject_enc = _seal(subject, "thread", thread.public_id, "subject")
        thread.save()
        _add(thread, actor_public_id, Side.PATIENT, body)
        _audit(actor_public_id, "message.start", thread.public_id)
        _event(thread, Side.PATIENT, actor_public_id)
        return thread_dict(thread)


def _add(thread: Thread, author_ref: str, side: str, body: str) -> Message:
    message = Message(thread=thread, author_ref=author_ref, side=side, body_enc="", **_scope())
    message.body_enc = _seal(body, "message", message.public_id, "body")
    message.save()
    return message


def reply(
    *, thread_ref: str, body: str, actor_public_id: str, decision: policy.Decision,
    organization_public_id: str | None = None, clock: Clock | None = None,
) -> dict[str, Any]:
    now = utc_now(clock)
    body = _text(body, "body", MAX_BODY)
    with transaction.atomic():
        thread = _own(thread_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_COMMS,
            organization_public_id=organization_public_id, subject_ref=thread.person_ref)
        side = _side(decision)
        if side == Side.STAFF and thread.organization_ref != organization_public_id:
            raise ZoikoError(ErrorCode.NOT_FOUND)         # another organization's thread
        if thread.status != ThreadStatus.OPEN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # closed: start a new one
        if _too_fast(actor_public_id, now):
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        _add(thread, actor_public_id, side, body)
        thread.last_message_at = now
        if side == Side.STAFF:
            thread.patient_unread = F("patient_unread") + 1
            thread.staff_unread = 0                       # the clinic has obviously seen it
            if thread.first_reply_at is None:
                thread.first_reply_at = now
        else:
            thread.staff_unread = F("staff_unread") + 1
        thread.entity_version += 1
        thread.save(update_fields=["last_message_at", "patient_unread", "staff_unread",
                                   "first_reply_at", "entity_version", "updated_at"])
        thread.refresh_from_db()
        _audit(actor_public_id, "message.reply", thread.public_id)
        _event(thread, side, actor_public_id)
        return thread_dict(thread)


def get_thread(thread_ref: str) -> dict[str, Any]:
    return thread_dict(_own(thread_ref))


def open_thread(thread_ref: str, *, side: str) -> dict[str, Any]:
    """The messages, oldest first. Opening a thread marks it read for the side that opened it.
    The caller has been allowed to read it (and is on the right side)."""
    with transaction.atomic():
        thread = _own(thread_ref)
        field = "patient_unread" if side == Side.PATIENT else "staff_unread"
        if getattr(thread, field):
            setattr(thread, field, 0)
            thread.save(update_fields=[field, "updated_at"])
        rows = Message.objects.filter(thread=thread, **_scope()).order_by("created_at")
        return {"thread": thread_dict(thread), "messages": [_message_dict(m) for m in rows]}


def read_messages(thread_ref: str) -> list[dict[str, Any]]:
    """The messages of a thread, oldest first, WITHOUT marking anything read. For a patient's
    data export; the caller has been allowed to read this thread."""
    thread = _own(thread_ref)
    rows = Message.objects.filter(thread=thread, **_scope()).order_by("created_at")
    return [_message_dict(m) for m in rows]


def close_thread(
    *, thread_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_public_id: str,
) -> dict[str, Any]:
    """The clinic closes a finished thread. Repeating it is harmless."""
    with transaction.atomic():
        thread = _own(thread_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_COMMS,
            organization_public_id=organization_public_id, subject_ref=thread.person_ref)
        if _side(decision) != Side.STAFF or thread.organization_ref != organization_public_id:
            raise ZoikoError(ErrorCode.NOT_FOUND if _side(decision) == Side.STAFF
                             else ErrorCode.POLICY_DENIED)
        if thread.status == ThreadStatus.OPEN:
            thread.status = ThreadStatus.CLOSED
            thread.entity_version += 1
            thread.save(update_fields=["status", "entity_version", "updated_at"])
            _audit(actor_public_id, "message.close", thread.public_id)
        return thread_dict(thread)


def list_threads(
    *, person_refs: list[str] | None = None, organization_ref: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    """Newest activity first. The caller has authorized each patient or organization it asks for."""
    rows = Thread.objects.filter(**_scope())
    if person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    return [thread_dict(t) for t in rows.order_by("-last_message_at")[: max(1, min(limit, 200))]]


def unread_threads_for_staff(organization_ref: str) -> int:
    """How many open threads of this organization have messages its staff have not opened."""
    return Thread.objects.filter(
        organization_ref=organization_ref, status=ThreadStatus.OPEN, staff_unread__gt=0,
        **_scope()).count()


def unanswered_threads(
    organization_ref: str, limit: int = 500
) -> list[tuple[str, dt.datetime]]:
    """(thread id, time of its newest message) of the open threads of this organization with
    messages its staff have not opened, oldest first. Reads no subject or message text."""
    rows = Thread.objects.filter(
        organization_ref=organization_ref, status=ThreadStatus.OPEN, staff_unread__gt=0,
        **_scope()).order_by("last_message_at").values_list("public_id", "last_message_at")
    return list(rows[: max(1, min(limit, 1000))])


def overdue_threads_for_staff(
    organization_ref: str, hours: int, clock: Clock | None = None
) -> int:
    """Open threads whose newest message the clinic has not opened for more than `hours` hours.
    Counted from the newest message, so a long-waiting thread is never over-reported."""
    cutoff = utc_now(clock) - dt.timedelta(hours=hours)
    return Thread.objects.filter(
        organization_ref=organization_ref, status=ThreadStatus.OPEN, staff_unread__gt=0,
        last_message_at__lt=cutoff, **_scope()).count()


def report(organization_ref: str, since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    """Counts of conversations with this organization: started in the period, how many are closed,
    how many wait for the clinic right now. Never any text."""
    rows = Thread.objects.filter(organization_ref=organization_ref, **_scope())
    started = rows.filter(created_at__gte=since, created_at__lt=until)
    waited = [
        (answered - created).total_seconds() / 3600
        for created, answered in started.filter(first_reply_at__isnull=False).values_list(
            "created_at", "first_reply_at")[:5000]
        if answered is not None
    ]
    return {
        "started": started.count(),
        "closed": started.filter(status=ThreadStatus.CLOSED).count(),
        "waiting_for_clinic": rows.filter(status=ThreadStatus.OPEN, staff_unread__gt=0).count(),
        "hours_to_first_reply": summarize(waited),
    }
