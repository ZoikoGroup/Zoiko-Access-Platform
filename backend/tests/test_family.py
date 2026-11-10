import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def ask(p, subject="pat", **over):
    body = {"relationship": "parent", "name": "Ravi Rao", "birth_date": "2018-05-02", **over}
    return p.post("family", body, subject=subject)


def family(p, subject="pat"):
    r = p.get("family", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)["family"]


def queue(p, subject="ops"):
    r = p.get("staff/family-requests", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["requests"]


ALL_SCOPES = ["appointments", "messages", "referrals", "insurance"]


def decide(p, grant, verb, subject="ops", **body):
    if verb == "approve" and "scopes" not in body:
        body["scopes"] = ALL_SCOPES            # the staff screen always chooses; default: all four
    return p.post(f"staff/family-requests/{grant}/{verb}", body, subject=subject)


def requested(p, **over):
    r = ask(p, **over)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["request"]


def approved(p, **over):
    made = requested(p, **over)
    assert decide(p, made["id"], "approve", verified=True).status_code == 200
    return made


def record_status(p, person):
    """The status of a patient record, read straight from the database."""
    from modules.trust.models import Person
    with tenant_transaction(p.clinic.ctx):
        return Person.objects.get(public_id=person).status


def my_profiles(p, subject="pat"):
    return json.loads(p.get("profile", subject=subject).content)["profiles"]


def booking_for(p, person, subject="pat"):
    if not p.open_slots():
        p.add_times(p.doc)
    slot = p.open_slots()[0]["id"]
    key = f"family-{person}-{slot}".ljust(24, "x")
    return p.post("bookings", {"slot": slot, "person": person}, subject=subject, key=key)


# ------------------------------------------------------------------ step 1: asking
def test_a_request_creates_a_waiting_entry_and_gives_no_access_yet(p):
    made = requested(p)
    assert made["status"] == "proposed" and made["name"] == "Ravi Rao"
    assert made["birth_date"] == "2018-05-02" and made["relationship"] == "parent"
    waiting = family(p)
    assert [f["id"] for f in waiting] == [made["id"]] and waiting[0]["status"] == "proposed"
    assert [x["person"] for x in my_profiles(p)] == [p.person]          # only my own record
    assert booking_for(p, made["person"]).status_code == 403


def test_the_child_details_are_stored_encrypted(p):
    requested(p)
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("select legal_name_enc, birth_date_enc from trust_personprofile")
        cells = [c for row in cur.fetchall() for c in row if c]
    assert cells and all(c.startswith("zf1:") for c in cells)
    assert "Ravi" not in " ".join(cells) and "2018" not in " ".join(cells)


@pytest.mark.parametrize("fields", [
    {"relationship": "uncle"}, {"relationship": ""}, {"name": ""}, {"name": "x" * 121},
    {"birth_date": ""}, {"birth_date": "2999-01-01"}, {"birth_date": "05/02/2018"},
])
def test_bad_requests_are_refused_and_nothing_is_created(p, fields):
    assert ask(p, **fields).status_code == 422
    assert family(p) == []


def test_only_a_patient_with_their_own_record_can_ask(p):
    assert ask(p, subject="doc").status_code == 403
    assert ask(p, subject="ops").status_code == 403


def test_there_is_a_limit_on_waiting_requests(p):
    for i in range(5):
        assert ask(p, name=f"Child {i}").status_code == 201
    assert ask(p, name="One too many").status_code == 409


# ------------------------------------------------------------------ step 2: the clinic decides
def test_staff_see_the_waiting_request_with_who_asked_and_for_whom(p):
    p.post(f"people/{p.person}/profile", {"name": "Asha Rao"}, subject="pat")
    made = requested(p)
    rows = queue(p)
    assert [r["id"] for r in rows] == [made["id"]]
    assert rows[0]["name"] == "Ravi Rao" and rows[0]["relationship"] == "parent"
    assert rows[0]["requested_by"].startswith("Patient ")      # a patient login is masked
    assert rows[0]["requested_by_name"] == "Asha Rao"


def test_approval_needs_the_staff_member_to_confirm_they_checked_proof(p):
    made = requested(p)
    for body in ({}, {"verified": False}, {"verified": "yes"}, {"verified": 1}):
        assert decide(p, made["id"], "approve", **body).status_code == 422, body
    assert family(p)[0]["status"] == "proposed"


def test_after_approval_the_guardian_can_see_and_book_but_not_edit(p):
    made = approved(p)
    assert family(p)[0]["status"] == "active"
    shown = {x["person"]: x for x in my_profiles(p)}
    assert shown[made["person"]]["name"] == "Ravi Rao"
    assert shown[made["person"]]["editable"] is False
    assert p.post(f"people/{made['person']}/profile", {"name": "Hacked"}, subject="pat"
                  ).status_code == 403                                   # read-only through a proxy
    assert booking_for(p, made["person"]).status_code == 201


def test_approval_gives_the_guardian_role_and_the_waiting_list_empties(p):
    approved(p)
    roles = [r["role"] for r in json.loads(p.get("me", subject="pat").content)["roles"]]
    assert "R02" in roles and "R01" in roles
    assert queue(p) == []


def test_approving_for_a_second_child_keeps_working(p):
    first, second = approved(p), approved(p, name="Meera Rao", birth_date="2020-01-01")
    assert {x["person"] for x in my_profiles(p)} == {p.person, first["person"], second["person"]}


def test_a_past_end_date_is_refused_and_a_future_one_is_recorded(p):
    made = requested(p)
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    assert decide(p, made["id"], "approve", verified=True, valid_until=yesterday).status_code == 422
    assert decide(p, made["id"], "approve", verified=True, valid_until="soon").status_code == 422
    tomorrow = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    r = decide(p, made["id"], "approve", verified=True, valid_until=tomorrow)
    assert r.status_code == 200
    assert json.loads(r.content)["request"]["valid_until"].startswith(tomorrow)


def test_a_waiting_request_can_be_approved_only_once(p):
    made = requested(p)
    assert decide(p, made["id"], "approve", verified=True).status_code == 200
    assert decide(p, made["id"], "approve", verified=True).status_code == 409


def test_rejecting_closes_the_request_and_the_empty_record(p):
    made = requested(p)
    assert decide(p, made["id"], "reject").status_code == 200
    assert family(p) == [] and queue(p) == []
    # The closed record cannot be reached at all now: 403, not "already decided".
    assert decide(p, made["id"], "reject").status_code == 403
    assert decide(p, made["id"], "approve", verified=True).status_code == 403
    assert booking_for(p, made["person"]).status_code == 403
    assert record_status(p, made["person"]) == "ended"


# ------------------------------------------------------------------ withdrawing and ending
def test_a_waiting_request_can_be_withdrawn_by_the_person_who_made_it(p):
    made = requested(p)
    assert p.post(f"family/{made['id']}/withdraw", subject="pat").status_code == 200
    assert p.post(f"family/{made['id']}/withdraw", subject="pat").status_code == 200   # harmless
    assert family(p) == [] and queue(p) == []
    assert record_status(p, made["person"]) == "ended"          # the empty record is closed


def test_ending_an_approved_link_removes_the_access_again(p):
    made = approved(p)
    assert booking_for(p, made["person"]).status_code == 201
    assert p.post(f"family/{made['id']}/withdraw", subject="pat").status_code == 200
    assert family(p) == []
    assert booking_for(p, made["person"]).status_code == 403
    assert all(x["person"] != made["person"] for x in my_profiles(p))
    assert record_status(p, made["person"]) == "active"         # a real, approved record stays


def test_nobody_can_withdraw_someone_elses_request(p):
    made = requested(p)
    p.patient("pat2")
    assert p.post(f"family/{made['id']}/withdraw", subject="pat2").status_code == 404
    assert family(p)[0]["status"] == "proposed"


# ------------------------------------------------------------------ who may decide
def test_the_person_who_asked_cannot_approve_their_own_request(p):
    made = requested(p)
    assert decide(p, made["id"], "approve", subject="pat", verified=True).status_code == 403
    assert decide(p, made["id"], "reject", subject="pat").status_code == 403
    assert family(p)[0]["status"] == "proposed"


def test_doctors_and_other_patients_cannot_see_or_decide(p):
    made = requested(p)
    p.patient("pat2")
    for who in ("doc", "pat2"):
        assert p.get("staff/family-requests", subject=who).status_code == 403
        assert decide(p, made["id"], "approve", subject=who, verified=True).status_code == 403


def test_another_customers_staff_cannot_touch_it(p):
    made = requested(p)
    outsider = Web()
    outsider.staff("desk")
    assert queue(outsider, "desk") == []
    r = outsider.post(f"staff/family-requests/{made['id']}/approve", {"verified": True},
                      subject="desk")
    assert r.status_code == 404
    assert family(p)[0]["status"] == "proposed"


def test_one_guardian_cannot_see_another_guardians_family(p):
    approved(p)
    _, other = p.patient("pat2")
    assert family(p, "pat2") == []
    assert [x["person"] for x in my_profiles(p, "pat2")] == [other]


# ------------------------------------------------------------------ the record
def test_the_audit_trail_names_who_did_what_but_never_the_childs_details(p):
    made = approved(p)
    with tenant_transaction(p.clinic.ctx):
        events = list(AuditEvent.objects.filter(
            action__startswith="family.").order_by("chain_seq"))
    assert [e.action for e in events] == ["family.request", "family.approve"]
    assert events[0].actor_ref == p.patient_actor and events[1].actor_ref == p.desk
    text = json.dumps([str(e.details) for e in events])
    assert "Ravi" not in text and "2018" not in text and made["id"]


# ------------------------------------------------------------------ the requester is told
def deliver():
    from foundation import dispatch
    from foundation.outbox import relay_once
    for _ in range(4):
        relay_once(dispatch.dispatch)


def family_notices(p, subject="pat"):
    from modules.comms import api as comms
    comms.register_subscribers()
    deliver()
    rows = json.loads(p.get("notifications", subject=subject).content)["notifications"]
    return [n for n in rows if n["kind"].startswith("family_")]


def test_the_patient_is_told_when_the_clinic_approves(p):
    made = requested(p)
    assert family_notices(p) == []                       # nothing until the clinic decides
    decide(p, made["id"], "approve", verified=True)
    notices = family_notices(p)
    assert [n["title"] for n in notices] == ["Family request approved"]
    assert "Ravi" not in json.dumps(notices)             # the message names nobody


def test_the_patient_is_told_when_the_clinic_declines(p):
    made = requested(p)
    decide(p, made["id"], "reject")
    assert [n["title"] for n in family_notices(p)] == ["Family request not approved"]


def test_withdrawing_your_own_request_does_not_notify_anyone(p):
    made = requested(p)
    p.post(f"family/{made['id']}/withdraw", subject="pat")
    assert family_notices(p) == []


def test_the_event_carries_no_name_or_birth_date(p):
    from foundation.models import OutboxEvent
    made = requested(p)
    decide(p, made["id"], "approve", verified=True)
    with tenant_transaction(p.clinic.ctx):
        payloads = [json.dumps(e.payload) for e in OutboxEvent.objects.filter(
            event_type="family.decided.v1")]
    assert payloads and not any("Ravi" in x or "2018" in x for x in payloads)


def test_only_the_requester_gets_the_notice(p):
    made = requested(p)
    p.patient("pat2")
    decide(p, made["id"], "approve", verified=True)
    assert len(family_notices(p)) == 1 and family_notices(p, "pat2") == []
