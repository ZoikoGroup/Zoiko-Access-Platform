import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction

from foundation import dbgrants
from foundation.dbgrants import (
    APPEND_ONLY,
    FULL_ACCESS,
    SEQUENCES,
    app_tables,
    differences,
    grant_statements,
    privileges_for,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

ROLE = "zoiko_grant_check"


@pytest.fixture
def role():
    """A throwaway login-less role, removed again afterwards."""
    with connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {ROLE}") if _exists(cursor) else None
        cursor.execute(f"DROP ROLE IF EXISTS {ROLE}")
        cursor.execute(f"CREATE ROLE {ROLE} NOLOGIN")
    yield ROLE
    with connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {ROLE}")
        cursor.execute(f"DROP ROLE {ROLE}")


def _exists(cursor):
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE])
    return cursor.fetchone() is not None


def apply(role):
    with connection.cursor() as cursor:
        for statement in grant_statements(role):
            cursor.execute(statement)


def wrong(role):
    with connection.cursor() as cursor:
        return differences(cursor, role)


def run(sql, *params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


# ------------------------------------------------------------------ the list
def test_each_kind_of_table_gets_its_own_rights():
    assert privileges_for("evidence_auditevent") == ("SELECT", "INSERT")
    assert privileges_for("foundation_outboxevent") == ("SELECT", "INSERT", "UPDATE", "DELETE")
    assert privileges_for("trust_actor") == ("SELECT", "INSERT", "UPDATE")
    assert privileges_for("a_table_nobody_listed") == ("SELECT", "INSERT", "UPDATE")


def test_nothing_but_the_plumbing_may_be_deleted_and_audit_tables_may_not_be_changed():
    for table in app_tables():
        rights = privileges_for(table)
        assert ("DELETE" in rights) == (table in FULL_ACCESS), table
        assert ("UPDATE" in rights) == (table not in APPEND_ONLY), table
        assert "SELECT" in rights and "INSERT" in rights and "TRUNCATE" not in rights


def test_the_tables_are_ours_and_not_djangos():
    tables = app_tables()
    for mine in ("trust_tenant", "scheduling_booking", "scheduling_bookingevent",
                 "workflow_workitemevent", "trust_suspension", "trust_incident",
                 "trust_releasenote", "evidence_auditevent", "policy_accessdecision",
                 "foundation_outboxevent", "foundation_queuedemail"):
        assert mine in tables, mine
    for django_own in ("django_migrations", "django_content_type"):
        assert django_own not in tables


def test_the_append_only_list_is_exactly_the_tables_the_database_protects():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal "
            "AND p.proname LIKE '%%append_only'")
        protected = {row[0] for row in cursor.fetchall()}
    assert protected == set(APPEND_ONLY)


def test_every_customer_table_is_covered_so_none_is_forgotten():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id'")
        with_tenant = {row[0] for row in cursor.fetchall()}
    assert with_tenant and with_tenant <= set(app_tables())


def test_the_statements_start_clean_name_every_table_once_and_never_use_public():
    statements = grant_statements("zoiko_app")
    assert statements[0] == 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "zoiko_app"'
    assert statements[1].startswith("REVOKE ALL ON ALL SEQUENCES")
    assert statements[2] == 'GRANT USAGE ON SCHEMA public TO "zoiko_app"'
    grants = [s for s in statements
              if s.startswith("GRANT") and ' ON "' in s and "SEQUENCE" not in s]
    assert len(grants) == len(app_tables())
    assert not any(s.upper().endswith(" TO PUBLIC") for s in statements)   # never everyone
    assert any("SEQUENCE" in s and "foundation_outbox_seq" in s for s in statements)
    assert all("TRUNCATE" not in s and "ALL PRIVILEGES" not in s for s in statements[3:])


@pytest.mark.parametrize("bad", ["", "Zoiko", "zoiko app", "x; DROP TABLE trust_actor", '"quoted',
                                 "a" * 64, "1abc", "role-name", None, 5])
def test_an_unsafe_role_or_table_name_is_refused(bad):
    with pytest.raises(ValueError):
        grant_statements(bad)
    with pytest.raises(ValueError):
        grant_statements("zoiko_app", [bad])
    with pytest.raises(ValueError):
        dbgrants.check_identifier(bad)


def test_the_test_setup_uses_the_production_list_for_its_own_restricted_role():
    assert wrong("zoiko_app") == []


# ------------------------------------------------------------------ really applied
def test_applying_gives_exactly_the_listed_rights_and_can_be_repeated(role):
    apply(role)
    assert wrong(role) == []
    apply(role)
    assert wrong(role) == []


