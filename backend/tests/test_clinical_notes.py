"""Consultation notes: encrypted, signed, never edited, corrected by a new version (DEV-304)."""

import json

import pytest
from django.db import connection

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

TEXT = "Pt reports intermittent chest tightness on exertion for two weeks. Plan: ECG, review."
TEXT2 = "Pt reports intermittent chest tightness on exertion. ECG normal. Reassured."


@pytest.fixture(autouse=True)
def on(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.CLINICAL_NOTES_ENABLED = True


def treat(clinic, doctor):
    with tenant_transaction(clinic.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=doctor,
            person_public_id=clinic.person,
            basis=trust.CareBasis.CARE_TEAM,
            organization_ref=clinic.clinic.org,
            by="system",
        )


@pytest.fixture
def clinic():
    practice = Practice()
    treat(practice, practice.doc)
    return practice


def body(r):
    return json.loads(r.content)


def draft(clinic, text=TEXT, subject="doc", day="2026-11-16"):
    r = clinic.post(
        "clinician/notes",
        {"patient": clinic.person, "note_date": day, "body": text},
        subject=subject,
    )
    assert r.status_code == 201, r.content
    return body(r)["note"]["id"]


def step(clinic, note_id, name, subject="doc", **data):
    return clinic.post(f"clinician/notes/{note_id}/{name}", data, subject=subject)


def signed(clinic, text=TEXT):
    note = draft(clinic, text)
    assert step(clinic, note, "sign").status_code == 200
    return note


def listing(clinic, subject="doc"):
    return clinic.get(f"clinician/notes?patient={clinic.person}", subject=subject)


# ------------------------------------------------------------------ the switch and who may use it
def test_everything_is_off_until_the_switch_is_turned_on(clinic, settings):
    note = signed(clinic)
    settings.CLINICAL_NOTES_ENABLED = False
    assert listing(clinic).status_code == 404
    assert clinic.get(f"clinician/notes/{note}", subject="doc").status_code == 404
    assert step(clinic, note, "amend", reason="clarification").status_code == 404


@pytest.mark.parametrize("who", ["pat", "ops"])
def test_a_patient_and_clinic_staff_cannot_use_notes(clinic, who):
    note = signed(clinic)
    assert listing(clinic, who).status_code == 403
    assert clinic.get(f"clinician/notes/{note}", subject=who).status_code == 403
    r = clinic.post(
        "clinician/notes",
        {"patient": clinic.person, "note_date": "2026-11-16", "body": TEXT},
        subject=who,
    )
    assert r.status_code == 403


def test_a_doctor_with_no_care_relationship_sees_and_writes_nothing(clinic):
    note = signed(clinic)
    assert listing(clinic, "doc2").status_code == 403
    assert clinic.get(f"clinician/notes/{note}", subject="doc2").status_code == 403
    r = clinic.post(
        "clinician/notes",
        {"patient": clinic.person, "note_date": "2026-11-16", "body": TEXT},
        subject="doc2",
    )
    assert r.status_code == 403


def test_a_doctor_loses_access_when_the_care_relationship_ends(clinic):
    note = signed(clinic)
    assert clinic.get(f"clinician/notes/{note}", subject="doc").status_code == 200
    with tenant_transaction(clinic.clinic.ctx):
        trust.end_care_links(
            actor_public_id=clinic.doc,
            person_public_id=clinic.person,
            basis=trust.CareBasis.CARE_TEAM,
            organization_ref=clinic.clinic.org,
            by="system",
        )
    assert clinic.get(f"clinician/notes/{note}", subject="doc").status_code == 403
    assert listing(clinic).status_code == 403


# ------------------------------------------------------------------ writing and reading
def test_a_doctor_writes_a_draft_and_the_list_never_shows_the_text(clinic):
    note = draft(clinic)
    r = listing(clinic)
    assert (
        r.status_code == 200
        and TEXT not in r.content.decode()
        and "chest" not in r.content.decode()
    )
    row = body(r)["notes"][0]
    assert row["id"] == note and row["state"] == "draft" and row["version"] == 1 and row["mine"]
    opened = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert opened["body"] == TEXT and opened["note_date"] == "2026-11-16"


def test_the_text_is_stored_only_as_ciphertext(clinic):
    draft(clinic, "Unmistakable sentinel phrase zebra-quokka-7741")
    with connection.cursor() as cur:
        cur.execute("select body_enc from clinicalnote_clinicalnote")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "zebra" not in stored and "quokka" not in stored


def test_bad_input_is_refused(clinic):
    for bad in (
        {"body": ""},
        {"body": "x" * 20_001},
        {"body": 5},
        {"note_date": "yesterday"},
        {"note_date": "2999-01-01"},
    ):
        fields = {"patient": clinic.person, "note_date": "2026-11-16", "body": TEXT, **bad}
        assert clinic.post("clinician/notes", fields, subject="doc").status_code == 422, bad


def test_a_draft_can_be_edited_only_by_its_author_and_is_hidden_from_others(clinic):
    treat(clinic, clinic.doc2)
    note = draft(clinic)
    assert step(clinic, note, "edit", body=TEXT2).status_code == 200
    assert body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]["body"] == TEXT2
    # another treating doctor cannot see, read, edit or sign someone else's draft
    assert body(listing(clinic, "doc2"))["notes"] == []
    assert clinic.get(f"clinician/notes/{note}", subject="doc2").status_code == 404
    assert step(clinic, note, "edit", subject="doc2", body="hijack").status_code in (403, 404)
    assert step(clinic, note, "sign", subject="doc2").status_code in (403, 404)


