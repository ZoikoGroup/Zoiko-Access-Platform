"""Unit tests for FHIR intake and quarantine (DEV-315, docs/FHIR-DESIGN.md, GHE-13 T-25, T-60).

Tests transport signature checks, size checks, strict FHIR R4 validation, quarantine on failure,
intake mapping for DiagnosticReport, flag mapping, idempotency, and staff triage.
"""

import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
import django

if not django.apps.apps.ready:
    django.setup()

import uuid

import pytest

from foundation import fhir
from foundation.errors import ErrorCode, ZoikoError
from modules.exchange.models import State
from modules.integration.delivery import signature_header
from modules.integration.fhir import (
    MAX_FHIR_BYTES,
    _clean_result_ref,
    _map_flag,
    quarantine_step,
    receive_fhir,
)
from modules.integration.models import ClientState, FhirQuarantineState


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


class DummyClient:
    def __init__(
        self,
        public_id="pc_12345678",
        key_id="pc.customer123.aabbccdd",
        secret="whsec_testsecret1234567890abcdef",  # noqa: S107 - test double, not a secret
    ):
        self.public_id = public_id
        self.key_id = key_id
        self.secret = secret
        self.secret_enc = "enc_" + secret
        self.tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        self.state = ClientState.ACTIVE
        self.rate_per_minute = 60
        self.last_used_at = None

    def save(self, **kwargs):
        pass


def test_clean_result_ref():
    assert _clean_result_ref("valid-ref_123") == "valid-ref_123"
    assert _clean_result_ref("inv@lid ref#!") == "inv_lid_ref__"
    assert _clean_result_ref("")[:4] == "res_"
    assert len(_clean_result_ref("a" * 100)) <= 40


def test_map_flag():
    # Critical codes
    assert _map_flag([{"coding": [{"system": "sys", "code": "CRIT"}]}]) == "critical"
    assert _map_flag([{"coding": [{"system": "sys", "code": "AA"}]}]) == "critical"
    # Abnormal codes
    assert _map_flag([{"coding": [{"system": "sys", "code": "A"}]}]) == "abnormal"
    assert _map_flag([{"coding": [{"system": "sys", "code": "high"}]}]) == "abnormal"
    # Normal codes
    assert _map_flag([{"coding": [{"system": "sys", "code": "N"}]}]) == "normal"
    assert _map_flag([{"coding": [{"system": "sys", "code": "neg"}]}]) == "normal"
    # Unknown/default
    assert _map_flag(None) == "normal"
    assert _map_flag([]) == "normal"


