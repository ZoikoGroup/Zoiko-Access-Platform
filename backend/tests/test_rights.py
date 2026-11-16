"""A patient's formal requests about their own data (GHE-06 data rights; DEV-126): filed by the
patient themselves, tracked by clinic staff, closed only by a clinic administrator."""
import base64
import io
import json

import pytest
from django.core.management import call_command

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.rights.models import RightsCase
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def file(p, type_="access", note="Please send me everything", subject="pat"):
    return p.post("rights", {"type": type_, "note": note}, subject=subject)


def filed(p, **kw):
    r = file(p, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["request"]


def mine(p, subject="pat"):
    r = p.get("rights", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["requests"]


def queue(p, subject="ops"):
    r = p.get("staff/rights", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def step(p, case, name, subject="ops", note=""):
    return p.post(f"staff/rights/{case}/{name}", {"note": note}, subject=subject)


# ------------------------------------------------------------------ filing
def test_a_patient_files_a_request_and_sees_it_with_a_reference(p):
    made = filed(p)
    assert made["ref"].startswith("DSR-") and made["state"] == "received"
    assert made["type"] == "access" and made["note"] == "Please send me everything"
    assert [r["id"] for r in mine(p)] == [made["id"]]


def test_the_patients_words_are_stored_sealed_not_as_text(p):
    filed(p, note="My address changed to 12 Lake Road")
    with tenant_transaction(p.clinic.ctx):
        row = RightsCase.objects.get(tenant_id=p.clinic.ctx.tenant_id)
    assert row.note_enc.startswith("zf1:") and "Lake Road" not in row.note_enc


@pytest.mark.parametrize("bad", ["", "delete_everything", "ERASURE", "0"])
def test_only_the_seven_kinds_of_request_are_accepted(p, bad):
    assert file(p, type_=bad).status_code == 422


def test_a_note_longer_than_500_characters_is_refused(p):
    assert file(p, note="x" * 501).status_code == 422
    assert file(p, note="x" * 500).status_code == 201


def test_the_same_kind_cannot_be_open_twice_but_another_kind_can(p):
    filed(p, type_="access")
    assert file(p, type_="access").status_code == 409
    assert file(p, type_="correction").status_code == 201


def test_a_patient_cannot_have_more_than_five_open_at_once(p):
    for kind in ("access", "correction", "portability", "restriction", "objection"):
        assert file(p, type_=kind).status_code == 201
    assert file(p, type_="erasure").status_code == 429


def test_only_a_patient_themselves_can_file_staff_cannot(p):
    assert file(p, subject="ops").status_code == 403
    assert file(p, subject="doc").status_code == 403


def test_nobody_sees_another_patients_requests(p):
    filed(p)
    p.patient("other")
    assert mine(p, "other") == []


# ------------------------------------------------------------------ the clinic's side
def test_staff_see_the_requests_sent_to_their_clinic_with_the_patients_own_words(p):
    made = filed(p)
    got = queue(p)
    assert [r["id"] for r in got["requests"]] == [made["id"]]
    assert got["requests"][0]["note"] == "Please send me everything"
    assert got["can_close"] is False and queue(p, "boss")["can_close"] is True


def test_another_clinics_staff_see_nothing(p):
    filed(p)
    outsider = Web()
    outsider.staff("desk")
    assert json.loads(outsider.get("staff/rights", subject="desk").content)["requests"] == []


def test_patients_and_doctors_cannot_use_the_staff_page(p):
    filed(p)
    for who in ("pat", "doc"):
        assert p.get("staff/rights", subject=who).status_code == 403


def test_staff_start_a_request_and_repeating_it_is_harmless(p):
    made = filed(p)
    assert step(p, made["id"], "start").status_code == 200
    assert step(p, made["id"], "start").status_code == 200
    assert mine(p)[0]["state"] == "in_progress"


def test_recording_the_identity_check_shows_the_patient_that_it_happened(p):
    made = filed(p)
    assert mine(p)[0]["identity_checked_at"] is None
    assert step(p, made["id"], "verify").status_code == 200
    assert mine(p)[0]["identity_checked_at"]
    assert "ops" not in p.get("rights", subject="pat").content.decode().replace("options", "")


def test_a_request_cannot_be_completed_before_who_is_asking_has_been_checked(p):
    made = filed(p, type_="erasure")
    assert step(p, made["id"], "complete", "boss", "Done").status_code == 422
    step(p, made["id"], "verify")
    assert step(p, made["id"], "complete", "boss", "Your data was deleted").status_code == 200
    assert mine(p)[0]["state"] == "completed"
    assert mine(p)[0]["decision_note"] == "Your data was deleted"


def test_only_a_clinic_administrator_can_close_a_request(p):
    made = filed(p)
    step(p, made["id"], "verify")
    assert step(p, made["id"], "complete", "ops").status_code == 403
    assert step(p, made["id"], "decline", "ops", "No").status_code == 403
    assert mine(p)[0]["state"] == "received"


def test_declining_needs_a_reason_the_patient_will_see(p):
    made = filed(p)
    assert step(p, made["id"], "decline", "boss").status_code == 422
    why = "We cannot delete a legal record"
    assert step(p, made["id"], "decline", "boss", why).status_code == 200
    seen = mine(p)[0]
    assert seen["state"] == "declined" and "legal record" in seen["decision_note"]


def test_a_closed_request_cannot_be_changed_again(p):
    made = filed(p)
    step(p, made["id"], "verify")
    step(p, made["id"], "complete", "boss")
    for name in ("start", "verify", "complete", "decline"):
        assert step(p, made["id"], name, "boss", "again").status_code in (409, 403)
    assert mine(p)[0]["state"] == "completed"


# ------------------------------------------------------------------ withdrawing
def test_the_patient_can_withdraw_an_open_request_and_repeating_it_is_harmless(p):
    made = filed(p)
    assert p.post(f"rights/{made['id']}/withdraw", subject="pat").status_code == 200
    assert p.post(f"rights/{made['id']}/withdraw", subject="pat").status_code == 200
    assert mine(p)[0]["state"] == "withdrawn"
    assert file(p).status_code == 201                     # a new one of the same kind is allowed


def test_a_finished_request_cannot_be_withdrawn(p):
    made = filed(p)
    step(p, made["id"], "verify")
    step(p, made["id"], "complete", "boss")
    assert p.post(f"rights/{made['id']}/withdraw", subject="pat").status_code == 409


def test_nobody_else_can_withdraw_a_patients_request(p):
    made = filed(p)
    p.patient("other")
    assert p.post(f"rights/{made['id']}/withdraw", subject="other").status_code in (403, 404)
    assert p.post(f"rights/{made['id']}/withdraw", subject="ops").status_code == 403
    assert mine(p)[0]["state"] == "received"


# ------------------------------------------------------------------ the record
def test_every_step_is_audited_without_the_text_of_the_request(p):
    made = filed(p, note="SECRET-WORDS-123")
    step(p, made["id"], "start")
    step(p, made["id"], "verify")
    step(p, made["id"], "complete", "boss", "CLOSING-NOTE-456")
    with tenant_transaction(p.clinic.ctx):
        found = list(AuditEvent.objects.filter(object_ref=made["id"]).order_by("chain_seq"))
    assert [e.action for e in found] == ["rights.request", "rights.start",
                                         "rights.identity_checked", "rights.completed"]
    assert "SECRET-WORDS-123" not in json.dumps([e.details for e in found])
    assert "CLOSING-NOTE-456" not in json.dumps([e.details for e in found])


def test_the_sealed_note_moves_to_a_new_key_with_the_rest(p, settings):
    filed(p, note="Please correct my phone number")
    settings.FIELD_KEYS = f"{settings.FIELD_KEYS},t2:{base64.b64encode(b'C' * 32).decode()}"
    settings.FIELD_KEY_ID = "t2"
    out = io.StringIO()
    call_command("rotate_field_keys", stdout=out)
    with tenant_transaction(p.clinic.ctx):
        row = RightsCase.objects.get(tenant_id=p.clinic.ctx.tenant_id)
    assert row.note_enc.startswith("zf1:t2:")
    assert mine(p)[0]["note"] == "Please correct my phone number"
