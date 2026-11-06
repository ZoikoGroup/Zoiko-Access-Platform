"""What the application's database login may do, table by table. One list, used everywhere.

The app connects as a restricted role (never a superuser: foundation/dbrole.py). A restricted role
can do nothing until it is given rights, so this module decides them:

* Audit-style tables are APPEND-ONLY: the app may read and add rows, never change or remove one.
  (The database also carries a trigger that refuses UPDATE and DELETE on these.)
* The delivery plumbing (outbox, inbox, idempotency keys, queued mail) is the only place the app
  may delete rows.
* Every other table: read, add and change, never delete.
* Django's own tables (migrations, content types, ...) are not the app's business: migrations run
  as the separate owner role.

The test suite grants its own restricted role from this same list, so the tests exercise exactly
the rights production gets. `manage.py db_grants` prints, applies or checks them.
"""
from __future__ import annotations

import re
from typing import Any

from django.apps import apps

# Tables the app may add to but never change or remove. Must match the tables that carry an
# "append-only" trigger in their migration (tests/test_db_grants.py checks that both agree).
APPEND_ONLY = frozenset({
    "evidence_auditevent",
    "policy_accessdecision",
    "scheduling_bookingevent",
    "workflow_workitemevent",
    "trust_releasenote",
    "ai_aipermitevent",
    "ai_airequest",
    "ai_airesponseevidence",
})

# The one place the app deletes rows (finished plumbing). probe_probe exists only under test
# settings.
FULL_ACCESS = frozenset({
    "foundation_outboxevent",
    "foundation_inboxmessage",
    "foundation_idempotencykey",
    "foundation_queuedemail",
    "probe_probe",
})

SEQUENCES = ("foundation_outbox_seq",)

# Django's own apps: nothing in them is used while serving requests.
NOT_THE_APPS = frozenset({"contenttypes", "auth", "admin", "sessions"})

ALL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def check_identifier(name: str) -> str:
    """A role or table name that is safe to put inside a SQL statement (lower-case word)."""
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise ValueError(f"not a valid database name: {name!r}")
    return name


def app_tables() -> list[str]:
    """Every table of our own models, sorted."""
    return sorted({
        m._meta.db_table for m in apps.get_models()
        if m._meta.app_label not in NOT_THE_APPS and m._meta.managed})


def privileges_for(table: str) -> tuple[str, ...]:
    """The rights the app role gets on one table."""
    if table in APPEND_ONLY:
        return ("SELECT", "INSERT")
    if table in FULL_ACCESS:
        return ("SELECT", "INSERT", "UPDATE", "DELETE")
    return ("SELECT", "INSERT", "UPDATE")


def grant_statements(role: str, tables: list[str] | None = None) -> list[str]:
    """SQL that leaves `role` with exactly the rights above and nothing else. Safe to repeat:
    it starts from a clean slate, so a right that was added by hand is taken away again."""
    role = check_identifier(role)
    chosen = tables if tables is not None else app_tables()
    statements = [
        f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"',
        f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{role}"',
        f'GRANT USAGE ON SCHEMA public TO "{role}"',
    ]
    for table in chosen:
        table = check_identifier(table)
        statements.append(f'GRANT {", ".join(privileges_for(table))} ON "{table}" TO "{role}"')
    for sequence in SEQUENCES:
        statements.append(f'GRANT USAGE ON SEQUENCE "{check_identifier(sequence)}" TO "{role}"')
    return statements


def differences(cursor: Any, role: str, tables: list[str] | None = None) -> list[str]:
    """Where the rights `role` really has differ from the list above (empty = exactly right)."""
    role = check_identifier(role)
    found: list[str] = []
    for table in tables if tables is not None else app_tables():
        wanted = set(privileges_for(check_identifier(table)))
        for privilege in ALL_PRIVILEGES:
            cursor.execute(
                "SELECT has_table_privilege(%s, %s, %s)", [role, f"public.{table}", privilege])
            has = bool(cursor.fetchone()[0])
            if has and privilege not in wanted:
                found.append(f"{table}: {privilege} is granted but must not be")
            elif not has and privilege in wanted:
                found.append(f"{table}: {privilege} is missing")
    for sequence in SEQUENCES:
        cursor.execute(
            "SELECT has_sequence_privilege(%s, %s, 'USAGE')", [role, f"public.{sequence}"])
        if not cursor.fetchone()[0]:
            found.append(f"{sequence}: USAGE is missing")
    return found
