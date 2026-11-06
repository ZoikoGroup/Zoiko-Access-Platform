"""The database login the app uses must not be able to skip row-level security.

PostgreSQL ignores row-level security for superusers and for roles with the BYPASSRLS attribute.
If the app connected as one of those, every customer's rows would be readable by a single missing
filter and nothing would say so. In development that is how the Docker setup works (one superuser
login), so there it only logs a warning; with DEBUG off the app and the worker refuse to start
instead (docs/decisions REGISTER, DEV-018: the app connects as a restricted role, migrations run
as a separate owner role).
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

log = logging.getLogger("zoiko.dbrole")

_QUERY = "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"


def role_problems(cursor: Any) -> list[str]:
    """What is wrong with the login this connection is running as, in plain words (empty when fine).

    Uses `current_user`, not the session login: a role switched to with SET ROLE counts."""
    cursor.execute(_QUERY)
    row = cursor.fetchone()
    if row is None:
        return ["could not be looked up"]
    superuser, bypass = row
    problems = []
    if superuser:
        problems.append("is a superuser")
    if bypass:
        problems.append("has the BYPASSRLS attribute")
    return problems


def assert_safe_database_role() -> None:
    """Call once when a web server or the worker starts.

    With DEBUG on (or REQUIRE_RESTRICTED_DB_ROLE off, test settings only): log a warning if the
    login can skip row-level security, and carry on. Otherwise: raise, so a deployment that would
    silently stop separating customers does not start at all."""
    with connection.cursor() as cursor:
        problems = role_problems(cursor)
    if not problems:
        return
    reason = " and ".join(problems)
    if settings.DEBUG or not settings.REQUIRE_RESTRICTED_DB_ROLE:
        log.warning("The database login %s, so row-level security does not protect customers from "
                    "each other here. Fine for development only.", reason)
        return
    raise ImproperlyConfigured(
        f"Refusing to start: the database login {reason}, so PostgreSQL would ignore row-level "
        "security and customers' data would not be separated. Connect the app and the worker as a "
        "restricted role (not a superuser, no BYPASSRLS) and run migrations as a separate owner "
        "role.")
