import json
import logging
import re

import pytest

from foundation.errors import CATALOG, ErrorCode, ZoikoError, problem_body
from foundation.request_context import RequestIdFilter, accept_or_new


# ------------------------------------------------------------------ the catalog itself
def test_every_error_code_has_a_catalog_entry():
    assert set(CATALOG) == set(ErrorCode)


def test_codes_required_by_the_spec_exist_with_the_spec_statuses():
    # GHE-11 W17 codes
    for name in ("POLICY_DENIED", "JURISDICTION_INACTIVE", "SUBJECT_LINK_UNVERIFIED",
                 "CONFLICT_VERSION", "DUPLICATE_COMMAND", "UNSAFE_REPLAY",
                 "PARTNER_UNCONFIRMED", "CLINICAL_ACCEPTANCE_ABSENT"):
        assert name in ErrorCode.__members__
    # GHE-07/11 statuses: 409 version conflict, 422 semantic invalid, 403 policy denied, 423 hold
    assert CATALOG[ErrorCode.CONFLICT_VERSION].status == 409
    assert CATALOG[ErrorCode.VALIDATION_FAILED].status == 422
    assert CATALOG[ErrorCode.POLICY_DENIED].status == 403
    assert CATALOG[ErrorCode.HOLD_ACTIVE].status == 423


def test_catalog_text_is_static_and_never_has_placeholders():
    for spec in CATALOG.values():
        for text in (spec.title, spec.detail):
            assert "{" not in text and "%" not in text
        assert 400 <= spec.status <= 599


def test_only_version_conflict_style_errors_are_marked_retryable_when_safe():
    assert CATALOG[ErrorCode.CONFLICT_VERSION].retryable is True
    assert CATALOG[ErrorCode.POLICY_DENIED].retryable is False
    assert CATALOG[ErrorCode.UNSAFE_REPLAY].retryable is False


# ------------------------------------------------------------------ ZoikoError cannot carry data
def test_exception_text_is_only_the_code():
    assert str(ZoikoError(ErrorCode.POLICY_DENIED)) == "POLICY_DENIED"


@pytest.mark.parametrize("bad", ["John Smith", "nhs=9434765919", "birth date", "A", "x'y", ""])
def test_field_names_that_could_be_data_are_refused(bad):
    with pytest.raises(ValueError):
        ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[bad])


def test_plain_field_names_are_accepted():
    error = ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["birth_date", "address.postcode"])
    assert error.fields == ("birth_date", "address.postcode")


def test_problem_body_has_the_standard_members():
    body = problem_body(ErrorCode.CONFLICT_VERSION, "req-12345678")
    assert body["type"] == "urn:zoiko:error:CONFLICT_VERSION"
    assert body["status"] == 409
    assert body["code"] == "CONFLICT_VERSION"
    assert body["request_id"] == "req-12345678"
    assert body["retryable"] is True
    assert "fields" not in body


# ------------------------------------------------------------------ request ids
def test_safe_incoming_request_id_is_kept():
    assert accept_or_new("client-req-0001") == "client-req-0001"


@pytest.mark.parametrize(
    "bad",
    [None, "", "short", "x" * 65, "has space!!", "a\nb-header-split", "<script>alert(1)</script>"],
)
def test_unsafe_incoming_request_id_is_replaced(bad):
    replaced = accept_or_new(bad)
    assert re.fullmatch(r"[0-9a-f]{32}", replaced)


# ------------------------------------------------------------------ over HTTP
@pytest.fixture
def err_client(client, settings):
    settings.ROOT_URLCONF = "tests.urls_errors"
    return client


def body(response):
    return json.loads(response.content)


def test_ok_response_still_gets_a_request_id_header(err_client):
    response = err_client.get("/ok")
    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_client_supplied_request_id_is_echoed_and_used_in_errors(err_client):
    response = err_client.get("/denied", HTTP_X_REQUEST_ID="my-trace-12345")
    assert response["X-Request-ID"] == "my-trace-12345"
    assert body(response)["request_id"] == "my-trace-12345"


def test_malicious_request_id_is_not_echoed(err_client):
    response = err_client.get("/denied", HTTP_X_REQUEST_ID="<script>alert(1)</script>")
    assert "<script>" not in response["X-Request-ID"]


def test_policy_denied_is_a_403_problem_document(err_client):
    response = err_client.get("/denied")
    assert response.status_code == 403
    assert response["Content-Type"] == "application/problem+json"
    assert response["Cache-Control"] == "no-store"
    assert body(response)["code"] == "POLICY_DENIED"


def test_validation_error_reports_field_names_only(err_client):
    response = err_client.get("/invalid")
    assert response.status_code == 422
    assert body(response)["fields"] == ["birth_date", "address.postcode"]


def test_version_conflict_is_409_and_retryable(err_client):
    response = err_client.get("/conflict")
    assert response.status_code == 409
    assert body(response)["retryable"] is True


def test_unexpected_crash_returns_500_without_the_message(err_client, caplog):
    with caplog.at_level(logging.ERROR):
        response = err_client.get("/crash")
    assert response.status_code == 500
    assert body(response)["code"] == "INTERNAL_ERROR"
    for secret in ("John Smith", "943 476 5919", "HIV"):
        assert secret.encode() not in response.content
        assert secret not in caplog.text          # not in the logs either


def test_crash_is_logged_with_type_location_and_request_id_on_every_line(err_client, caplog):
    # Use the same filter our real log config uses, so we test the real behaviour.
    caplog.handler.addFilter(RequestIdFilter())
    caplog.handler.setFormatter(logging.Formatter("rid=%(request_id)s %(name)s %(message)s"))
    with caplog.at_level(logging.ERROR):
        response = err_client.get("/crash")
    request_id = body(response)["request_id"]
    assert "ValueError" in caplog.text
    assert "urls_errors.py" in caplog.text
    assert caplog.text.count(f"rid={request_id}") == len(caplog.records)  # no "rid=-" lines


def test_404_message_from_a_view_is_not_leaked(err_client):
    response = err_client.get("/missing")
    assert response.status_code == 404
    assert b"John Smith" not in response.content
    assert body(response)["code"] == "NOT_FOUND"


def test_unknown_url_uses_the_same_error_format(err_client):
    response = err_client.get("/no/such/page")
    assert response.status_code == 404
    assert response["Content-Type"] == "application/problem+json"
    assert body(response)["code"] == "NOT_FOUND"