def test_receive_fhir_signature_and_headers():
    client = DummyClient()
    body = b'{"resourceType":"ServiceRequest"}'
    delivery_id = "del-00010001"

    # 1. Body exceeds maximum allowed
    large_body = b"x" * (MAX_FHIR_BYTES + 1)
    status, res, _ = receive_fhir(raw_body=large_body, signature="bad", delivery_id=delivery_id)
    assert status == 413
    assert res["error"] == "body_too_large"

    # 2. Malformed signature header -> 401
    with pytest.raises(ZoikoError) as exc:
        receive_fhir(raw_body=body, signature="bad_format", delivery_id=delivery_id)
    assert exc.value.code == ErrorCode.UNAUTHENTICATED

    # 3. Invalid delivery_id format -> 401
    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, delivery_id, body)
    with pytest.raises(ZoikoError) as exc:
        receive_fhir(raw_body=body, signature=sig, delivery_id="bad/id")
    assert exc.value.code == ErrorCode.UNAUTHENTICATED

    # 4. Timestamp skewed beyond 5 minutes -> 401
    old_stamp = stamp - 600
    old_sig = signature_header(client.secret, client.key_id, old_stamp, delivery_id, body)
    with pytest.raises(ZoikoError) as exc:
        receive_fhir(raw_body=body, signature=old_sig, delivery_id=delivery_id)
    assert exc.value.code == ErrorCode.UNAUTHENTICATED


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.record")
def test_malformed_json_quarantined(
    mock_rec, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar
):
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_quar.filter.return_value.count.return_value = 0
    mock_quar.filter.return_value.first.return_value = None

    q_mock = MagicMock()
    q_mock.public_id = "quar_001"
    mock_quar.create.return_value = q_mock

    bad_body = b"not json at all"
    delivery_id = "del-test-01"
    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, delivery_id, bad_body)

    status, res, _ = receive_fhir(raw_body=bad_body, signature=sig, delivery_id=delivery_id)
    assert status == 202
    assert res["quarantined"] is True
    assert res["error"] == "invalid_json"
    assert res["id"] == "quar_001"

    # Verify quarantine created
    mock_quar.create.assert_called_once()
    kwargs = mock_quar.create.call_args.kwargs
    assert kwargs["resource_type"] == "unknown"
    assert kwargs["issues"][0]["code"] == "format"


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.record")
def test_invalid_fhir_resource_quarantined_zero_clinical_state(
    mock_rec, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar
):
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_quar.filter.return_value.count.return_value = 0
    mock_quar.filter.return_value.first.return_value = None

    q_mock = MagicMock()
    q_mock.public_id = "quar_002"
    mock_quar.create.return_value = q_mock

    # ServiceRequest with forbidden element 'contained' and missing required elements
    invalid_resource = {
        "resourceType": "ServiceRequest",
        "contained": [{"resourceType": "Patient"}],
    }
    raw = json.dumps(invalid_resource).encode()
    delivery_id = "del-test-02"
    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, delivery_id, raw)

    status, res, _ = receive_fhir(raw_body=raw, signature=sig, delivery_id=delivery_id)
    assert status == 202
    assert res["quarantined"] is True
    assert res["id"] == "quar_002"
    codes = [i["code"] for i in res["issues"]]
    assert "forbidden" in codes
    assert "required" in codes


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.require_context", return_value=MagicMock(tenant_id="tenant_01"))
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.exchange")
@patch("modules.integration.fhir.record")
def test_diagnostic_report_for_missing_order_quarantined(
    mock_rec, mock_exchange, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar, mock_req_ctx
):
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_trust.tenant_public_id.return_value = "cust12345"
    mock_trust.list_tenants.return_value = [
        {"type": "platform", "status": "active", "public_id": "plat0001"}]
    mock_quar.filter.return_value.count.return_value = 0
    mock_quar.filter.return_value.first.return_value = None

    q_mock = MagicMock()
    q_mock.public_id = "quar_003"
    mock_quar.create.return_value = q_mock

    report = {
        "resourceType": "DiagnosticReport",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "id": "rep-101",
        "status": "final",
        "code": {
            "coding": [
                {"system": "http://snomed.info/sct", "code": "26604007"},
                {"system": "http://loinc.org", "code": "57021-8"},
            ]
        },
        "subject": {"reference": "Patient/pat-001"},
        "basedOn": [{"reference": "ServiceRequest/req-nonexistent"}],
        "conclusion": "Normal.",
    }
    raw = json.dumps(report).encode()
    delivery_id = "del-test-03"
    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, delivery_id, raw)

    mock_exchange.delivery_lookup.return_value = None  # no such request
    status, res, _ = receive_fhir(raw_body=raw, signature=sig, delivery_id=delivery_id)

    assert status == 202
    assert res["quarantined"] is True
    assert res["error"] == "request_not_found"


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.require_context", return_value=MagicMock(tenant_id="tenant_01"))
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.exchange")
@patch("modules.integration.fhir.record")
def test_valid_diagnostic_report_advances_exchange_to_result_available(
    mock_rec, mock_exchange, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar, mock_req_ctx
):
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_trust.tenant_public_id.return_value = "cust12345"
    mock_trust.list_tenants.return_value = [
        {"type": "platform", "status": "active", "public_id": "plat0001"}]
    mock_quar.filter.return_value.count.return_value = 0
    mock_quar.filter.return_value.first.return_value = None

    report = {
        "resourceType": "DiagnosticReport",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "id": "crit-rep-101",
        "status": "final",
        "code": {
            "coding": [
                {"system": "http://snomed.info/sct", "code": "104866001"},
                {"system": "http://loinc.org", "code": "2823-3"},
            ]
        },
        "subject": {"reference": "Patient/pat-002"},
        "basedOn": [{"reference": "ServiceRequest/req-valid-001"}],
        "conclusion": "CRITICAL: Potassium elevated at 6.8 mmol/L.",
        "conclusionCode": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                        "code": "CRIT",
                    }
                ]
            }
        ],
    }
    raw = json.dumps(report).encode()
    delivery_id = "del-test-04"
    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, delivery_id, raw)

    mock_exchange.delivery_lookup.return_value = {
        "from_tenant_ref": "cust12345",
        "to_tenant_ref": "partner_tenant_99",
        "state": State.IN_PROGRESS.value,
    }

    mock_exchange.partner_step.return_value = {"changed": True, "state": "result_available"}

    status, res, _ = receive_fhir(raw_body=raw, signature=sig, delivery_id=delivery_id)

    assert status == 202
    assert res["quarantined"] is False
    assert res["request_id"] == "req-valid-001"
    assert res["state"] == "result_available"

    # Verify exchange step was called with mapped flag and summary
    mock_exchange.partner_step.assert_called_once_with(
        request_ref="req-valid-001",
        tenant_ref="partner_tenant_99",
        step="deliver",
        actor_ref=client.public_id,
        result_ref="crit-rep-101",
        summary="CRITICAL: Potassium elevated at 6.8 mmol/L.",
        flag="critical",
        clock=None,
        values=[],
    )


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
def test_idempotent_duplicate_delivery(mock_dec, mock_trust, mock_pclient, mock_quar):
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_quar.filter.return_value.count.return_value = 0

    body = b'{"resourceType":"ServiceRequest"}'
    digest = hashlib.sha256(body).hexdigest()
    delivery_id = "del-dup-01"

    # Seen with same digest -> duplicate 202
    seen_q = MagicMock()
    seen_q.body_digest = digest
    seen_q.public_id = "q_existing"
    mock_quar.filter.return_value.first.return_value = seen_q

    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, delivery_id, body)
    status, res, _ = receive_fhir(raw_body=body, signature=sig, delivery_id=delivery_id)
    assert status == 202
    assert res["duplicate"] is True

    # Seen with DIFFERENT digest -> 409 conflict
    seen_q.body_digest = "different_digest"
    status, res, _ = receive_fhir(raw_body=body, signature=sig, delivery_id=delivery_id)
    assert status == 409
    assert res["error"] == "delivery_id_reused_with_different_content"


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.policy")
@patch("modules.integration.fhir.record")
def test_staff_quarantine_triage(mock_rec, mock_policy, mock_quar):
    mock_policy.check_receipt.return_value = None

    row = MagicMock()
    row.public_id = "quar_123"
    row.delivery_id = "del-01"
    row.client.label = "Apex Lab"
    row.client.public_id = "pc_apex"
    row.resource_type = "DiagnosticReport"
    row.partner_ref = "rep-1"
    row.body_digest = "digest123"
    row.issues = [{"path": "code", "code": "required", "text": "missing"}]
    row.state = FhirQuarantineState.HELD
    row.created_at = MagicMock()
    row.created_at.isoformat.return_value = "2026-11-18T09:00:00Z"
    row.decided_at = None
    row.decided_by_ref = ""
    row.decision_note = ""

    mock_quar.select_for_update.return_value.filter.return_value.first.return_value = row

    # Discard action
    done = quarantine_step(
        quarantine_ref="quar_123",
        step="discard",
        actor_public_id="actor_staff_01",
        decision=MagicMock(),
        organization_ref="org_clinic_01",
        note="Invalid test format",
    )
    assert row.state == FhirQuarantineState.DISCARDED
    assert row.decided_by_ref == "actor_staff_01"
    assert row.decision_note == "Invalid test format"
    assert done["state"] == FhirQuarantineState.DISCARDED


