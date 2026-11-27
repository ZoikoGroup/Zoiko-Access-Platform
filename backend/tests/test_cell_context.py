import uuid

import pytest

from foundation.cell_context import (
    CellContext,
    MissingContextError,
    bind_context,
    build_context,
    get_context,
    require_context,
)
from foundation.errors import ErrorCode, ZoikoError
from foundation.rls import POLICY_NAME, disable_rls_sql, enable_rls_sql


@pytest.mark.parametrize("bad", ["", "a", "dev-01", "DEV 01", "DEV-01;DROP", "-DEV", "X" * 40])
def test_invalid_cell_ids_are_rejected(bad):
    with pytest.raises(ValueError):
        CellContext(bad, uuid.uuid4())


def test_tenant_must_be_a_uuid():
    with pytest.raises(ValueError):
        CellContext("DEV-01", "not-a-uuid")  # type: ignore[arg-type]


def test_build_context_uses_this_deployments_cell():
    tenant = uuid.uuid4()
    ctx = build_context(tenant)
    assert ctx.cell_id == "TEST-01" and ctx.tenant_id == tenant


def test_a_token_claiming_another_cell_is_refused():
    with pytest.raises(ZoikoError) as excinfo:
        build_context(uuid.uuid4(), claimed_cell_id="OTHER-01")
    assert excinfo.value.code is ErrorCode.POLICY_DENIED


def test_a_token_claiming_this_cell_is_accepted():
    assert build_context(uuid.uuid4(), claimed_cell_id="TEST-01").cell_id == "TEST-01"


def test_no_context_means_failure_not_a_default():
    assert get_context() is None
    with pytest.raises(MissingContextError):
        require_context()


def test_bind_context_sets_nests_and_restores():
    a = CellContext("TEST-01", uuid.uuid4())
    b = CellContext("TEST-01", uuid.uuid4())
    with bind_context(a):
        assert get_context() == a
        with bind_context(b):
            assert get_context() == b
        assert get_context() == a
    assert get_context() is None


def test_bind_context_restores_even_when_the_block_fails():
    with pytest.raises(RuntimeError), bind_context(CellContext("TEST-01", uuid.uuid4())):
        raise RuntimeError("boom")
    assert get_context() is None


# ------------------------------------------------------------------ the SQL generator (no database)
def test_enable_rls_sql_turns_on_and_forces_rls_with_one_policy():
    statements = enable_rls_sql("trust_organization")
    assert 'ENABLE ROW LEVEL SECURITY' in statements[0]
    assert 'FORCE ROW LEVEL SECURITY' in statements[1]
    assert POLICY_NAME in statements[2]
    assert "WITH CHECK" in statements[2]  # stops writing a row into another tenant


def test_disable_rls_sql_reverses_it():
    statements = disable_rls_sql("trust_organization")
    assert statements[0].startswith("DROP POLICY")
    assert "DISABLE ROW LEVEL SECURITY" in statements[-1]


@pytest.mark.parametrize(
    "bad", ['x"; DROP TABLE y; --', "Upper", "1abc", "", "has space", "a" * 64, "a.b"]
)
def test_table_names_that_could_inject_sql_are_refused(bad):
    with pytest.raises(ValueError):
        enable_rls_sql(bad)
