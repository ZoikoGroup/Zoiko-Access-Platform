import uuid

import pytest
from django.db import transaction

from foundation.cell_context import MissingContextError, tenant_transaction
from foundation.inbox import process_once
from foundation.models import InboxMessage
from tests.conftest import as_app_role
from tests.probe.models import Probe

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def test_a_duplicate_delivery_runs_the_handler_only_once(ctx_a):
    event_id, calls = uuid.uuid4(), []
    with tenant_transaction(ctx_a):
        assert process_once("booking-projector", event_id, lambda: calls.append(1)) is True
    with tenant_transaction(ctx_a):
        assert process_once("booking-projector", event_id, lambda: calls.append(1)) is False
    assert calls == [1]


def test_each_consumer_handles_the_same_event_once_independently(ctx_a):
    event_id, calls = uuid.uuid4(), []
    with tenant_transaction(ctx_a):
        process_once("consumer-a", event_id, lambda: calls.append("a"))
        process_once("consumer-b", event_id, lambda: calls.append("b"))
        process_once("consumer-a", event_id, lambda: calls.append("a again"))
    assert calls == ["a", "b"]


def test_a_failing_handler_leaves_no_trace_so_the_event_can_be_retried(ctx_a):
    event_id = uuid.uuid4()

    def broken():
        Probe.objects.create(label="half-done")
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError), tenant_transaction(ctx_a):
        process_once("projector", event_id, broken)
    assert InboxMessage.objects.count() == 0       # not recorded as handled
    assert Probe.objects.count() == 0              # the handler's own change rolled back too

    with tenant_transaction(ctx_a):
        assert process_once("projector", event_id, lambda: None) is True


def test_the_handlers_change_and_the_record_commit_together(ctx_a):
    event_id = uuid.uuid4()
    with tenant_transaction(ctx_a):
        process_once("projector", event_id, lambda: Probe.objects.create(label="done"))
    assert Probe.objects.count() == 1 and InboxMessage.objects.count() == 1


def test_without_a_bound_tenant_it_fails_closed():
    with pytest.raises(MissingContextError), transaction.atomic():
        process_once("projector", uuid.uuid4(), lambda: None)


def test_one_tenant_cannot_see_anothers_inbox_records(ctx_a, ctx_b):
    with tenant_transaction(ctx_a):
        process_once("projector", uuid.uuid4(), lambda: None)
    with tenant_transaction(ctx_b), as_app_role():
        assert InboxMessage.objects.count() == 0
    with tenant_transaction(ctx_a), as_app_role():
        assert InboxMessage.objects.count() == 1
