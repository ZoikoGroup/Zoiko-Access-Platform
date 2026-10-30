import uuid
from datetime import timedelta

import pytest

from foundation.cell_context import MissingContextError, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from tests.probe.models import Probe

pytestmark = pytest.mark.django_db(transaction=True)


def test_new_rows_get_ids_and_are_stamped_from_the_context_not_from_the_caller(ctx_a):
    with tenant_transaction(ctx_a):
        row = Probe.objects.create(label="x")
    assert row.cell_id == ctx_a.cell_id
    assert row.tenant_id == ctx_a.tenant_id
    assert row.id.version == 7
    assert len(row.public_id) == 22
    assert row.entity_version == 1
    assert row.created_at.utcoffset() == timedelta(0)  # stored as UTC


def test_creating_a_row_with_no_context_fails_closed():
    with pytest.raises(MissingContextError):
        Probe.objects.create(label="orphan")


def test_cannot_create_a_row_for_a_different_tenant(ctx_a, ctx_b):
    with pytest.raises(ZoikoError) as excinfo, tenant_transaction(ctx_a):
        Probe.objects.create(label="x", tenant_id=ctx_b.tenant_id)
    assert excinfo.value.code is ErrorCode.POLICY_DENIED


def test_cannot_create_a_row_for_a_different_cell(ctx_a):
    with pytest.raises(ZoikoError), tenant_transaction(ctx_a):
        Probe.objects.create(label="x", cell_id="OTHER-01")


def test_two_rows_never_share_ids(ctx_a):
    with tenant_transaction(ctx_a):
        rows = [Probe.objects.create(label=str(i)) for i in range(50)]
    assert len({r.id for r in rows}) == 50
    assert len({r.public_id for r in rows}) == 50
    assert uuid.UUID(int=rows[0].id.int).version == 7
