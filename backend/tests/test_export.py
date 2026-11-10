import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.scheduling.models import Booking, Slot
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import SECRET, reply, started

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

SECTIONS = {"appointments", "referrals", "care_team", "access_log", "profiles", "family",
            "insurance", "waiting_list", "notifications", "conversations", "appointment_history"}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def export(p, subject="pat"):
    r = p.get("my/export", subject=subject)
    assert r.status_code == 200, r.content
    return r, json.loads(r.content)


def test_the_export_is_a_json_download_with_every_section(p):
    r, data = export(p)
    assert r["Content-Disposition"] == 'attachment; filename="my-zoiko-data.json"'
    assert data["format"] == "zoiko-access-export-1" and data["exported_at"]
    assert SECTIONS <= set(data)


def test_it_holds_the_patients_own_appointments_and_messages_with_their_text(p):
    booking = booked(p, confirm=True)
    thread = started(p)
    reply(p, thread, "Please come on Thursday")
    _, data = export(p)
    assert [b["id"] for b in data["appointments"]] == [booking]
    (conversation,) = data["conversations"]
    assert [m["body"] for m in conversation["messages"]] == [SECRET, "Please come on Thursday"]
    assert [m["side"] for m in conversation["messages"]] == ["patient", "staff"]


def test_exporting_does_not_mark_messages_as_read(p):
    thread = started(p)
    reply(p, thread, "Your results are in")
    before = json.loads(p.get("messages/threads", subject="pat").content)["threads"][0]["unread"]
    export(p)
    after = json.loads(p.get("messages/threads", subject="pat").content)["threads"][0]["unread"]
    assert before == 1 and after == 1


def test_it_holds_no_one_elses_data(p):
    booked(p, confirm=True)
    started(p)
    other = Practice()
    booked(other, confirm=True)
    started(other, subject="Other subject")
    _, mine = export(p)
    _, theirs = export(other)
    assert len(mine["appointments"]) == 1 and len(mine["conversations"]) == 1
    assert mine["appointments"][0]["id"] != theirs["appointments"][0]["id"]
    assert "Other subject" not in json.dumps(mine)


def test_another_patient_of_the_same_clinic_gets_only_their_own(p):
    booked(p, confirm=True)
    started(p)
    p.patient("second")
    _, data = export(p, "second")
    assert data["appointments"] == [] and data["conversations"] == []


def test_staff_and_doctors_cannot_use_it_so_no_clinic_records_leak(p):
    booked(p, confirm=True)
    for who in ("ops", "doc"):
        assert p.get("my/export", subject=who).status_code == 403


def test_the_export_is_written_to_the_audit_trail(p):
    export(p)
    with tenant_transaction(p.clinic.ctx):
        actions = list(AuditEvent.objects.filter(actor_ref=p.patient_actor).values_list(
            "action", flat=True))
    assert "data.export" in actions


def test_it_needs_a_login(p):
    assert p.client.get("/api/v1/my/export").status_code == 401


# ------------------------------------------------------------------ the steps of each appointment
def steps_of(data, booking):
    (entry,) = [h for h in data["appointment_history"] if h["appointment"] == booking]
    return entry


def test_it_holds_every_appointments_steps_worded_as_the_app_shows_them(p):
    booking = booked(p, confirm=True)
    p.post(f"bookings/{booking}/cancel", {"reason": "schedule_conflict"}, subject="pat")  # asks
    p.post(f"bookings/{booking}/cancel", subject="ops")                      # the clinic answers
    _, data = export(p)
    entry = steps_of(data, booking)
    assert [(e["event"], e["text"]) for e in entry["events"]] == [
        ("requested", "Requested"), ("confirmed", "Confirmed"),
        ("cancel_requested", "Cancellation asked for: waiting for the clinic"),
        ("cancelled", "Cancelled")]
    assert [e["who"] for e in entry["events"]] == [
        "You or your family", "The clinic", "You or your family", "The clinic"]
    assert entry["events"][-1]["reason"] == "schedule_conflict" and entry["chain_ok"] is True
    assert [e["seq"] for e in entry["events"]] == [1, 2, 3, 4]