# ------------------------------------------------------------------ signing and correcting
def test_a_signed_note_is_never_edited_and_signing_twice_is_harmless(clinic):
    note = signed(clinic)
    assert step(clinic, note, "edit", body=TEXT2).status_code == 409
    assert step(clinic, note, "sign").status_code == 200
    got = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert got["body"] == TEXT and got["state"] == "signed" and got["signed_at"]


def test_a_colleague_with_a_care_relationship_reads_a_signed_note_but_cannot_change_it(clinic):
    treat(clinic, clinic.doc2)
    note = signed(clinic)
    assert clinic.get(f"clinician/notes/{note}", subject="doc2").status_code == 200
    assert body(listing(clinic, "doc2"))["notes"][0]["mine"] is False
    assert step(clinic, note, "amend", subject="doc2", reason="clarification").status_code == 403


def test_a_correction_is_a_new_version_and_the_old_one_stays_as_signed(clinic):
    note = signed(clinic)
    made = step(clinic, note, "amend", reason="new_information")
    assert made.status_code == 201
    new = body(made)["note"]
    assert new["version"] == 2 and new["state"] == "draft" and new["supersedes"] == note
    assert new["amend_reason"] == "new_information"
    assert body(clinic.get(f"clinician/notes/{new['id']}", subject="doc"))["note"]["body"] == TEXT
    # until the correction is signed, the original is still the signed note
    assert body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]["state"] == "signed"
    assert step(clinic, new["id"], "edit", body=TEXT2).status_code == 200
    assert step(clinic, new["id"], "sign").status_code == 200
    old = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert old["state"] == "superseded" and old["superseded_by"] == new["id"]
    assert old["body"] == TEXT  # the original text is kept
    latest = body(clinic.get(f"clinician/notes/{new['id']}", subject="doc"))["note"]
    assert latest["state"] == "signed" and latest["body"] == TEXT2
    assert [n["version"] for n in body(listing(clinic))["notes"]] == [2, 1]


def test_only_one_correction_may_be_open_and_the_reason_is_a_fixed_choice(clinic):
    note = signed(clinic)
    assert step(clinic, note, "amend", reason="because I said so").status_code == 422
    assert step(clinic, note, "amend").status_code == 422
    assert step(clinic, note, "amend", reason="clarification").status_code == 201
    assert step(clinic, note, "amend", reason="clarification").status_code == 409


