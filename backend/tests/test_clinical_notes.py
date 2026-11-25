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
        assert row.signed_at is not None
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
    assert off.status_code == 200 and body(off) == {"notes": [], "enabled": False}  # no error
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


# ------------------------------------------------------------------ paging the notes (DEV-320)
def test_the_notes_list_pages_and_never_shows_another_doctors_draft(clinic):
    treat(clinic, clinic.doc2)
    mine = [signed(clinic, f"note {i}") for i in range(4)]
    draft(clinic, subject="doc2")  # doc2's draft: not for doc
    seen, cursor = [], None
    for _ in range(5):
        q = f"clinician/notes?patient={clinic.person}&limit=2" + (
            f"&cursor={cursor}" if cursor else ""
        )
        data = body(clinic.get(q, subject="doc"))
        seen += [n["id"] for n in data["notes"]]
        cursor = data.get("next_cursor")
        if not cursor:
            break
    assert sorted(seen) == sorted(mine) and len(seen) == 4
    cursor = body(clinic.get(f"clinician/notes?patient={clinic.person}&limit=1", subject="doc"))[
        "next_cursor"
    ]
    other = f"clinician/notes?patient={clinic.person}&limit=1&cursor={cursor}"
    assert clinic.get(other, subject="doc2").status_code == 422  # the cursor is doc's own
    assert "next_cursor" not in body(listing(clinic))  # the old shape is unchanged


# ------------------------------------------------------------------ guardians (DEV-324)
G = "guardian@example.org"


def _born(clinic, person_ref, born, name="Ada Child"):
    import datetime as dt

    from modules.trust.models import Person
    from modules.trust.profile import store_profile

    with tenant_transaction(clinic.clinic.ctx):
        store_profile(Person.objects.get(public_id=person_ref), legal_name=name, birth_date=born,
                      phone="", by="system", today=dt.date.today())


def _child_of_guardian(clinic, born, rights=("appointments", "notes")):
    """A child of the clinic with a guardian, and the doctor caring for the child. Afterwards
    `clinic.person` is the child, so the helpers above write about them."""
    guardian = clinic.login(G, trust.ActorType.PROXY, "R02")
    with tenant_transaction(clinic.clinic.ctx):
        child = trust.create_person().public_id
        grant = trust.propose_proxy_grant(
            delegate_public_id=guardian, person_public_id=child,
            relationship_type="parent", by="system")
        trust.approve_proxy_grant(grant.public_id, clinic.desk, rights=list(rights))
    _born(clinic, child, born)
    clinic.person = child
    treat(clinic, clinic.doc)
    return child


def _years_ago(years):
    import datetime as dt

    today = dt.date.today()
    try:
        return today.replace(year=today.year - years).isoformat()
    except ValueError:
        return today.replace(year=today.year - years, day=28).isoformat()


def test_a_guardian_reads_a_childs_notes_while_the_child_is_under_16(clinic):
    _child_of_guardian(clinic, _years_ago(10))
    note = signed(clinic)
    backdate(clinic, note, 25)
    shown = body(mine(clinic, G))["notes"]
    assert [n["id"] for n in shown] == [note] and shown[0]["for_name"] == "Ada Child"
    opened = clinic.get(f"my/notes/{note}", subject=G)
    assert opened.status_code == 200 and body(opened)["note"]["body"] == TEXT
    with tenant_transaction(clinic.clinic.ctx):
        assert AuditEvent.objects.filter(action="clinicalnote.read_by_guardian").count() == 1


def test_from_16_only_the_young_person_reads_their_notes(clinic):
    _child_of_guardian(clinic, _years_ago(16))  # sixteen today
    note = signed(clinic)
    backdate(clinic, note, 25)
    assert body(mine(clinic, G))["notes"] == []
    assert clinic.get(f"my/notes/{note}", subject=G).status_code == 404


def test_a_guardian_without_the_notes_part_of_the_grant_reads_nothing(clinic):
    _child_of_guardian(clinic, _years_ago(8), rights=("appointments",))
    note = signed(clinic)
    backdate(clinic, note, 25)
    assert body(mine(clinic, G))["notes"] == []
    assert clinic.get(f"my/notes/{note}", subject=G).status_code == 404