@patch("modules.integration.fhir.tenant_transaction", dummy_cm)
@patch("modules.integration.fhir.transaction.atomic", dummy_cm)
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.exchange")
@patch("modules.integration.fhir.record")
def test_a_loinc_only_report_is_held_because_uk_core_needs_snomed_ct(
    mock_rec, mock_exchange, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar
):
    """DEV-321: the lead switched the intake to UK Core; a report with no SNOMED CT code is held for
    a person and moves nothing."""
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_quar.filter.return_value.count.return_value = 0
    mock_quar.filter.return_value.first.return_value = None
    q_mock = MagicMock()
    q_mock.public_id = "quar_uk"
    mock_quar.create.return_value = q_mock
    report = {
        "resourceType": "DiagnosticReport",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "id": "rep-loinc",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "57021-8"}]},
        "subject": {"reference": "Patient/pat-001"},
        "basedOn": [{"reference": "ServiceRequest/req-1"}],
        "conclusion": "Normal.",
    }
    raw = json.dumps(report).encode()
    stamp = int(time.time())
    sig = signature_header(client.secret, client.key_id, stamp, "del-uk-01", raw)
    status, res, _ = receive_fhir(raw_body=raw, signature=sig, delivery_id="del-uk-01")
    assert status == 202 and res["quarantined"] is True
    assert {"path": "code", "code": "required"} in [
        {"path": i["path"], "code": i["code"]} for i in res["issues"]
    ]
    mock_exchange.partner_step.assert_not_called()  # no clinical state



