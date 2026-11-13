"""The OpenAPI document matches the live routes and has not drifted (DEV-150)."""
import json
import os
import re
from pathlib import Path

import pytest
from django.test import Client

from api import openapi, urls

SNAPSHOT = Path(__file__).resolve().parent.parent / "api" / "openapi.snapshot.json"


def test_every_live_route_is_in_the_document_and_nothing_else():
    spec = openapi.build_spec()
    live = {"/" + re.sub(r"<(?:[a-z]+:)?(\w+)>", r"{\1}", r)
            for r, _ in openapi._walk(urls.urlpatterns)}
    assert set(spec["paths"]) == live and len(live) > 100


def test_the_document_is_valid_openapi_31_in_structure():
    spec = openapi.build_spec()
    assert spec["openapi"] == "3.1.0" and spec["info"]["title"] and spec["info"]["version"]
    for path, item in spec["paths"].items():
        assert path.startswith("/") and item
        for method, op in item.items():
            assert method in {"get", "post", "put", "patch", "delete"}
            assert op["operationId"] and op["summary"] and "default" in op["responses"]
            names = re.findall(r"{(\w+)}", path)
            assert [p["name"] for p in op["parameters"] if p["in"] == "path"] == names
    ids = [op["operationId"] for i in spec["paths"].values() for op in i.values()]
    assert len(ids) == len(set(ids))


def test_login_requirements_are_stated_and_the_open_routes_are_few():
    spec = openapi.build_spec()
    assert spec["paths"]["/me"]["get"]["security"] == [{"bearerAuth": []}]
    assert spec["paths"]["/auth/login"]["post"]["security"] == []
    assert spec["paths"]["/openapi.json"]["get"]["security"] == []
    assert sum(1 for i in spec["paths"].values() for o in i.values() if not o["security"]) < 20


def test_the_error_shape_lists_every_error_code():
    from foundation.errors import CATALOG
    enum = openapi.build_spec()["components"]["schemas"]["Problem"]["properties"]["code"]["enum"]
    assert set(enum) == {c.value for c in CATALOG}


def test_the_document_carries_no_examples_so_it_holds_no_patient_data():
    text = json.dumps(openapi.build_spec())
    assert '"example"' not in text and '"examples"' not in text


@pytest.mark.django_db
def test_it_is_served_without_a_login():
    r = Client().get("/api/v1/openapi.json")
    assert r.status_code == 200 and json.loads(r.content)["openapi"] == "3.1.0"


def test_a_route_changed_without_updating_the_snapshot_fails_here():
    """GHE-13 T-04's start: change the API on purpose, then refresh the snapshot with
    ZOIKO_WRITE_OPENAPI=1 so the change is visible in review."""
    current = json.dumps(openapi.build_spec(), indent=1, sort_keys=True) + "\n"
    if os.environ.get("ZOIKO_WRITE_OPENAPI") == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
    assert SNAPSHOT.read_text(encoding="utf-8") == current, (
        "The API changed. Run with ZOIKO_WRITE_OPENAPI=1 to refresh api/openapi.snapshot.json.")