def test_the_role_really_cannot_change_audit_rows_or_delete_ordinary_rows(role):
    apply(role)
    for sql in ("UPDATE evidence_auditevent SET action = 'x'", "DELETE FROM evidence_auditevent",
                "DELETE FROM trust_actor", "DELETE FROM scheduling_booking",
                "UPDATE scheduling_bookingevent SET event_type = 'x'",
                "TRUNCATE trust_actor"):
        with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
            run(f"SET LOCAL ROLE {ROLE}")
            run(sql)


def test_the_role_really_can_do_what_the_app_needs(role):
    apply(role)
    with transaction.atomic():
        run(f"SET LOCAL ROLE {ROLE}")
        run("SELECT count(*) FROM trust_tenant")
        run("UPDATE trust_actor SET status = status WHERE false")
        run("DELETE FROM foundation_outboxevent WHERE false")
        run("SELECT nextval('foundation_outbox_seq')")


def test_a_right_added_by_hand_is_found_and_then_taken_away_by_applying_again(role):
    apply(role)
    run(f"GRANT DELETE ON trust_actor TO {ROLE}")
    run(f"GRANT UPDATE ON evidence_auditevent TO {ROLE}")
    run(f"GRANT TRUNCATE ON trust_tenant TO {ROLE}")
    found = wrong(role)
    assert "trust_actor: DELETE is granted but must not be" in found
    assert "evidence_auditevent: UPDATE is granted but must not be" in found
    assert "trust_tenant: TRUNCATE is granted but must not be" in found
    apply(role)
    assert wrong(role) == []


def test_a_missing_right_is_found(role):
    apply(role)
    run(f"REVOKE INSERT ON evidence_auditevent FROM {ROLE}")
    run(f"REVOKE USAGE ON SEQUENCE foundation_outbox_seq FROM {ROLE}")
    found = wrong(role)
    assert "evidence_auditevent: INSERT is missing" in found
    assert "foundation_outbox_seq: USAGE is missing" in found
    assert len(found) == 2


def test_a_role_with_nothing_is_missing_everything(role):
    found = wrong(role)
    expected = sum(len(privileges_for(table)) for table in app_tables())
    assert len(found) >= expected and all("is missing" in line for line in found)


def test_a_table_added_later_has_no_rights_until_the_grants_are_applied_again(role):
    apply(role)
    run("CREATE TABLE zz_new_table (id int)")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT has_table_privilege(%s, 'public.zz_new_table', 'SELECT')", [ROLE])
            assert cursor.fetchone()[0] is False                    # fail closed
    finally:
        run("DROP TABLE zz_new_table")


# ------------------------------------------------------------------ the command
def command(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command("db_grants", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_without_options_it_only_prints_the_statements(role):
    out, _ = command("--role", role)
    assert out.count("GRANT") > len(app_tables()) and f'FROM "{role}"' in out
    assert out.rstrip().endswith(";")
    assert len(wrong(role)) > 0                                     # nothing was applied


def test_apply_sets_the_rights_and_check_confirms_them(role):
    out, _ = command("--role", role, "--apply")
    assert "set" in out and "exactly as listed" in out
    out, _ = command("--role", role, "--check")
    assert "exactly as listed" in out and wrong(role) == []


def test_check_fails_and_names_each_difference(role):
    apply(role)
    run(f"GRANT DELETE ON trust_actor TO {ROLE}")
    with pytest.raises(CommandError, match="1 difference"):
        command("--role", role, "--check")
    err = io.StringIO()
    with pytest.raises(CommandError):
        call_command("db_grants", "--role", role, "--check", stderr=err, stdout=io.StringIO())
    assert "trust_actor: DELETE is granted but must not be" in err.getvalue()


def test_a_missing_role_is_a_clear_error_and_nothing_is_run():
    with pytest.raises(CommandError, match="does not exist"):
        command("--role", "zoiko_no_such_role", "--apply")
    with pytest.raises(CommandError, match="does not exist"):
        command("--role", "zoiko_no_such_role", "--check")


def test_a_bad_role_name_or_both_modes_at_once_are_refused(role):
    with pytest.raises(CommandError):
        command("--role", "x; DROP TABLE trust_actor")
    with pytest.raises(CommandError):
        command("--role", role, "--apply", "--check")


def test_a_failed_apply_leaves_the_rights_as_they_were(role, monkeypatch):
    apply(role)
    before = wrong(role)
    original = dbgrants.grant_statements

    def broken(r, tables=None):
        return [*original(r, tables)[:5], "GRANT NONSENSE ON nowhere TO nobody"]

    monkeypatch.setattr("foundation.management.commands.db_grants.grant_statements", broken)
    with pytest.raises(DatabaseError):
        command("--role", role, "--apply")
    assert wrong(role) == before == []                  # the first statements were undone


def test_the_sequences_are_listed():
    assert "foundation_outbox_seq" in SEQUENCES