def test_a_draft_or_a_replaced_note_cannot_be_amended(clinic):
    note = draft(clinic)
    assert step(clinic, note, "amend", reason="clarification").status_code == 409
    done = signed(clinic)
    new = body(step(clinic, done, "amend", reason="clarification"))["note"]["id"]
    step(clinic, new, "sign")
    assert step(clinic, done, "amend", reason="clarification").status_code == 409


# ------------------------------------------------------------------ the trail
def test_every_read_and_change_is_recorded_without_the_text(clinic):
    note = draft(clinic)
    step(clinic, note, "sign")
    clinic.get(f"clinician/notes/{note}", subject="doc")
    clinic.get(f"clinician/notes/{note}", subject="doc")
    with tenant_transaction(clinic.clinic.ctx):
        actions = list(
            AuditEvent.objects.filter(action__startswith="clinicalnote.").values_list(
                "action", flat=True
            )
        )
        dump = json.dumps(list(AuditEvent.objects.values_list("details", flat=True)), default=str)
    assert actions.count("clinicalnote.read") == 2
    assert "clinicalnote.draft_created" in actions and "clinicalnote.signed" in actions
    assert "chest" not in dump and TEXT not in dump


def test_an_unknown_note_and_unknown_steps_are_not_found(clinic):
    assert clinic.get("clinician/notes/does-not-exist-1234", subject="doc").status_code == 404
    note = draft(clinic)
    assert step(clinic, note, "delete").status_code == 404


# ------------------------------------------ the patient's own view (DEV-306)
def backdate(clinic, note_id, hours):
    from datetime import timedelta

    from modules.clinicalnote.models import ClinicalNote

    with tenant_transaction(clinic.clinic.ctx):
        row = ClinicalNote.objects.get(public_id=note_id)
        row.signed_at = row.signed_at - timedelta(hours=hours)
        row.save(update_fields=["signed_at"])


def mine(clinic, subject="pat"):
    return clinic.get("my/notes", subject=subject)


def test_a_patient_sees_a_signed_note_only_after_the_delay_and_never_a_draft(clinic):
    wait = draft(clinic)
    assert body(mine(clinic))["notes"] == []  # a draft is never shown
    step(clinic, wait, "sign")
    assert body(mine(clinic))["notes"] == []  # signed, but inside the 24 hours
    assert clinic.get(f"my/notes/{wait}", subject="pat").status_code == 404
    backdate(clinic, wait, 25)
    shown = body(mine(clinic))["notes"]
    assert [n["id"] for n in shown] == [wait] and "body" not in shown[0]
    assert TEXT not in mine(clinic).content.decode()
    opened = body(clinic.get(f"my/notes/{wait}", subject="pat"))["note"]
    assert opened["body"] == TEXT and opened["corrected"] is False


def test_a_correction_shows_the_old_version_as_corrected_once_both_have_passed_the_delay(clinic):
    first = signed(clinic)
    second = body(step(clinic, first, "amend", reason="clarification"))["note"]["id"]
    step(clinic, second, "edit", body=TEXT2)
    step(clinic, second, "sign")
    backdate(clinic, first, 30)
    assert [n["version"] for n in body(mine(clinic))["notes"]] == [
        1
    ]  # the new one is still inside the delay
    assert body(mine(clinic))["notes"][0]["corrected"] is True
    backdate(clinic, second, 30)
    assert [(n["version"], n["corrected"]) for n in body(mine(clinic))["notes"]] == [
        (2, False),
        (1, True),
    ]