def test_the_author_can_keep_one_note_from_the_guardian(clinic):
    _child_of_guardian(clinic, _years_ago(14))
    kept, shared = signed(clinic), signed(clinic, TEXT2)
    backdate(clinic, kept, 25)
    backdate(clinic, shared, 25)
    assert step(clinic, kept, "guardian", subject="doc2", hidden=True).status_code == 403
    assert step(clinic, kept, "guardian", hidden="yes").status_code == 422
    r = step(clinic, kept, "guardian", hidden=True)
    assert r.status_code == 200 and body(r)["note"]["guardian_hidden"] is True
    assert [n["id"] for n in body(mine(clinic, G))["notes"]] == [shared]
    assert clinic.get(f"my/notes/{kept}", subject=G).status_code == 404
    step(clinic, kept, "guardian", hidden=False)
    assert {n["id"] for n in body(mine(clinic, G))["notes"]} == {kept, shared}


# ------------------------------------------------------------------ retention (DEV-324)
def test_the_nhs_retention_rules():
    import datetime as dt

    from modules.clinicalnote.services import keep_until

    last = dt.date(2020, 3, 1)
    assert keep_until(last, "1970-05-05") == dt.date(2028, 3, 1)  # adult: 8 years
    assert keep_until(last, "2012-05-05") == dt.date(2037, 5, 5)  # child of 7: 25th birthday
    assert keep_until(last, "2002-05-05") == dt.date(2028, 5, 5)  # 17 at last care: 26th
    assert keep_until(dt.date(2016, 2, 29), "1960-01-01") == dt.date(2024, 2, 29)
    assert keep_until(last, "") is None and keep_until(last, "garbage") is None  # never early


def test_retention_erases_the_text_and_keeps_the_rows(clinic):
    from modules.clinicalnote.jobs import erase_expired_all_tenants
    from modules.clinicalnote.models import ClinicalNote, NoteAddendum

    _born(clinic, clinic.person, "1960-01-01", "Pat Adult")
    old = draft(clinic, day="2010-01-05")
    step(clinic, old, "sign")
    step(clinic, old, "addendum", body=ADD)
    assert erase_expired_all_tenants() == 1  # 2010 + 8 years has passed
    with tenant_transaction(clinic.clinic.ctx):
        row = ClinicalNote.objects.get(public_id=old)
        assert row.body_enc == "" and row.medications_enc == "" and row.erased_at is not None
        assert NoteAddendum.objects.get(note=row).body_enc == ""
        assert AuditEvent.objects.filter(action="clinicalnote.erased_retention").count() == 1
    read = body(clinic.get(f"clinician/notes/{old}", subject="doc"))["note"]
    assert read["body"] == "" and read["medications"] == [] and read["erased"] is True
    assert erase_expired_all_tenants() == 0  # nothing left to erase


def test_recent_care_keeps_older_notes_and_an_unknown_birth_date_erases_nothing(clinic):
    from modules.clinicalnote.jobs import erase_expired_all_tenants

    draft(clinic, day="2010-01-05")
    assert erase_expired_all_tenants() == 0  # the birth date is not known
    _born(clinic, clinic.person, "1960-01-01", "Pat Adult")
    draft(clinic, day="2026-01-05")  # recent care: the 2010 note stays too
    assert erase_expired_all_tenants() == 0



# ------------------------------------------------------------------ coded diagnoses (DEV-344)
DX = [{"code": "38341003", "text": "Hypertension", "status": "confirmed"},
      {"code": "73211009", "text": "Diabetes mellitus", "status": "provisional"}]


def test_a_draft_carries_coded_diagnoses_signed_with_the_note_and_shown_to_the_patient(clinic):
    r = clinic.post("clinician/notes", {"patient": clinic.person, "note_date": "2026-11-16",
                                        "body": TEXT, "diagnoses": DX}, subject="doc")
    assert r.status_code == 201, r.content
    note = body(r)["note"]["id"]
    got = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert got["diagnoses"] == DX and got["has_diagnoses"] is True
    assert "Hypertension" not in listing(clinic).content.decode()  # never in the list
    step(clinic, note, "edit", body=TEXT2)  # no diagnoses given: they stay
    assert body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]["diagnoses"] == DX
    step(clinic, note, "sign")
    assert step(clinic, note, "edit", body=TEXT, diagnoses=[]).status_code == 409  # signed
    new = body(step(clinic, note, "amend", reason="clarification"))["note"]["id"]
    assert body(clinic.get(f"clinician/notes/{new}", subject="doc"))["note"]["diagnoses"] == DX
    backdate(clinic, note, 25)
    assert body(clinic.get(f"my/notes/{note}", subject="pat"))["note"]["diagnoses"] == DX


