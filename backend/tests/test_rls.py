"""Proof that tenant isolation works against a real PostgreSQL, as the restricted app role."""
import uuid
from datetime import datetime, timezone

import pytest
from django.db import DatabaseError, connection, transaction

from foundation.cell_context import CellContext, get_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from tests.conftest import as_app_role
from tests.probe.models import Probe

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def seed(ctx, *labels):
    with tenant_transaction(ctx):
        return [Probe.objects.create(label=label) for label in labels]


def visible(ctx):
    """Labels this tenant can see, queried as the restricted app role."""
    with tenant_transaction(ctx), as_app_role():
        return sorted(Probe.objects.values_list("label", flat=True))


def test_each_tenant_sees_only_its_own_rows(ctx_a, ctx_b):
    seed(ctx_a, "a1", "a2")
    seed(ctx_b, "b1")
    assert visible(ctx_a) == ["a1", "a2"]
    assert visible(ctx_b) == ["b1"]


def test_with_no_tenant_set_the_app_sees_nothing(ctx_a):
    seed(ctx_a, "a1")
    with transaction.atomic(), as_app_role():  # no tenant_transaction: nothing was set
        assert Probe.objects.count() == 0


def test_a_superuser_ignores_rls_which_is_why_the_app_must_not_be_one(ctx_a):
    seed(ctx_a, "a1")
    with transaction.atomic():  # still the test superuser, no tenant set
        assert Probe.objects.count() == 1


def test_cannot_insert_a_row_for_another_tenant(ctx_a, ctx_b):
    other_tenant = ctx_b.tenant_id
    now = datetime.now(timezone.utc)
    with pytest.raises(DatabaseError, match="row-level security"):
        with tenant_transaction(ctx_a), as_app_role(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO probe_probe (id, public_id, cell_id, tenant_id, entity_version, "
                "created_at, updated_at, label) VALUES (%s, %s, %s, %s, 1, %s, %s, 'sneaky')",
                [uuid.uuid4(), "x" * 22, ctx_a.cell_id, other_tenant, now, now],
            )
    assert visible(ctx_b) == []


def test_cannot_update_or_delete_another_tenants_rows(ctx_a, ctx_b):
    (b_row,) = seed(ctx_b, "b1")
    with tenant_transaction(ctx_a), as_app_role():
        assert Probe.objects.filter(public_id=b_row.public_id).update(label="hacked") == 0
        assert Probe.objects.filter(public_id=b_row.public_id).delete()[0] == 0
    assert visible(ctx_b) == ["b1"]


def test_cannot_move_own_row_into_another_tenant(ctx_a, ctx_b):
    (a_row,) = seed(ctx_a, "a1")
    with pytest.raises(DatabaseError, match="row-level security"):
        with tenant_transaction(ctx_a), as_app_role():
            Probe.objects.filter(public_id=a_row.public_id).update(tenant_id=ctx_b.tenant_id)


def test_nested_blocks_switch_tenant_and_restore_the_outer_one(ctx_a, ctx_b):
    seed(ctx_a, "a1")
    seed(ctx_b, "b1")
    with tenant_transaction(ctx_a), as_app_role():
        assert sorted(Probe.objects.values_list("label", flat=True)) == ["a1"]
        with tenant_transaction(ctx_b):
            assert sorted(Probe.objects.values_list("label", flat=True)) == ["b1"]
        assert sorted(Probe.objects.values_list("label", flat=True)) == ["a1"]


def test_context_is_cleared_after_a_normal_block(ctx_a):
    with tenant_transaction(ctx_a):
        assert get_context() == ctx_a
    assert get_context() is None
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.tenant_id', true)")
        assert cursor.fetchone()[0] in (None, "")


def test_context_is_cleared_and_work_rolled_back_after_an_error(ctx_a):
    with pytest.raises(RuntimeError), tenant_transaction(ctx_a):
        Probe.objects.create(label="will-vanish")
        raise RuntimeError("boom")
    assert get_context() is None
    with transaction.atomic():
        assert Probe.objects.count() == 0


def test_a_context_for_a_different_cell_is_refused():
    foreign = CellContext("OTHER-01", uuid.uuid4())
    with pytest.raises(ZoikoError) as excinfo, tenant_transaction(foreign):
        pass
    assert excinfo.value.code is ErrorCode.POLICY_DENIED
