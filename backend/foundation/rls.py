"""Row-level security (RLS): the database itself hides other tenants' rows.

Even if application code forgets a filter, PostgreSQL returns only rows whose cell_id and
tenant_id match the values set by tenant_transaction(). If nothing is set, NO rows match
(fail closed).

Use in a migration for every table that has cell_id + tenant_id columns:

    operations = [EnableRLS("trust_organization")]

Two things make this real, and both are deployment rules, not code:
  * The application must connect as a role that is NOT a superuser and does NOT own the tables
    (PostgreSQL skips RLS for superusers and for owners, unless FORCE is on, and never for
    superusers). Migrations run as a separate owner role.
  * RLS is a safety net. Policy checks (who may see which fields) still happen in the app.
"""
from __future__ import annotations

import re
from typing import Any, cast

from django.db import migrations

POLICY_NAME = "cell_tenant_isolation"

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_PREDICATE = (
    "cell_id = current_setting('app.cell_id', true) "
    "AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)


def _table(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError("invalid table name")
    return f'"{name}"'


def enable_rls_sql(table: str) -> list[str]:
    t = _table(table)
    return [
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",  # noqa: S608 - name validated above
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        # USING filters what you can see/update/delete; WITH CHECK stops you inserting or
        # moving a row into another tenant.
        f"CREATE POLICY {POLICY_NAME} ON {t} USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
    ]


def disable_rls_sql(table: str) -> list[str]:
    t = _table(table)
    return [
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {t}",
        f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
    ]


class EnableRLS(migrations.RunSQL):
    """Migration operation: turn on tenant isolation for one table (reversible)."""

    def __init__(self, table: str) -> None:
        # Django's stubs type `sql` as an invariant list of "str | (str, params)"; a plain list
        # of str is valid at runtime.
        super().__init__(
            sql=cast("Any", enable_rls_sql(table)),
            reverse_sql=cast("Any", disable_rls_sql(table)),
        )


def for_each_tenant(apps: Any, schema_editor: Any, step: Any) -> None:
    """Run a data migration step once per customer, with that customer's RLS context set
    (DEV-216). A migration step that just reads a table sees nothing when it runs as a role that
    row-level security applies to (FORCE applies it to the owner too); setting the context makes the
    step work whichever role runs the migration. `step(tenant_id, cell_id)` is called inside the
    migration's transaction; the settings are local to it."""
    tenant_model = apps.get_model("trust", "Tenant")
    with schema_editor.connection.cursor() as cursor:
        for tenant_id, cell_id in tenant_model.objects.values_list("id", "cell_id"):
            cursor.execute("SELECT set_config('app.cell_id', %s, true), "
                           "set_config('app.tenant_id', %s, true)", [cell_id, str(tenant_id)])
            step(tenant_id, cell_id)
        cursor.execute("SELECT set_config('app.cell_id', '', true), "
                       "set_config('app.tenant_id', '', true)")
