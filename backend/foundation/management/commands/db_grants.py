"""Give the application's database login its rights (and check it has exactly those).

    python manage.py db_grants                      # print the SQL, change nothing
    python manage.py db_grants --apply              # run it (connect as the OWNER role)
    python manage.py db_grants --check              # compare; exit code 1 if anything differs
    python manage.py db_grants --role zoiko_app     # the application role (default zoiko_app)

Run `--apply` after every `migrate`: a new table has no rights for the app until then, so the app
cannot touch it by accident (fail closed). See docs/DEPLOYMENT.md.
"""
from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from foundation.dbgrants import check_identifier, differences, grant_statements


class Command(BaseCommand):
    help = "Print, apply or check the rights of the application's database role."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--role", default="zoiko_app", help="the application role (default zoiko_app)")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply", action="store_true", help="run the statements (as the owner role)")
        mode.add_argument("--check", action="store_true", help="compare, change nothing")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            role = check_identifier(options["role"])
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        if not options["apply"] and not options["check"]:
            self.stdout.write(";\n".join(grant_statements(role)) + ";")
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            if cursor.fetchone() is None:
                raise CommandError(f"the database role '{role}' does not exist: create it first "
                                   "(deploy/create_db_roles.sql)")
            if options["apply"]:
                with transaction.atomic():
                    for statement in grant_statements(role):
                        cursor.execute(statement)
                self.stdout.write(f"rights of '{role}' set")
            wrong = differences(cursor, role)
        if wrong:
            for line in wrong:
                self.stderr.write(line)
            raise CommandError(
                f"{len(wrong)} difference(s) between the rights '{role}' has and the list")
        self.stdout.write(f"rights of '{role}' are exactly as listed")