def test_diagnoses_are_stored_encrypted_and_bad_ones_refused(clinic):
    from django.db import connection

    for bad in ([{"code": "38341004", "text": "x"}], [{"code": "38341003", "text": ""}],
                [{"code": "38341003", "text": "x", "status": "maybe"}],
                [{"code": "38341003", "text": "x", "note": "free"}],
                [DX[0], DX[0]], [DX[0]] * 11, "Hypertension"):
        r = clinic.post("clinician/notes", {"patient": clinic.person, "note_date": "2026-11-16",
                                            "body": TEXT, "diagnoses": bad}, subject="doc")
        assert r.status_code == 422, bad
    clinic.post("clinician/notes", {"patient": clinic.person, "note_date": "2026-11-16",
                                    "body": TEXT, "diagnoses": DX}, subject="doc")
    with connection.cursor() as cur:
        cur.execute("select diagnoses_enc from clinicalnote_clinicalnote")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "Hypertension" not in stored and "38341003" not in stored



# ------------------------------------------------ structured medications (DEV-375)
MEDS = [
    {
        "code": "38341003",
        "name": "Amoxicillin 500mg capsules",
        "dose": "500mg TDS for 7 days",
        "status": "active",
    },
    {
        "code": "73211009",
        "name": "Paracetamol 500mg tablets",
        "dose": "1g QDS PRN",
        "status": "suspended",
    },
]


def test_a_draft_carries_structured_medications_signed_with_the_note_and_shown_to_the_patient(clinic):
    r = clinic.post("clinician/notes", {"patient": clinic.person, "note_date": "2026-11-16",
                                        "body": TEXT, "medications": MEDS}, subject="doc")
    assert r.status_code == 201, r.content
    note = body(r)["note"]["id"]
    got = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert got["medications"] == MEDS and got["has_medications"] is True
    assert "Amoxicillin" not in listing(clinic).content.decode()  # never in the list
    step(clinic, note, "edit", body=TEXT2)  # no medications given: they stay
    assert body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]["medications"] == MEDS
    step(clinic, note, "edit", body=TEXT2, medications=[MEDS[0]])
    assert body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]["medications"] == [MEDS[0]]
    step(clinic, note, "sign")
    assert step(clinic, note, "edit", body=TEXT, medications=[]).status_code == 409  # signed
    new = body(step(clinic, note, "amend", reason="clarification"))["note"]["id"]
    assert body(clinic.get(f"clinician/notes/{new}", subject="doc"))["note"]["medications"] == [MEDS[0]]
    # embargo: patient cannot read before 24h
    assert clinic.get(f"my/notes/{note}", subject="pat").status_code == 404
    backdate(clinic, note, 25)
    pat_note = body(clinic.get(f"my/notes/{note}", subject="pat"))["note"]
    assert pat_note["medications"] == [MEDS[0]]


def test_medications_are_stored_encrypted_and_bad_ones_refused(clinic):
    from django.db import connection

    for bad in (
        [{"code": "38341004", "name": "Amoxicillin", "status": "active"}],  # bad check digit
        [{"code": "38341003", "name": "", "status": "active"}],  # empty name
        [{"code": "38341003", "name": "x", "status": "unknown"}],  # bad status
        [{"code": "38341003", "name": "x", "status": "active", "route": "oral"}],  # extra field
        [MEDS[0], MEDS[0]],  # duplicate code
        [MEDS[0]] * 11,  # more than 10
        "Amoxicillin 500mg",  # not a list
    ):
        r = clinic.post("clinician/notes", {"patient": clinic.person, "note_date": "2026-11-16",
                                            "body": TEXT, "medications": bad}, subject="doc")
        assert r.status_code == 422, bad
    clinic.post("clinician/notes", {"patient": clinic.person, "note_date": "2026-11-16",
                                    "body": TEXT, "medications": MEDS}, subject="doc")
    with connection.cursor() as cur:
        cur.execute("select medications_enc from clinicalnote_clinicalnote")
        stored = " ".join(row[0] for row in cur.fetchall())
    assert stored and "Amoxicillin" not in stored and "38341003" not in stored and "500mg TDS" not in stored



# ------------------------------------------------ attachments (DEV-376)
def _pdf(extra=b""):
    return b"%PDF-1.4\n1 0 obj<< /Type /Catalog /Font /AAAAAB+Arial >>endobj\n" + extra + b"\ntrailer<<>>\n%%EOF\n"


