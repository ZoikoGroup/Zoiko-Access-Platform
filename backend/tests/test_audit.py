import datetime as dt
import threading

import pytest
from django.db import DatabaseError, connection, transaction

from foundation.cell_context import tenant_transaction
from foundation.payloads import PayloadError
from foundation.timeutil import UTC, FixedClock
from modules.evidence.api import AuditOutcome, record, verify_chain
from modules.evidence.audit import AuditError, compute_hash
from modules.evidence.models import GENESIS_HASH, AuditEvent
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def log(ctx, n=1, **kwargs):
    args = {
        "actor_ref": "act_1", "action": "booking.confirm", "object_type": "booking",
        "object_ref": "bk_1", "outcome": AuditOutcome.ALLOWED, **kwargs,
    }
    events = []
    with tenant_transaction(ctx):
        for _ in range(n):
            events.append(record(**args))
    return events


def tamper(sql, params=()):
    """Edit the table the way an attacker with full database access would."""
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE evidence_auditevent DISABLE TRIGGER USER")
        cursor.execute(sql, params)
        cursor.execute("ALTER TABLE evidence_auditevent ENABLE TRIGGER USER")


def check(ctx):
    with tenant_transaction(ctx):
        return verify_chain()


def test_events_form_a_numbered_chain_linked_by_hashes(ctx_a):
    first, second, third = log(ctx_a, 3)
    assert (first.chain_seq, second.chain_seq, third.chain_seq) == (1, 2, 3)
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.event_hash and third.prev_hash == second.event_hash
    assert len({first.event_hash, second.event_hash, third.event_hash}) == 3
    report = check(ctx_a)
    assert report.ok and report.checked == 3


def test_event_is_stamped_from_the_context_and_request(ctx_a):
    event = log(ctx_a, details={"booking_ref": "bk_1"}, reason_code="policy_ok")[0]
    assert (event.cell_id, event.tenant_id) == (ctx_a.cell_id, ctx_a.tenant_id)
    assert event.request_id == "-" and event.details == {"booking_ref": "bk_1"}


def test_each_tenant_has_its_own_chain(ctx_a, ctx_b):
    log(ctx_a, 2)
    first_b = log(ctx_b)[0]
    assert first_b.chain_seq == 1 and first_b.prev_hash == GENESIS_HASH
    assert check(ctx_a).checked == 2 and check(ctx_b).checked == 1


def test_changing_a_row_is_detected(ctx_a):
    log(ctx_a, 3)
    tamper("UPDATE evidence_auditevent SET outcome = 'denied' WHERE chain_seq = 2")
    report = check(ctx_a)
    assert (report.ok, report.first_bad_seq, report.problem) == (
        False, 2, "row content was changed")


def test_rewriting_a_row_and_its_hash_still_breaks_the_next_link(ctx_a):
    log(ctx_a, 3)
    with tenant_transaction(ctx_a):
        forged = AuditEvent.objects.get(chain_seq=2)
        forged.outcome = AuditOutcome.DENIED
        new_hash = compute_hash(forged)
    tamper("UPDATE evidence_auditevent SET outcome = 'denied', event_hash = %s "
           "WHERE chain_seq = 2", [new_hash])
    report = check(ctx_a)
    assert (report.first_bad_seq, report.problem) == (3, "link to the previous row broken")


def test_deleting_a_row_is_detected(ctx_a):
    log(ctx_a, 3)
    tamper("DELETE FROM evidence_auditevent WHERE chain_seq = 2")
    report = check(ctx_a)
    assert (report.ok, report.first_bad_seq, report.problem) == (False, 2, "a row is missing")


def test_the_database_refuses_update_and_delete_even_for_the_owner(ctx_a):
    log(ctx_a)
    for sql in ("UPDATE evidence_auditevent SET action = 'x'", "DELETE FROM evidence_auditevent"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql)
    assert check(ctx_a).ok


def test_the_app_role_cannot_change_or_remove_rows(ctx_a):
    log(ctx_a)
    for sql in ("UPDATE evidence_auditevent SET action = 'x'", "DELETE FROM evidence_auditevent"):
        with (
            pytest.raises(DatabaseError),
            tenant_transaction(ctx_a),
            as_app_role(),
            connection.cursor() as cursor,
        ):
            cursor.execute(sql)


def test_orm_update_and_delete_are_refused(ctx_a):
    event = log(ctx_a)[0]
    with pytest.raises(PermissionError):
        event.save()
    with pytest.raises(PermissionError):
        event.delete()


def test_the_app_role_can_write_and_read_only_its_own_tenant(ctx_a, ctx_b):
    with tenant_transaction(ctx_a), as_app_role():
        record(actor_ref="act_1", action="booking.confirm", object_type="booking",
               object_ref="bk_1", outcome=AuditOutcome.ALLOWED)
    with tenant_transaction(ctx_b), as_app_role():
        assert AuditEvent.objects.count() == 0
    with tenant_transaction(ctx_a), as_app_role():
        assert AuditEvent.objects.count() == 1


def test_a_rolled_back_action_leaves_no_audit_row(ctx_a):
    with pytest.raises(RuntimeError), tenant_transaction(ctx_a):
        record(actor_ref="act_1", action="booking.confirm", object_type="booking",
               object_ref="bk_1", outcome=AuditOutcome.ALLOWED)
        raise RuntimeError("business change failed")
    assert AuditEvent.objects.count() == 0


def test_time_comes_from_the_clock_in_utc(ctx_a):
    when = dt.datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    event = log(ctx_a, clock=FixedClock(when))[0]
    assert AuditEvent.objects.get(pk=event.pk).occurred_at == when


def test_record_needs_a_transaction_and_clean_inputs(ctx_a):
    with pytest.raises(AuditError):
        record(actor_ref="a", action="x.y", object_type="t", object_ref="r",
               outcome=AuditOutcome.ALLOWED)
    for bad in ({"action": "Booking Confirm"}, {"actor_ref": "John Smith"},
                {"object_ref": ""}, {"reason_code": "has space"}):
        with pytest.raises(AuditError):
            log(ctx_a, **bad)
    with pytest.raises(PayloadError):
        log(ctx_a, details={"note": "x" * 500})
    assert AuditEvent.objects.count() == 0


def test_two_writers_at_once_never_share_a_position(ctx_a):
    errors = []

    def writer():
        try:
            log(ctx_a, 5)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    report = check(ctx_a)
    assert report.ok and report.checked == 20


def test_a_second_writer_waits_for_the_first_to_commit(ctx_a):
    holding, release, finished = threading.Event(), threading.Event(), []

    def first():
        try:
            with tenant_transaction(ctx_a):
                record(actor_ref="act_1", action="booking.confirm", object_type="booking",
                       object_ref="bk_1", outcome=AuditOutcome.ALLOWED)
                holding.set()
                release.wait(10)          # keep the transaction open, uncommitted
        finally:
            connection.close()

    def second():
        try:
            finished.append(log(ctx_a, actor_ref="act_2")[0].chain_seq)
        finally:
            connection.close()

    t1 = threading.Thread(target=first)
    t1.start()
    assert holding.wait(10)
    t2 = threading.Thread(target=second)
    t2.start()
    t2.join(1.5)
    assert t2.is_alive() and finished == []     # blocked: it must not take position 1 as well
    release.set()
    t1.join(10)
    t2.join(10)
    assert finished == [2]
    report = check(ctx_a)
    assert report.ok and report.checked == 2
