"""Tests for patient longitudinal result trends (DEV-372).

Verifies that:
- Patients can see historical trends across their reviewed test results.
- Unreviewed results, preliminary results, or results within the 24-hour embargo are omitted.
- Test values match by SNOMED code or name.
- Invalid or blank keys are rejected.
- Audit records are emitted for every read.
"""

import datetime as dt
import json
import os
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
import django

if not django.apps.apps.ready:
    django.setup()

import pytest
from django.test import RequestFactory

from foundation.errors import ErrorCode, ZoikoError
from modules.exchange.models import ExchangeRequest, State
from modules.exchange.services import patient_value_trend


def _hb(value):
    """One haemoglobin value, in g/L."""
    return {"name": "Haemoglobin", "code": "26604007", "value": value, "unit": "g/L"}


@contextmanager
def dummy_cm(ctx=None, *args, **kwargs):
    from foundation.cell_context import CellContext, bind_context

    if ctx is None or not isinstance(ctx, CellContext):
        ctx = CellContext(
            cell_id="TEST-01",
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        )
    with bind_context(ctx):
        yield ctx


def _mock_request(
    public_id: str,
    reviewed_hours_ago: float | None,
    values: list[dict],
    preliminary: bool = False,
    state: str = State.REVIEWED,
) -> MagicMock:
    r = MagicMock(spec=ExchangeRequest)
    r.public_id = public_id
    r.label = f"Order {public_id}"
    r.state = state
    r.result_preliminary = preliminary
    r.result_version = 1
    now = dt.datetime.now(dt.timezone.utc)
    if reviewed_hours_ago is not None:
        r.reviewed_at = now - dt.timedelta(hours=reviewed_hours_ago)
        r.result_at = now - dt.timedelta(hours=reviewed_hours_ago + 1)
    else:
        r.reviewed_at = None
        r.result_at = now
    r.result_values_enc = "sealed_mock_values"
    return r


@patch("modules.exchange.services.require_context")
@patch("modules.exchange.services._open_values")
@patch("modules.exchange.services._audit")
def test_patient_value_trend_filters_embargo_and_preliminary(
    mock_audit, mock_open_values, mock_ctx
):
    ctx_mock = MagicMock()
    ctx_mock.cell_id = "TEST-01"
    ctx_mock.tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    mock_ctx.return_value = ctx_mock

    # 4 requests in history:
    # r1: 48h ago (eligible, Hb = 120)
    # r2: 2h ago (reviewed, but within 24h embargo -> must be skipped)
    # r3: 30h ago (reviewed >24h, but preliminary -> must be skipped)
    # r4: 26h ago (eligible, Hb = 135)
    r1 = _mock_request("REQ-1", 48.0, [_hb(120)])
    r2 = _mock_request("REQ-2", 2.0, [_hb(128)])
    r3 = _mock_request("REQ-3", 30.0, [_hb(130)], preliminary=True)
    r4 = _mock_request("REQ-4", 26.0, [_hb(135)])

    val_map = {
        "REQ-1": [{"name": "Haemoglobin", "code": "26604007", "value": 120, "unit": "g/L"}],
        "REQ-2": [{"name": "Haemoglobin", "code": "26604007", "value": 128, "unit": "g/L"}],
        "REQ-3": [{"name": "Haemoglobin", "code": "26604007", "value": 130, "unit": "g/L"}],
        "REQ-4": [{"name": "Haemoglobin", "code": "26604007", "value": 135, "unit": "g/L"}],
    }
    mock_open_values.side_effect = lambda req, enc: val_map[req.public_id]

    mock_qs = MagicMock()
    mock_qs.__iter__.return_value = iter([r1, r2, r3, r4])
    mock_qs.first.return_value = r1

    with patch.object(ExchangeRequest.objects, "filter") as mock_filter:
        mock_filter.return_value.exclude.return_value.order_by.return_value = mock_qs

        with dummy_cm(ctx_mock):
            points = patient_value_trend(
                person_ref="PER-100",
                tenant_ref="CLI-001",
                key="26604007",
                actor_ref="ACT-PATIENT-1",
            )

    # Only r1 and r4 should be present
    assert len(points) == 2
    assert [p["request"] for p in points] == ["REQ-1", "REQ-4"]
    assert [p["value"] for p in points] == [120, 135]
    assert all(p["reviewed"] is True for p in points)
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][1] == "exchange.patient_trend_read"


