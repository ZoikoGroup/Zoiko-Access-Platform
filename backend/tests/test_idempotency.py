import datetime as dt
import json
import threading

import pytest
from django.db import connection

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.idempotency import request_digest, run
from foundation.models import IdempotencyKey
from foundation.payloads import PayloadError
from foundation.timeutil import UTC, FixedClock
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

KEY = "booking-key-0001-abcdef"
SCOPE = "actor:a1:POST /bookings"
DAY = dt.timedelta(days=1)


class Counter:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"booking_ref": "bk_1", "status": "requested"}


def call(ctx, key=KEY, payload=None, work=None, scope=SCOPE, **kwargs):
    with tenant_transaction(ctx):
        return run(
            scope=scope, key=key, payload=payload or {"service": "svc_1"},
            work=work or Counter(), retain_for=DAY, **kwargs,
        )


def test_the_first_request_runs_the_work_and_saves_the_answer(ctx_a):
    work = Counter()
    outcome = call(ctx_a, work=work)
    assert outcome.replayed is False
    assert outcome.result == {"booking_ref": "bk_1", "status": "requested"}
    assert work.calls == 1


def test_repeating_the_request_returns_the_saved_answer_without_redoing_the_work(ctx_a):
    work = Counter()
    first = call(ctx_a, work=work)
    second = call(ctx_a, work=work)
    assert second.replayed is True and second.result == first.result
    assert work.calls == 1


def test_the_same_key_with_a_different_request_is_refused(ctx_a):
    work = Counter()
    call(ctx_a, payload={"service": "svc_1"}, work=work)
    with pytest.raises(ZoikoError) as excinfo:
        call(ctx_a, payload={"service": "svc_2"}, work=work)
    assert excinfo.value.code is ErrorCode.DUPLICATE_COMMAND
    assert work.calls == 1


def test_the_order_of_fields_in_the_request_does_not_matter(ctx_a):
    work = Counter()
    call(ctx_a, payload={"a": 1, "b": 2}, work=work)
    assert call(ctx_a, payload={"b": 2, "a": 1}, work=work).replayed is True
    assert request_digest({"a": 1, "b": 2}) == request_digest({"b": 2, "a": 1})


def test_a_failed_request_is_not_remembered_so_a_retry_can_succeed(ctx_a):
    def broken():
        raise RuntimeError("downstream failed")

    with pytest.raises(RuntimeError):
        call(ctx_a, work=broken)
    assert IdempotencyKey.objects.count() == 0
    work = Counter()
    assert call(ctx_a, work=work).replayed is False and work.calls == 1


@pytest.mark.parametrize(
    "bad", ["", "short", "x" * 129, "has space in the key!!", "ключ-ключ-ключ-ключ"]
)
def test_malformed_keys_are_refused(ctx_a, bad):
    with pytest.raises(ZoikoError) as excinfo:
        call(ctx_a, key=bad)
    assert excinfo.value.code is ErrorCode.VALIDATION_FAILED
    assert excinfo.value.fields == ("idempotency_key",)


def test_neither_the_key_nor_the_request_is_stored(ctx_a):
    call(ctx_a, payload={"patient": "John Smith"})
    row = IdempotencyKey.objects.get()
    everything = " ".join([row.scope, row.key_hash, row.request_digest, json.dumps(row.result)])
    assert KEY not in everything and "John Smith" not in everything
    assert len(row.key_hash) == len(row.request_digest) == 64


def test_the_answer_expires_when_the_caller_said(ctx_a):
    clock = FixedClock(dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    call(ctx_a, clock=clock)
    assert IdempotencyKey.objects.get().expires_at == dt.datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_different_scopes_and_different_tenants_do_not_interfere(ctx_a, ctx_b):
    work = Counter()
    call(ctx_a, work=work)
    call(ctx_a, work=work, scope="actor:a2:POST /bookings")   # other actor, same key
    call(ctx_b, work=work)                                     # other tenant, same key
    assert work.calls == 3


def test_a_saved_answer_cannot_hold_free_text(ctx_a):
    with pytest.raises(PayloadError):
        call(ctx_a, work=lambda: {"note": {"nested": "no"}})
    assert IdempotencyKey.objects.count() == 0


def test_it_works_as_the_restricted_app_role_and_stays_private_to_the_tenant(ctx_a, ctx_b):
    with tenant_transaction(ctx_a), as_app_role():
        outcome = run(scope=SCOPE, key=KEY, payload={"s": 1}, work=Counter(), retain_for=DAY)
    assert outcome.replayed is False
    with tenant_transaction(ctx_b), as_app_role():
        assert IdempotencyKey.objects.count() == 0
    with tenant_transaction(ctx_a), as_app_role():
        assert IdempotencyKey.objects.count() == 1


def test_two_identical_requests_at_the_same_time_do_the_work_once(ctx_a):
    work_started, release = threading.Event(), threading.Event()
    executions, outcomes, errors = [], [], []

    def slow_work():
        executions.append(1)
        work_started.set()
        release.wait(10)
        return {"booking_ref": "bk_1"}

    def request(work):
        try:
            outcomes.append(call(ctx_a, work=work))
        except Exception as exc:  # noqa: BLE001 - surface any thread failure in the assertion
            errors.append(exc)
        finally:
            connection.close()

    def never_runs():
        executions.append("second ran the work")
        return {"booking_ref": "bk_other"}

    first = threading.Thread(target=request, args=(slow_work,))
    first.start()
    assert work_started.wait(10)
    second = threading.Thread(target=request, args=(never_runs,))
    second.start()
    second.join(0.5)
    assert second.is_alive()          # the second request is WAITING on the first, not racing it
    release.set()
    first.join(10)
    second.join(10)

    assert errors == []
    assert executions == [1]
    assert sorted(o.replayed for o in outcomes) == [False, True]
    assert all(o.result == {"booking_ref": "bk_1"} for o in outcomes)
    assert IdempotencyKey.objects.count() == 1
