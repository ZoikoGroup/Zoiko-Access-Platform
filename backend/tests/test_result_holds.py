"""Holding a lab result while who it belongs to is in doubt (DEV-385, GHE-06 I-05): during an open
duplicate-record case, and while the patient disputes it. Mostly what must be REFUSED: values,
trends and reviews of a held result, disputes by anyone else, decisions by anyone but a treating
doctor, and releasing a result the clinic agreed is not the patient's."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust.models import MatchCase, MatchState, Person
from tests import test_exchange
from tests.test_exchange import (
    _reviewed_with_values,
    accepted,
    body,
    mine,
    mine_trend,
    mine_values,
    review_result,
    step,
)

# The exchange tests' own fixtures: a clinic, its patient and doctor, and a lab.
world = test_exchange.world
dev_login = test_exchange.dev_login

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def open_case(clinic, state=MatchState.POSSIBLE_MATCH):
    """A duplicate-record case about the clinic's patient and another record, still open."""
    with tenant_transaction(clinic.clinic.ctx):
        me = Person.objects.get(public_id=clinic.person)
        other = Person.objects.create(cell_id=me.cell_id, tenant_id=me.tenant_id)
        return MatchCase.objects.create(
            ref_no="M-0901", person_a=me, person_b=other, state=state, factors={},
            confidence="medium", policy_version="test", evidence_digest="x",
            cell_id=me.cell_id, tenant_id=me.tenant_id)


def close_case(clinic, case):
    with tenant_transaction(clinic.clinic.ctx):
        MatchCase.objects.filter(pk=case.pk).update(state=MatchState.CLOSED_NOT_SAME)


def delivered(clinic, lab):
    request_id = accepted(clinic, lab)
    from tests.test_exchange import VALUES
    assert step(lab, request_id, "deliver", result_ref="RES-0901", flag="normal",
                values=VALUES).status_code == 200
    return request_id


def row_of(clinic, request_id):
    return next(r for r in mine(clinic) if r["id"] == request_id)


def dispute(clinic, request_id, reason="not_me", subject="pat"):
    return clinic.post(f"exchange/mine/{request_id}/dispute", {"reason": reason},
                       subject=subject)


def decide(clinic, request_id, outcome, note="", subject="doc"):
    return clinic.post(f"exchange/requests/{request_id}/dispute",
                       {"outcome": outcome, "note": note}, subject=subject)


def clinic_row(clinic, request_id, subject="doc"):
    rows = body(clinic.get("exchange/requests", subject=subject))["requests"]
    return next(r for r in rows if r["id"] == request_id)


# ------------------------------------------------------------------ an open duplicate-record case
def test_during_a_duplicate_check_the_patient_sees_the_result_is_held_and_no_values(world):
    _, clinic, lab = world
    request_id = _reviewed_with_values(clinic, lab, hours_ago=25)
    assert row_of(clinic, request_id)["values_ready"] is True
    case = open_case(clinic)
    row = row_of(clinic, request_id)
    assert row["values_ready"] is False and row["held"] == "identity_check"
    assert mine_values(clinic, request_id).status_code == 404
    assert mine_trend(clinic, request_id, "26604007").status_code == 404
    close_case(clinic, case)                                  # decided: released again
    assert mine_values(clinic, request_id).status_code == 200