@patch("modules.exchange.services.require_context")
@patch("modules.exchange.services._open_values")
@patch("modules.exchange.services._audit")
def test_patient_value_trend_matches_by_name_case_insensitive(
    mock_audit, mock_open_values, mock_ctx
):
    ctx_mock = MagicMock()
    ctx_mock.cell_id = "TEST-01"
    ctx_mock.tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    mock_ctx.return_value = ctx_mock

    r1 = _mock_request("REQ-1", 48.0, [])
    mock_open_values.return_value = [{"name": "Serum Glucose", "value": 5.4, "unit": "mmol/L"}]

    mock_qs = MagicMock()
    mock_qs.__iter__.return_value = iter([r1])
    mock_qs.first.return_value = r1

    with patch.object(ExchangeRequest.objects, "filter") as mock_filter:
        mock_filter.return_value.exclude.return_value.order_by.return_value = mock_qs

        with dummy_cm(ctx_mock):
            # Match with different case
            points = patient_value_trend(
                person_ref="PER-100",
                tenant_ref="CLI-001",
                key="serum glucose",
                actor_ref="ACT-PATIENT-1",
            )

    assert len(points) == 1
    assert points[0]["value"] == 5.4


def test_patient_value_trend_rejects_empty_or_oversized_key():
    ctx_mock = MagicMock()
    ctx_mock.cell_id = "TEST-01"
    ctx_mock.tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    with dummy_cm(ctx_mock):
        with pytest.raises(ZoikoError) as exc_info:
            patient_value_trend(
                person_ref="PER-100",
                tenant_ref="CLI-001",
                key="   ",
                actor_ref="ACT-PATIENT-1",
            )
        assert exc_info.value.code == ErrorCode.VALIDATION_FAILED

        with pytest.raises(ZoikoError) as exc_info:
            patient_value_trend(
                person_ref="PER-100",
                tenant_ref="CLI-001",
                key="A" * 81,
                actor_ref="ACT-PATIENT-1",
            )
        assert exc_info.value.code == ErrorCode.VALIDATION_FAILED


@patch("api.views.identity_held", return_value=set())   # no open duplicate check (DEV-385)
@patch("api.views._my_customer", return_value="CLI-001")
@patch("api.views._platform_context")
@patch("api.views.tenant_transaction", dummy_cm)
@patch("api.views.trust.reachable_persons", return_value=["PER-100"])
@patch("api.views.exchange.list_for_patients")
@patch("api.views.is_allowed", return_value=True)
@patch("api.views.exchange.patient_value_trend")
@patch("api.views.evidence.record")
@patch("api.support.trust.authenticate")
def test_exchange_mine_trend_api_view(
    mock_auth,
    mock_record,
    mock_trend,
    mock_is_allowed,
    mock_list,
    mock_reachable,
    mock_platform_ctx,
    mock_cust,
    mock_held,
):
    from api.views import exchange_mine_trend

    factory = RequestFactory()
    req = factory.get(
        "/api/v1/exchange/mine/REQ-001/trend?key=HbA1c",
        HTTP_AUTHORIZATION="Bearer mock_token",
    )

    auth_mock = MagicMock()
    auth_mock.actor_public_id = "ACT-PATIENT-1"
    auth_mock.must_set_up_two_step = False      # a MagicMock attribute would count as "yes"
    auth_mock.tenant_type = "platform"          # skips the country-pack gate (DEV-383)
    mock_auth.return_value = auth_mock

    mock_platform_ctx.return_value = MagicMock()
    mock_list.return_value = [
        {"id": "REQ-001", "person": "PER-100", "kind": "lab_order", "values_ready": True}
    ]
    mock_trend.return_value = [
        {"request": "REQ-001", "label": "Order 1", "value": 38, "unit": "mmol/mol",
         "at": "2026-11-20T10:00:00Z"}
    ]

    resp = exchange_mine_trend(req, "REQ-001")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert "points" in data
    assert len(data["points"]) == 1
    assert data["points"][0]["value"] == 38
    mock_record.assert_called_once()


