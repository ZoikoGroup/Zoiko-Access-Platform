"""An unsupported API major version gets a safe answer that says which versions exist (DEV-149)."""
import json

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path", ["/api/v2/health", "/api/v0/anything", "/api/v10/a/b", "/api/beta/x"])
def test_an_unknown_version_is_refused_safely_and_points_to_v1(path):
    for method in ("get", "post", "delete"):
        r = getattr(Client(), method)(path)
        assert r.status_code == 404 and r["Content-Type"] == "application/problem+json"
        assert r["X-Supported-Api-Versions"] == "v1" and "successor-version" in r["Link"]
        assert r["Cache-Control"] == "no-store"
        assert "v2" not in json.dumps(json.loads(r.content))      # nothing echoed back


def test_a_known_version_with_an_unknown_path_is_a_plain_not_found():
    r = Client().get("/api/v1/no-such-thing-here")
    assert r.status_code == 404 and "X-Supported-Api-Versions" not in r