def test_the_export_holds_the_same_steps_as_the_history_button(p):
    booking = booked(p, confirm=True)
    _, data = export(p)
    shown = json.loads(p.get(f"bookings/{booking}/history", subject="pat").content)
    assert steps_of(data, booking) == {"appointment": booking, **shown}


def test_there_is_one_entry_per_appointment_in_the_same_order(p):
    first = booked(p, confirm=True)
    second = booked(p, confirm=False)
    _, data = export(p)
    listed = [b["id"] for b in data["appointments"]]
    assert {first, second} == set(listed) and len(listed) == 2
    assert [h["appointment"] for h in data["appointment_history"]] == listed


def test_no_appointments_means_no_histories(p):
    _, data = export(p)
    assert data["appointments"] == [] and data["appointment_history"] == []


def test_an_appointment_made_before_steps_were_recorded_has_an_empty_list(p):
    booked(p, confirm=False)                                   # makes sure there are free times
    slot_ref = p.slot_of("doc")
    with tenant_transaction(p.clinic.ctx):
        old = Booking.objects.create(
            slot=Slot.objects.get(public_id=slot_ref), person_ref=p.person, booked_by_ref=p.doc)
    _, data = export(p)
    entry = steps_of(data, old.public_id)
    assert entry["events"] == [] and entry["chain_ok"] is True


def test_a_moved_appointment_exports_both_bookings_steps(p):
    old = booked(p, confirm=True)
    target = p.slot_of("doc", index=1)
    moved = p.post(f"bookings/{old}/reschedule", {"slot": target}, subject="pat")
    new = json.loads(moved.content)["booking"]["id"]
    _, data = export(p)                          # the original stays until the clinic answers
    assert [e["event"] for e in steps_of(data, old)["events"]][-1] == "change_requested"
    assert [e["event"] for e in steps_of(data, new)["events"]] == ["requested"]
    p.post(f"bookings/{new}/confirm", subject="ops")
    _, data = export(p)
    assert [e["event"] for e in steps_of(data, old)["events"]][-1] == "moved_away"
    assert [e["event"] for e in steps_of(data, new)["events"]][-1] == "moved_here"


def test_the_steps_never_show_which_staff_member_acted_or_anyone_elses_data(p):
    booking = booked(p, confirm=True)
    other = Practice()
    other_booking = booked(other, confirm=True)
    _, mine = export(p)
    text = json.dumps(mine["appointment_history"])
    assert p.desk not in text and p.doc not in text and "actor" not in text
    assert other_booking not in text and booking in text
    assert len(mine["appointment_history"]) == 1


def test_another_patient_of_the_same_clinic_gets_no_one_elses_steps(p):
    booked(p, confirm=True)
    p.patient("second")
    _, data = export(p, "second")
    assert data["appointment_history"] == []


def test_an_appointment_whose_steps_cannot_be_read_is_marked_unavailable_not_hidden(p, monkeypatch):
    from api import views
    from foundation.errors import ErrorCode, ZoikoError
    booking = booked(p, confirm=True)

    def refuse(auth, booking_id):
        raise ZoikoError(ErrorCode.POLICY_DENIED)

    monkeypatch.setattr(views, "_history_payload", refuse)
    _, data = export(p)
    assert data["appointment_history"] == [{"appointment": booking, "available": False}]
    assert [b["id"] for b in data["appointments"]] == [booking]     # the rest still exports


def test_when_the_appointments_section_is_unavailable_so_is_their_history(p, monkeypatch):
    from api import views
    booked(p, confirm=True)
    real = views._section

    def section(view, request, auth, key):
        return {"available": False} if key == "bookings" else real(view, request, auth, key)

    monkeypatch.setattr(views, "_section", section)
    _, data = export(p)
    assert data["appointments"] == {"available": False}
    assert data["appointment_history"] == {"available": False}
