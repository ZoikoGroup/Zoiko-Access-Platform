import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.db import connection

from foundation.cell_context import CellContext, tenant_transaction
from foundation.dbgrants import grant_statements
from foundation.rls import enable_rls_sql

APP_ROLE = "zoiko_app"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run one share of the suite when ZOIKO_TEST_SHARD is "i/n" (CI runs n shares at once, each
    with its own database, because the whole suite in one job outlasts GitHub's 6-hour limit).

    Whole test files stay together (their fixtures and order are unchanged). Files are dealt out,
    largest first, to the share with the fewest tests so far: the same split every time, every test
    in exactly one share. Without the variable, everything runs, as before."""
    import os

    value = os.environ.get("ZOIKO_TEST_SHARD", "")
    if not value:
        return
    index, total = (int(part) for part in value.split("/"))
    if not 0 <= index < total:
        raise pytest.UsageError("ZOIKO_TEST_SHARD must be i/n with 0 <= i < n")
    by_file: dict[str, list[pytest.Item]] = {}
    for item in items:
        by_file.setdefault(item.nodeid.split("::")[0], []).append(item)
    load = [0] * total
    owner: dict[str, int] = {}
    for name in sorted(by_file, key=lambda f: (-len(by_file[f]), f)):
        share = min(range(total), key=lambda s: (load[s], s))
        owner[name] = share
        load[share] += len(by_file[name])
    keep = [item for item in items if owner[item.nodeid.split("::")[0]] == index]
    dropped = [item for item in items if owner[item.nodeid.split("::")[0]] != index]
    items[:] = keep
    config.hook.pytest_deselected(items=dropped)


@pytest.fixture(scope="session")
def rls_probe(django_db_setup, django_db_blocker):
    """Prepare the test table the way production will be set up:
    a restricted role for the app, and row-level security switched on."""
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'zoiko_app') "
            "THEN CREATE ROLE zoiko_app NOLOGIN; END IF; END $$"
        )
        # The rights come from the same list production uses (foundation/dbgrants.py), so the tests
        # run with exactly the permissions the real application role will have.
        for statement in grant_statements(APP_ROLE):
            cursor.execute(statement)
        for statement in enable_rls_sql("probe_probe"):
            cursor.execute(statement)


@pytest.fixture
def ctx_a() -> CellContext:
    return CellContext(settings.ZOIKO_CELL_ID, uuid.uuid4())


@pytest.fixture
def ctx_b() -> CellContext:
    return CellContext(settings.ZOIKO_CELL_ID, uuid.uuid4())


@contextmanager
def as_app_role() -> Iterator[None]:
    """Act as the restricted application role for the rest of the current transaction.
    (The test database user is a superuser, and superusers skip row-level security.)"""
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {APP_ROLE}")
    yield


def publish_service(ctx: CellContext, service_ref: str) -> None:
    """Set-up shortcut for tests that are about something else. A service made through the app
    starts as a draft nobody can book (DEV-115); this makes it published without the review steps,
    which test_listing_states.py covers on their own."""
    from modules.catalog.models import Service
    with tenant_transaction(ctx):
        Service.objects.filter(public_id=service_ref).update(listing_state="published")
