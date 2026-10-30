import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.db import connection

from foundation.cell_context import CellContext
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
        cursor.execute("GRANT USAGE ON SCHEMA public TO zoiko_app")
        for table in (
            "probe_probe",
            "foundation_outboxevent",
            "foundation_inboxmessage",
            "foundation_idempotencykey",
            "foundation_queuedemail",
        ):
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO zoiko_app')
        for table in ("trust_tenant", "trust_organization", "trust_role", "trust_actor",
                      "trust_roleassignment", "trust_person", "trust_carerelationship",
                      "trust_proxygrant", "catalog_location", "catalog_service", "scheduling_slot",
                      "scheduling_booking", "trust_credential", "comms_notification",
                      "referral_referral", "trust_authtoken"):
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE ON "{table}" TO zoiko_app')
        # Audit rows are append-only: the app role may read and add, never change or remove.
        cursor.execute('GRANT SELECT, INSERT ON "evidence_auditevent" TO zoiko_app')
        cursor.execute('GRANT SELECT, INSERT ON "policy_accessdecision" TO zoiko_app')
        cursor.execute("GRANT USAGE ON SEQUENCE foundation_outbox_seq TO zoiko_app")
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
