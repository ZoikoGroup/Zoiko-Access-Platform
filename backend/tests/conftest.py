import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.db import connection

from foundation.cell_context import CellContext
from foundation.dbgrants import grant_statements
from foundation.rls import enable_rls_sql

APP_ROLE = "zoiko_app"


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