_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb"


def upload_att(clinic, note_id, data, name="letter.pdf", content_type="application/pdf", subject="doc"):
    return clinic.client.post(
        f"/api/v1/clinician/notes/{note_id}/attachments?filename={name}",
        data,
        content_type=content_type,
        **clinic.header(subject),
    )


def delete_att(clinic, note_id, att_id, subject="doc"):
    return clinic.client.delete(
        f"/api/v1/clinician/notes/{note_id}/attachments/{att_id}",
        **clinic.header(subject),
    )


def get_att(clinic, note_id, att_id, subject="doc"):
    return clinic.client.get(
        f"/api/v1/clinician/notes/{note_id}/attachments/{att_id}",
        **clinic.header(subject),
    )


def get_pat_att(clinic, note_id, att_id, subject="pat"):
    return clinic.client.get(
        f"/api/v1/my/notes/{note_id}/attachments/{att_id}",
        **clinic.header(subject),
    )


def test_a_draft_accepts_attachments_and_signed_refuses_new_ones(clinic, settings):
    settings.ATTACHMENT_SCANNER = "pretend"
    note = draft(clinic)
    # Upload PDF
    r1 = upload_att(clinic, note, _pdf(), "referral letter.pdf")
    assert r1.status_code == 201, r1.content
    a1 = body(r1)["attachment"]
    assert a1["scan"] == "clean" and a1["filename"] == "referral letter.pdf"
    assert a1["content_type"] == "application/pdf"
    # Upload PNG
    r2 = upload_att(clinic, note, _PNG, "ecg_trace.png", content_type="image/png")
    assert r2.status_code == 201, r2.content
    a2 = body(r2)["attachment"]
    assert a2["scan"] == "clean" and a2["content_type"] == "image/png"
    # Note reading shows attachments metadata
    got = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert got["has_attachments"] is True
    assert len(got["attachments"]) == 2
    # Download attachment
    downloaded = get_att(clinic, note, a1["id"])
    assert downloaded.status_code == 200
    assert downloaded["Content-Type"] == "application/pdf"
    assert downloaded.content == _pdf()
    # Delete PNG attachment
    del_res = delete_att(clinic, note, a2["id"])
    assert del_res.status_code == 200
    got_after_del = body(clinic.get(f"clinician/notes/{note}", subject="doc"))["note"]
    assert len(got_after_del["attachments"]) == 1
    # Sign note
    step(clinic, note, "sign")
    # Refuse attach to signed note
    assert upload_att(clinic, note, _pdf(), "late.pdf").status_code == 409
    # Refuse delete from signed note
    assert delete_att(clinic, note, a1["id"]).status_code == 409
    # Amend note -> attachment carried forward
    new_note = body(step(clinic, note, "amend", reason="clarification"))["note"]["id"]
    new_got = body(clinic.get(f"clinician/notes/{new_note}", subject="doc"))["note"]
    assert len(new_got["attachments"]) == 1
    assert new_got["attachments"][0]["filename"] == "referral letter.pdf"
    # Embargo: patient cannot download before 24h
    assert get_pat_att(clinic, note, a1["id"]).status_code == 404
    backdate(clinic, note, 25)
    pat_res = get_pat_att(clinic, note, a1["id"])
    assert pat_res.status_code == 200
    assert pat_res.content == _pdf()
    assert pat_res["Content-Type"] == "application/pdf"


