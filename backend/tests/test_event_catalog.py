"""The event catalogue matches the events the code publishes (DEV-151)."""
import ast
import json
import os
import re
from pathlib import Path

import pytest
from django.test import Client

from foundation import event_catalog

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "foundation" / "asyncapi.snapshot.json"
TYPE = re.compile(r"^[a-z_]+(\.[a-z_]+)+\.v\d+$")
ALL = {**event_catalog.EVENTS, **event_catalog.DIRECT}


def published_in_source() -> set[str]:
    """Every string that looks like an event type in the product's code (not in tests)."""
    found: set[str] = set()
    for folder in ("modules", "foundation"):
        for path in (ROOT / folder).rglob("*.py"):
            if "migrations" in path.parts or path.name == "event_catalog.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and TYPE.match(node.value)):
                    found.add(node.value)
    return found


def test_every_event_in_the_code_is_in_the_catalogue_and_nothing_else():
    in_code = published_in_source()
    assert in_code - set(ALL) == set(), "published but not in the catalogue"
    assert set(ALL) - in_code == set(), "in the catalogue but never published"


def test_each_event_has_a_schema_and_a_stable_hash():
    for name in ALL:
        schema = event_catalog.schema_of(name)
        assert schema["required"] and schema["additionalProperties"] is False
        assert event_catalog.schema_hash(name) == event_catalog.schema_hash(name)
    same = {event_catalog.schema_hash(n) for n in event_catalog.EVENTS
            if n.startswith("booking.") and n != "booking.attendance.v1"}
    assert len(same) == 1


def test_no_event_can_carry_a_name_or_free_text():
    banned = {"name", "email", "phone", "body", "subject", "note", "reason_text", "address"}
    for name in ALL:
        assert banned.isdisjoint(event_catalog.schema_of(name)["properties"]), name


def test_the_document_is_valid_asyncapi_30_in_structure():
    doc = event_catalog.build_asyncapi()
    assert doc["asyncapi"] == "3.0.0" and doc["info"]["title"]
    assert set(doc["channels"]) == set(doc["components"]["messages"])
    for key, channel in doc["channels"].items():
        message = doc["components"]["messages"][key]
        assert channel["address"] == message["name"] and message["x-schema-hash"]
    for op in doc["operations"].values():
        assert op["channel"]["$ref"].split("/")[-1] in doc["channels"]
    assert '"example' not in json.dumps(doc)


@pytest.mark.django_db
def test_it_is_served_without_a_login():
    r = Client().get("/api/v1/asyncapi.json")
    assert r.status_code == 200 and json.loads(r.content)["asyncapi"] == "3.0.0"


def test_a_changed_event_without_a_refreshed_snapshot_fails_here():
    """Change an event on purpose, then refresh with ZOIKO_WRITE_OPENAPI=1 so review sees it."""
    current = json.dumps(event_catalog.build_asyncapi(), indent=1, sort_keys=True) + "\n"
    if os.environ.get("ZOIKO_WRITE_OPENAPI") == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
    assert SNAPSHOT.read_text(encoding="utf-8") == current, (
        "An event changed. Run with ZOIKO_WRITE_OPENAPI=1 to refresh the snapshot.")


# ------------------------------------------------- publishing is checked against the catalogue
def test_a_published_event_must_match_its_catalogue_entry():
    from foundation.outbox import OutboxError
    from foundation.outbox import _check_against_catalogue as check
    good = {"case_ref": "r1"}
    check("rights.requested.v1", "rights_case", good)
    for kind, about, body in (
        ("rights.requested.v1", "rights_case", {}),                           # field missing
        ("rights.requested.v1", "rights_case", {"case_ref": "r1", "x": "y"}),  # field not listed
        ("rights.requested.v1", "booking", good),                              # wrong subject
    ):
        with pytest.raises(OutboxError):
            check(kind, about, body)


def test_an_event_the_catalogue_does_not_know_is_left_to_the_scan():
    from foundation.outbox import _check_against_catalogue as check
    check("something.new.v1", "anything", {"whatever": "x"})
