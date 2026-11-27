"""Partner adapters plugged in by configuration (DEV-361)."""
import hashlib
import hmac
import io
import json
import urllib.error

import pytest

from modules.connector import adapters, plugins
from modules.connector.adapters import (
    AdapterTimeout,
    AdapterUnavailable,
    AdapterUnsupported,
    OperationView,
    Outcome,
)


class GoodAdapter:
    key = "test-lab"
    idempotent = True
    profile_version = "2"

    def submit(self, operation):
        return adapters.PartnerResult(Outcome.ACCEPTED)

    def query(self, operation):
        return adapters.PartnerResult(Outcome.CONFIRMED)


class NoKey:
    idempotent = True

    def submit(self, operation): ...

    def query(self, operation): ...


good = GoodAdapter()
OP = OperationView("opref000000000000000001", "booking.create", "subject-1", "idem-00000001",
                   "", {"a": 1})


@pytest.fixture(autouse=True)
def fresh(settings):
    before = dict(adapters._adapters)
    settings.CONNECTOR_PLUGINS = ""
    settings.CONNECTOR_HTTP = ""
    yield settings
    settings.CONNECTOR_PLUGINS = ""
    settings.CONNECTOR_HTTP = ""
    plugins.load_configured()
    adapters._adapters.clear()
    adapters._adapters.update(before)


def test_adapter_code_named_in_the_settings_is_loaded_and_marked_as_plugged_in(fresh):
    fresh.CONNECTOR_PLUGINS = "tests.test_connector_plugins:good"
    plugins.load_configured()
    assert adapters.get("test-lab") is good
    assert plugins.sources()["test-lab"] == "plugin"
    assert plugins.problems() == []


def test_a_class_is_made_into_an_adapter(fresh):
    fresh.CONNECTOR_PLUGINS = "tests.test_connector_plugins:GoodAdapter"
    plugins.load_configured()
    assert isinstance(adapters.get("test-lab"), GoodAdapter)


@pytest.mark.parametrize("named,why", [
    ("tests.no_such_module:thing", "could not be loaded"),
    ("tests.test_connector_plugins:missing", "could not be loaded"),
    ("tests.test_connector_plugins:NoKey", "no usable key"),
])
def test_what_does_not_load_is_listed_with_a_reason_and_stops_nothing(fresh, named, why):
    fresh.CONNECTOR_PLUGINS = named + ", tests.test_connector_plugins:good"
    plugins.load_configured()
    assert adapters.get("test-lab") is good                     # the good one still loads
    (problem,) = plugins.problems()
    assert problem["name"] == named and why in problem["reason"]


def test_a_key_cannot_be_taken_twice(fresh):
    fresh.CONNECTOR_PLUGINS = ("tests.test_connector_plugins:good, "
                               "tests.test_connector_plugins:GoodAdapter")
    plugins.load_configured()
    assert adapters.get("test-lab") is good
    assert "already used" in plugins.problems()[0]["reason"]


def test_loading_again_forgets_what_the_settings_no_longer_name(fresh):
    fresh.CONNECTOR_PLUGINS = "tests.test_connector_plugins:good"
    plugins.load_configured()
    fresh.CONNECTOR_PLUGINS = ""
    plugins.load_configured()
    assert adapters.get("test-lab") is None


# ------------------------------------------------------------------ Zoiko's JSON contract
SECRET = "s3cret-for-tests"


def http_partner(fresh, url="https://lab.example/zoiko"):
    fresh.CONNECTOR_HTTP = json.dumps([{"key": "json-lab", "url": url, "secret": SECRET,
                                        "profile_version": "3"}])
    plugins.load_configured()
    return adapters.get("json-lab")


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def answering(monkeypatch, payload=None, error=None, seen=None):
    def fake(request, timeout):
        if seen is not None:
            seen.append(request)
        if error is not None:
            raise error
        return Answer(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    monkeypatch.setattr(plugins.urllib.request, "urlopen", fake)


def test_a_json_partner_is_listed_from_the_settings(fresh):
    adapter = http_partner(fresh)
    assert adapter is not None and plugins.sources()["json-lab"] == "http"
    assert adapter.profile_version == "3" and adapter.idempotent is True


def test_a_plain_http_address_is_refused_outside_development(fresh):
    fresh.CONNECTOR_ALLOW_INSECURE = False
    assert http_partner(fresh, "http://lab.example/zoiko") is None
    assert "not https" in plugins.problems()[0]["reason"]


@pytest.mark.parametrize("raw", ["not json", json.dumps([{"key": "x"}])])
def test_a_broken_http_list_is_reported(fresh, raw):
    fresh.CONNECTOR_HTTP = raw
    plugins.load_configured()
    assert plugins.problems() and adapters.get("x") is None


def test_each_request_is_signed_and_carries_the_idempotency_key(fresh, monkeypatch):
    adapter = http_partner(fresh)
    seen = []
    answering(monkeypatch, {"outcome": "accepted", "partner_ref": "P-1"}, seen=seen)
    got = adapter.submit(OP)
    assert (got.outcome, got.partner_ref) == (Outcome.ACCEPTED, "P-1")
    request = seen[0]
    expected = "sha256=" + hmac.new(SECRET.encode(), request.data, hashlib.sha256).hexdigest()
    assert request.get_header("X-zoiko-signature") == expected
    assert request.get_header("Idempotency-key") == "idem-00000001"
    assert request.full_url == "https://lab.example/zoiko/operations"


def test_asking_what_became_of_it_goes_to_the_operation(fresh, monkeypatch):
    adapter = http_partner(fresh)
    seen = []
    answering(monkeypatch, {"outcome": "confirmed", "code": "done"}, seen=seen)
    got = adapter.query(OP)
    assert (got.outcome, got.code) == (Outcome.CONFIRMED, "done")
    assert seen[0].full_url.endswith("/operations/opref000000000000000001")


@pytest.mark.parametrize("payload", [b"not json", {"outcome": "maybe"}, ["accepted"]])
def test_an_unclear_answer_is_never_taken_as_one(fresh, monkeypatch, payload):
    adapter = http_partner(fresh)
    answering(monkeypatch, payload)
    with pytest.raises(AdapterTimeout):
        adapter.submit(OP)


def test_unsupported_halts_and_refused_is_safe_to_send_again(fresh, monkeypatch):
    adapter = http_partner(fresh)
    answering(monkeypatch, {"outcome": "unsupported"})
    with pytest.raises(AdapterUnsupported):
        adapter.submit(OP)
    answering(monkeypatch, error=urllib.error.HTTPError("u", 501, "no", {}, None))
    with pytest.raises(AdapterUnsupported):
        adapter.submit(OP)
    answering(monkeypatch, error=urllib.error.URLError(ConnectionRefusedError()))
    with pytest.raises(AdapterUnavailable):
        adapter.submit(OP)
    answering(monkeypatch, error=TimeoutError())
    with pytest.raises(AdapterTimeout):
        adapter.submit(OP)