# ------------------------------------------------ corrections (DEV-325, FHIR step 5)
def _corrected_report(status):
    return {
        "resourceType": "DiagnosticReport",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "id": "rep-201",
        "status": status,
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "26604007"}]},
        "subject": {"reference": "Patient/pat-002"},
        "basedOn": [{"reference": "ServiceRequest/req-valid-001"}],
        "conclusion": "Corrected: haemoglobin 128 g/L.",
        "conclusionCode": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": "N"}]}],
    }


def _intake(mock_exchange, mock_dec, mock_trust, mock_pclient, mock_quar, report, state):
    client = DummyClient()
    mock_pclient.select_for_update.return_value.filter.return_value.first.return_value = client
    mock_dec.return_value = client.secret
    mock_trust.context_for.return_value = MagicMock()
    mock_trust.tenant_public_id.return_value = "cust12345"
    mock_trust.list_tenants.return_value = [
        {"type": "platform", "status": "active", "public_id": "plat0001"}]
    mock_quar.filter.return_value.count.return_value = 0
    mock_quar.filter.return_value.first.return_value = None
    mock_exchange.delivery_lookup.return_value = {
        "from_tenant_ref": "cust12345", "to_tenant_ref": "partner_tenant_99", "state": state,
    }
    mock_exchange.correct_result.return_value = {"changed": True, "state": "result_available"}
    mock_exchange.partner_step.return_value = {"changed": True, "state": "result_available"}
    raw = json.dumps(report).encode()
    delivery_id = f"del-corr-{report['status']}-{state}"
    sig = signature_header(client.secret, client.key_id, int(time.time()), delivery_id, raw)
    return client, receive_fhir(raw_body=raw, signature=sig, delivery_id=delivery_id)


_PATCHES: list[Any] = [
    patch("modules.integration.fhir.tenant_transaction", dummy_cm),
    patch("modules.integration.fhir.transaction.atomic", dummy_cm),
    patch("modules.integration.fhir.require_context",
          return_value=MagicMock(tenant_id="tenant_01")),
]


def _patched(fn: Any) -> Any:
    for p in reversed(_PATCHES):
        fn = p(fn)
    return fn


@_patched
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.exchange")
@patch("modules.integration.fhir.record")
def test_a_corrected_report_for_a_delivered_result_becomes_a_new_version(
    mock_rec, mock_exchange, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar, _ctx
):
    client, (status, res, _) = _intake(mock_exchange, mock_dec, mock_trust, mock_pclient,
                                       mock_quar, _corrected_report("corrected"), "reviewed")
    assert status == 202 and res["quarantined"] is False
    mock_exchange.partner_step.assert_not_called()
    mock_exchange.correct_result.assert_called_once_with(
        request_ref="req-valid-001", tenant_ref="partner_tenant_99", actor_ref=client.public_id,
        result_ref="rep-201", summary="Corrected: haemoglobin 128 g/L.", flag="normal",
        correction="corrected", clock=None, values=[],
    )
    assert mock_rec.call_args.kwargs["action"] == "integration.fhir_report_corrected"


@_patched
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.exchange")
@patch("modules.integration.fhir.record")
def test_an_amended_report_before_any_delivery_is_simply_delivered(
    mock_rec, mock_exchange, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar, _ctx
):
    _, (status, res, _) = _intake(mock_exchange, mock_dec, mock_trust, mock_pclient, mock_quar,
                                  _corrected_report("amended"), "in_progress")
    assert status == 202 and res["quarantined"] is False
    mock_exchange.correct_result.assert_not_called()
    assert mock_exchange.partner_step.call_args.kwargs["step"] == "deliver"


@_patched
@patch("modules.integration.fhir.FhirQuarantine.objects")
@patch("modules.integration.fhir.PartnerClient.objects")
@patch("modules.integration.fhir.trust")
@patch("modules.integration.fhir.decrypt")
@patch("modules.integration.fhir.encrypt", return_value="enc_payload")
@patch("modules.integration.fhir.exchange")
@patch("modules.integration.fhir.record")
def test_a_withdrawn_report_is_held_for_a_person_and_changes_nothing(
    mock_rec, mock_exchange, mock_enc, mock_dec, mock_trust, mock_pclient, mock_quar, _ctx
):
    for withdrawn in ("entered-in-error", "cancelled"):
        _, (status, res, _) = _intake(mock_exchange, mock_dec, mock_trust, mock_pclient,
                                      mock_quar, _corrected_report(withdrawn), "reviewed")
        assert status == 202 and res["quarantined"] is True and res["error"] == "withdrawn"
    mock_exchange.correct_result.assert_not_called()
    mock_exchange.partner_step.assert_not_called()