def test_during_a_duplicate_check_a_doctor_cannot_mark_it_reviewed(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    open_case(clinic)
    assert clinic_row(clinic, request_id)["identity_check_open"] is True
    r = review_result(clinic, request_id)
    assert r.status_code == 409 and body(r)["fields"] == ["identity_check_open"]


def test_a_decided_case_does_not_hold_anything(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    case = open_case(clinic)
    close_case(clinic, case)
    assert clinic_row(clinic, request_id)["identity_check_open"] is False
    assert review_result(clinic, request_id).status_code == 200


# ------------------------------------------------------------------ the patient disputes
def test_the_patient_disputes_and_the_result_is_held_at_once(world):
    _, clinic, lab = world
    request_id = _reviewed_with_values(clinic, lab, hours_ago=25)
    r = dispute(clinic, request_id, "wrong_values")
    assert r.status_code == 201 and body(r)["dispute"]["dispute"] == "open"
    row = row_of(clinic, request_id)
    assert row["held"] == "dispute" and row["values_ready"] is False
    assert mine_values(clinic, request_id).status_code == 404
    seen = clinic_row(clinic, request_id)
    assert seen["dispute"] == "open" and seen["dispute_reason"] == "wrong_values"


def test_a_disputed_result_cannot_be_reviewed_until_the_doctor_decides(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    dispute(clinic, request_id)
    r = review_result(clinic, request_id)
    assert r.status_code == 409 and body(r)["fields"] == ["dispute_open"]
    assert decide(clinic, request_id, "confirmed").status_code == 200
    assert review_result(clinic, request_id).status_code == 200


def test_confirmed_releases_it_again(world):
    _, clinic, lab = world
    request_id = _reviewed_with_values(clinic, lab, hours_ago=25)
    dispute(clinic, request_id)
    decide(clinic, request_id, "confirmed", note="Checked the order with the patient")
    row = row_of(clinic, request_id)
    assert row["held"] is None and row["values_ready"] is True
    assert dispute(clinic, request_id).status_code == 201    # they may dispute again later


def test_not_theirs_withholds_it_for_good(world):
    from modules.trust.api import list_incidents

    platform, clinic, lab = world
    request_id = _reviewed_with_values(clinic, lab, hours_ago=25)
    dispute(clinic, request_id)
    assert decide(clinic, request_id, "not_theirs").status_code == 422    # needs a note
    r = decide(clinic, request_id, "not_theirs", note="Sample label mismatch, incident raised")
    assert r.status_code == 200 and body(r)["request"]["dispute"] == "not_theirs"
    incident_id = body(r).get("incident_id")
    assert incident_id
    with tenant_transaction(platform.ctx):
        inc = next(i for i in list_incidents() if i["public_id"] == incident_id)
        assert inc["status"] == "open" and inc["severity"] == "unknown"
        assert inc["safety_impact"] is True and inc["recorded_by_ref"] == "system"
    assert row_of(clinic, request_id)["held"] == "not_theirs"
    assert mine_values(clinic, request_id).status_code == 404
    assert dispute(clinic, request_id).status_code == 409                 # final
    assert decide(clinic, request_id, "confirmed").status_code == 409


@pytest.mark.parametrize("reason", ["", "it is wrong", "NOT_ME", 3])
def test_only_a_known_reason_code_is_accepted(world, reason):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    r = dispute(clinic, request_id, reason)
    assert r.status_code == 422 and body(r)["fields"] == ["reason"]


def test_nothing_to_dispute_before_a_result_arrives(world):
    _, clinic, lab = world
    request_id = accepted(clinic, lab)
    r = dispute(clinic, request_id)
    assert r.status_code == 409 and body(r)["fields"] == ["no_result"]


def test_one_open_dispute_at_a_time(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    dispute(clinic, request_id)
    assert dispute(clinic, request_id, "wrong_test").status_code == 409


def test_staff_and_other_patients_cannot_dispute(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    assert dispute(clinic, request_id, subject="ops").status_code in (403, 404)
    assert dispute(clinic, request_id, subject="doc").status_code in (403, 404)


def test_only_a_treating_doctor_decides(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    dispute(clinic, request_id)
    assert decide(clinic, request_id, "confirmed", subject="ops").status_code == 403
    assert decide(clinic, request_id, "confirmed", subject="pat").status_code == 403
    assert decide(clinic, request_id, "maybe").status_code == 422


def test_disputes_and_decisions_are_audited_and_in_the_history(world):
    _, clinic, lab = world
    request_id = delivered(clinic, lab)
    dispute(clinic, request_id)
    decide(clinic, request_id, "confirmed")
    steps = [s["what"] for s in body(clinic.get(f"exchange/requests/{request_id}/history",
                                                subject="doc"))["history"]]
    assert "disputed" in steps and "dispute_confirmed" in steps
    with tenant_transaction(clinic.clinic.ctx):
        actions = set(AuditEvent.objects.filter(tenant_id=clinic.clinic.ctx.tenant_id)
                      .values_list("action", flat=True))
    assert {"exchange.result_disputed", "exchange.dispute_confirmed"} <= actions
    assert json.dumps(list(actions))                         # nothing but action names


# ------------------------------------------------ consultation notes & attachments (DEV-385)
def test_during_a_duplicate_check_consultation_notes_and_attachments_are_held(world, settings):
    from datetime import timedelta

    from django.utils import timezone

    from modules.clinicalnote.models import ClinicalNote

    settings.CLINICAL_NOTES_ENABLED = True
    settings.ATTACHMENT_SCANNER = "pretend"
    _, clinic, _ = world
    r = clinic.post(
        "clinician/notes",
        {"patient": clinic.person, "note_date": "2026-11-20", "body": "Consultation note."},
        subject="doc",
    )
    assert r.status_code == 201
    note_id = body(r)["note"]["id"]
    pdf = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<<>>\n%%EOF\n"
    att_res = clinic.client.post(
        f"/api/v1/clinician/notes/{note_id}/attachments?filename=summary.pdf",
        pdf,
        content_type="application/pdf",
        **clinic.header("doc"),
    )
    assert att_res.status_code == 201
    att_id = body(att_res)["attachment"]["id"]
    assert clinic.post(f"clinician/notes/{note_id}/sign", {}, subject="doc").status_code == 200
    with tenant_transaction(clinic.clinic.ctx):
        ClinicalNote.objects.filter(public_id=note_id).update(
            signed_at=timezone.now() - timedelta(hours=25)
        )

    assert clinic.get(f"my/notes/{note_id}", subject="pat").status_code == 200
    assert clinic.get(
        f"my/notes/{note_id}/attachments/{att_id}", subject="pat"
    ).status_code == 200

    case = open_case(clinic)
    listed = body(clinic.get("my/notes", subject="pat"))["notes"]
    assert next(n for n in listed if n["id"] == note_id)["held"] == "identity_check"
    assert clinic.get(f"my/notes/{note_id}", subject="pat").status_code == 404
    assert clinic.get(
        f"my/notes/{note_id}/attachments/{att_id}", subject="pat"
    ).status_code == 404

    close_case(clinic, case)
    listed_after = body(clinic.get("my/notes", subject="pat"))["notes"]
    assert "held" not in next(n for n in listed_after if n["id"] == note_id)
    assert clinic.get(f"my/notes/{note_id}", subject="pat").status_code == 200
    assert clinic.get(
        f"my/notes/{note_id}/attachments/{att_id}", subject="pat"
    ).status_code == 200


def test_during_a_duplicate_check_a_doctor_cannot_sign_a_consultation_note(world, settings):
    settings.CLINICAL_NOTES_ENABLED = True
    _, clinic, _ = world
    r = clinic.post(
        "clinician/notes",
        {"patient": clinic.person, "note_date": "2026-11-20", "body": "Draft note."},
        subject="doc",
    )
    assert r.status_code == 201
    note_id = body(r)["note"]["id"]

    case = open_case(clinic)
    listed = body(clinic.get(f"clinician/notes?patient={clinic.person}", subject="doc"))["notes"]
    assert next(n for n in listed if n["id"] == note_id)["identity_check_open"] is True
    assert body(clinic.get(f"clinician/notes/{note_id}", subject="doc"))["note"][
        "identity_check_open"
    ] is True
    refused = clinic.post(f"clinician/notes/{note_id}/sign", {}, subject="doc")
    assert refused.status_code == 409 and body(refused)["fields"] == ["identity_check_open"]

    close_case(clinic, case)
    assert clinic.post(f"clinician/notes/{note_id}/sign", {}, subject="doc").status_code == 200