def test_a_patient_reading_is_recorded_and_nobody_else_can_use_the_patient_route(clinic):
    note = signed(clinic)
    backdate(clinic, note, 25)
    clinic.get(f"my/notes/{note}", subject="pat")
    with tenant_transaction(clinic.clinic.ctx):
        reads = AuditEvent.objects.filter(action="clinicalnote.read_by_patient").count()
    assert reads == 1
    clinic.patient("other")  # another patient of the clinic
    assert clinic.get(f"my/notes/{note}", subject="other").status_code == 404
    assert body(mine(clinic, "other"))["notes"] == []
    assert (
        clinic.get(f"my/notes/{note}", subject="doc").status_code == 404
    )  # a doctor is not the patient
    assert body(mine(clinic, "doc"))["notes"] == []


def test_the_patient_routes_are_off_with_the_switch(clinic, settings):
    note = signed(clinic)
    backdate(clinic, note, 25)
    settings.CLINICAL_NOTES_ENABLED = False
    off = mine(clinic)
    assert off.status_code == 200 and body(off) == {"notes": [], "enabled": False}   # no error
    assert clinic.get(f"my/notes/{note}", subject="pat").status_code == 404


# ------------------------------------------------------------------ an addendum (DEV-309)
ADD = "Patient phoned: symptoms settled, no further action."


def test_the_author_adds_an_addendum_to_a_signed_note_and_the_note_is_untouched(clinic):
    note = signed(clinic)
    r = step(clinic, note, "addendum", body=ADD)
    assert r.status_code == 200 and body(r)["note"]["addenda"][0]["body"] == ADD
    got = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert got["body"] == TEXT and got["state"] == "signed" and got["version"] == 1
    assert [a["body"] for a in got["addenda"]] == [ADD]
    assert step(clinic, note, "addendum", body="Second.").status_code == 200
    again = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]["addenda"]
    assert [a["body"] for a in again] == [ADD, "Second."]


def test_an_addendum_is_stored_encrypted_and_absent_from_the_list(clinic):
    note = signed(clinic)
    step(clinic, note, "addendum", body="Unmistakable sentinel zebra-quokka-3302")
    with connection.cursor() as cur:
        cur.execute("select body_enc from clinicalnote_noteaddendum")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "zebra" not in stored
    assert "zebra" not in listing(clinic).content.decode()


def test_only_the_author_may_add_one_and_only_to_the_current_signed_note(clinic):
    treat(clinic, clinic.doc2)
    draft_id = draft(clinic)
    assert step(clinic, draft_id, "addendum", body=ADD).status_code == 409  # not signed yet
    note = signed(clinic)
    assert step(clinic, note, "addendum", subject="doc2", body=ADD).status_code == 403
    replacement = body(step(clinic, note, "amend", reason="clarification"))["note"]["id"]
    step(clinic, replacement, "sign")
    assert step(clinic, note, "addendum", body=ADD).status_code == 409  # replaced version
    assert step(clinic, replacement, "addendum", body=ADD).status_code == 200


def test_a_bad_addendum_is_refused(clinic):
    note = signed(clinic)
    for bad in ("", "   ", "x" * 5001, 7, None):
        assert step(clinic, note, "addendum", body=bad).status_code == 422


def test_a_patient_sees_an_addendum_a_day_after_it_was_added(clinic):
    from datetime import timedelta

    from modules.clinicalnote.models import NoteAddendum

    note = signed(clinic)
    backdate(clinic, note, 30)
    step(clinic, note, "addendum", body=ADD)
    first = body(clinic.get(f"my/notes/{note}", subject="pat"))["note"]
    assert first["body"] == TEXT and first["addenda"] == []  # the addendum is new
    with tenant_transaction(clinic.clinic.ctx):
        row = NoteAddendum.objects.get()
        row.created_at = row.created_at - timedelta(hours=25)
        row.save(update_fields=["created_at"])
    later = body(clinic.get(f"my/notes/{note}", subject="pat"))["note"]
    assert [a["body"] for a in later["addenda"]] == [ADD]


def test_an_addendum_is_recorded(clinic):
    note = signed(clinic)
    step(clinic, note, "addendum", body=ADD)
    with tenant_transaction(clinic.clinic.ctx):
        n = AuditEvent.objects.filter(action="clinicalnote.addendum_added").count()
    assert n == 1