def test_attachment_validation_virus_scan_and_retention(clinic, settings):
    from foundation.scanner import EICAR
    from modules.clinicalnote.jobs import erase_expired_all_tenants, scan_attachments_all_tenants
    from modules.clinicalnote.models import NoteAttachment

    settings.ATTACHMENT_SCANNER = "pretend"
    note = draft(clinic)
    # Refuse text file
    assert upload_att(clinic, note, b"not a valid pdf or image", "doc.txt").status_code == 422
    # Refuse broken PDF
    assert upload_att(clinic, note, b"%PDF-broken", "broken.pdf").status_code == 422
    # Refuse PDF with active JS
    assert upload_att(clinic, note, _pdf(b"/JavaScript (malicious())"), "x.pdf").status_code == 422
    # Refuse > 10MB
    assert upload_att(clinic, note, b"%PDF-" + b"0" * (10 * 1024 * 1024 + 1) + b"%%EOF", "huge.pdf").status_code == 422
    # Virus check: EICAR standard test file is detected as infected
    infected_res = upload_att(clinic, note, _pdf(EICAR), "eicar.pdf")
    assert infected_res.status_code == 201
    infected_id = body(infected_res)["attachment"]["id"]
    assert body(infected_res)["attachment"]["scan"] == "infected"
    # Cannot open infected attachment
    assert get_att(clinic, note, infected_id).status_code == 409
    with connection.cursor() as cur:
        cur.execute("select content_enc from clinicalnote_noteattachment where public_id = %s", [infected_id])
        assert cur.fetchone()[0] == ""  # wiped on infection
    # Pending scan worker test
    settings.ATTACHMENT_SCANNER = ""  # no scanner available: stays pending
    pending_res = upload_att(clinic, note, _pdf(), "pending.pdf")
    pending_id = body(pending_res)["attachment"]["id"]
    assert body(pending_res)["attachment"]["scan"] == "pending"
    assert get_att(clinic, note, pending_id).status_code == 409  # cannot open while pending
    settings.ATTACHMENT_SCANNER = "pretend"
    assert scan_attachments_all_tenants() >= 1
    assert get_att(clinic, note, pending_id).status_code == 200  # now clean and openable
    # Encryption at rest: ciphertext in DB
    with connection.cursor() as cur:
        cur.execute("select content_enc from clinicalnote_noteattachment where public_id = %s", [pending_id])
        stored = cur.fetchone()[0]
    assert stored and "Type /Catalog" not in stored and "AAAAAB+Arial" not in stored
    # Max attachments: 5 per note
    # Currently have 2 (infected, pending). Add 3 more:
    for i in range(3):
        assert upload_att(clinic, note, _pdf(), f"file_{i}.pdf").status_code == 201
    # 6th is refused with 409
    assert upload_att(clinic, note, _pdf(), "file_extra.pdf").status_code == 409
    # Retention test: a separate patient with no recent care
    _, old_person = clinic.patient("pat_old")
    treat(clinic, clinic.doc)
    with tenant_transaction(clinic.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=clinic.doc,
            person_public_id=old_person,
            basis=trust.CareBasis.CARE_TEAM,
            organization_ref=clinic.clinic.org,
            by="system",
        )
    _born(clinic, old_person, "1960-01-01", "Pat Old")
    old_res = clinic.post("clinician/notes", {"patient": old_person, "note_date": "2010-01-05", "body": TEXT}, subject="doc")
    old_note = body(old_res)["note"]["id"]
    att = body(upload_att(clinic, old_note, _pdf(), "old.pdf"))["attachment"]
    step(clinic, old_note, "sign")
    assert erase_expired_all_tenants() >= 1
    with tenant_transaction(clinic.clinic.ctx):
        row = NoteAttachment.objects.get(public_id=att["id"])
        assert row.content_enc == ""



# ------------------------------------------------ an adult's attorney reads their notes (DEV-353)
def _attorney_of(clinic, rights=("appointments", "notes"), relationship="lpa_attorney"):
    from modules.trust.models import ProxyGrant

    adult = _child_of_guardian(clinic, _years_ago(84), rights=rights)  # "child" helper, an adult
    with tenant_transaction(clinic.clinic.ctx):
        ProxyGrant.objects.filter(person__public_id=adult).update(relationship_type=relationship)
    return adult


def test_an_attorney_with_the_notes_part_reads_the_adults_notes(clinic):
    _attorney_of(clinic)
    note = signed(clinic)
    backdate(clinic, note, 25)
    assert [n["id"] for n in body(mine(clinic, G))["notes"]] == [note]
    assert clinic.get(f"my/notes/{note}", subject=G).status_code == 200
    step(clinic, note, "guardian", hidden=True)  # kept from guardians: from attorneys too
    assert body(mine(clinic, G))["notes"] == []


def test_without_the_notes_part_or_as_a_mere_carer_an_adults_notes_stay_closed(clinic):
    _attorney_of(clinic, rights=("appointments",), relationship="court_deputy")
    note = signed(clinic)
    backdate(clinic, note, 25)
    assert body(mine(clinic, G))["notes"] == []
    from modules.trust.models import ProxyGrant

    with tenant_transaction(clinic.clinic.ctx):  # a carer of an adult, even with "notes"
        ProxyGrant.objects.update(relationship_type="caregiver", rights=["notes"])
    assert body(mine(clinic, G))["notes"] == []
