"""Release safety (DEV-212): events stay readable by the last release (GHE-20 F-03), one release
identity, and no migration that would break the release before it or lose data (F-04, F-06)."""
import copy
import json
import re

import pytest
from django.core.management import CommandError, call_command
from django.db import migrations
from django.db.migrations.loader import MigrationLoader

from api import release
from foundation.contracts import event_breaking_changes
from foundation.event_catalog import build_asyncapi
from foundation.management.commands import freeze_event_contract as freeze


# ------------------------------------------------------------------ events (F-03)
def message(doc, name):
    return next(m["payload"] for m in doc["components"]["messages"].values() if m["name"] == name)


@pytest.fixture
def doc():
    return build_asyncapi()


def test_the_current_events_do_not_break_the_frozen_release():
    frozen = freeze.load_frozen()
    assert frozen is not None, "foundation/released_events.json is missing"
    problems = event_breaking_changes(frozen["asyncapi"], build_asyncapi())
    assert problems == [], (
        "These event changes would break consumers of release " + frozen["release"]
        + ". Publish a new .v2 event instead: " + "; ".join(problems))


def test_an_unchanged_catalogue_and_a_new_event_are_both_safe(doc):
    assert event_breaking_changes(doc, doc) == []
    newer = copy.deepcopy(doc)
    newer["components"]["messages"]["thing_new_v1"] = {
        "name": "thing.new.v1", "payload": {"type": "object", "properties": {}}}
    assert event_breaking_changes(doc, newer) == []


def test_a_removed_event_is_breaking(doc):
    newer = copy.deepcopy(doc)
    newer["components"]["messages"].pop("booking_confirmed_v1")
    assert event_breaking_changes(doc, newer) == [
        "booking.confirmed.v1: the event is no longer published"]


@pytest.mark.parametrize("change,expected", [
    (lambda p: p["properties"].pop("status") and p["required"].remove("status"),
     "field 'status' was removed"),
    (lambda p: p["properties"].update(extra={"type": "string"}),
     "field 'extra' was added"),
    (lambda p: p["required"].remove("status"), "field 'status' is no longer always present"),
    (lambda p: p["properties"].update(status={"type": "integer"}), "field 'status' changed type"),
    (lambda p: p.update({"x-aggregate": "other"}), "its subject changed"),
    (lambda p: p.update(additionalProperties=True), "allows fields nobody listed"),
])
def test_each_kind_of_breaking_change_is_named(doc, change, expected):
    newer = copy.deepcopy(doc)
    change(message(newer, "booking.confirmed.v1"))
    found = event_breaking_changes(doc, newer)
    assert any(expected in p and p.startswith("booking.confirmed.v1") for p in found), found


def test_freezing_refuses_a_catalogue_that_breaks_the_last_release(monkeypatch, tmp_path, doc):
    path = tmp_path / "released.json"
    monkeypatch.setattr(freeze, "FROZEN", path)
    call_command("freeze_event_contract", "r1")
    assert json.loads(path.read_text())["release"] == "r1"
    broken = copy.deepcopy(doc)
    broken["components"]["messages"].pop("booking_confirmed_v1")
    monkeypatch.setattr(freeze, "build_asyncapi", lambda: broken)
    with pytest.raises(CommandError, match="booking.confirmed.v1"):
        call_command("freeze_event_contract", "r2")
    assert json.loads(path.read_text())["release"] == "r1"        # unchanged


def test_a_release_name_must_be_plain(monkeypatch, tmp_path):
    monkeypatch.setattr(freeze, "FROZEN", tmp_path / "released.json")
    with pytest.raises(CommandError):
        call_command("freeze_event_contract", "../etc")


# ------------------------------------------------------------------ one release identity
@pytest.mark.django_db
def test_the_version_is_served_without_a_login_and_names_every_part(client):
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"build", "cell", "api_contract", "event_contract", "migrations",
                         "release_id"}
    assert body["release_id"].startswith(body["build"] + "+")
    assert all(re.fullmatch(r"[0-9a-f]{16}", body[k])
               for k in ("api_contract", "event_contract", "migrations"))
    assert r["Cache-Control"] == "no-store"
    assert client.post("/api/v1/version").status_code == 405


def test_a_different_build_is_a_different_release_but_the_cell_is_not_part_of_it(settings):
    first = release.release_identity()
    settings.ZOIKO_CELL_ID = "TEST-02"
    assert release.release_identity()["release_id"] == first["release_id"]
    settings.ZOIKO_BUILD = "something-else"
    assert release.release_identity()["release_id"] != first["release_id"]


# ------------------------------------------------------------------ migrations (F-04, F-06)
DESTRUCTIVE_OPERATIONS = (migrations.RemoveField, migrations.DeleteModel, migrations.RenameField,
                          migrations.RenameModel)
DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN)|RENAME\s+(TO|COLUMN)|TRUNCATE|DELETE\s+FROM)\b", re.IGNORECASE)
# (app, migration) pairs a person has reviewed and accepted, with the register entry. Empty today.
REVIEWED: dict[tuple[str, str], str] = {}


def _our_migrations():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    for (app, name), migration in loader.disk_migrations.items():
        module = type(migration).__module__
        if module.startswith(("modules.", "foundation.")):
            yield app, name, migration


def _sql_text(op):
    sql = op.sql
    parts = sql if isinstance(sql, (list, tuple)) else [sql]
    return " ".join(p if isinstance(p, str) else str(p[0]) for p in parts)


def test_no_migration_drops_or_renames_a_column_or_table():
    """The release before this one still reads the old columns while a rollout runs, and a rollback
    must never lose data: add a new column instead, and stop using the old one."""
    found = []
    for app, name, migration in _our_migrations():
        for op in migration.operations:
            bad = isinstance(op, DESTRUCTIVE_OPERATIONS) or (
                isinstance(op, migrations.RunSQL) and DESTRUCTIVE_SQL.search(_sql_text(op)))
            if bad and (app, name) not in REVIEWED:
                found.append(f"{app}.{name}: {type(op).__name__}")
    assert found == [], "Destructive migration steps: " + ", ".join(found)


def test_the_rule_would_catch_a_dropped_column():
    op = migrations.RunSQL("ALTER TABLE x DROP COLUMN y")
    assert DESTRUCTIVE_SQL.search(_sql_text(op))
    assert isinstance(migrations.RemoveField("m", "f"), DESTRUCTIVE_OPERATIONS)
    assert not DESTRUCTIVE_SQL.search("CREATE POLICY p ON t USING (tenant_id = x)")


def test_every_data_step_can_be_stepped_back_over():
    """A data step with no way back would make `migrate` refuse to roll back past it at all."""
    missing = [f"{app}.{name}" for app, name, migration in _our_migrations()
               for op in migration.operations
               if isinstance(op, migrations.RunPython) and op.reverse_code is None]
    assert missing == [], "RunPython without reverse_code (use migrations.RunPython.noop): " + \
        ", ".join(missing)
