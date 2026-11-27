"""Cross-state deny tests: every state machine refuses every step it does not allow.

Each machine below is described by a table: for each state, the steps that are ALLOWED there. The
tests derive every (state, step) pair that is NOT in the table, put a fresh object into that state,
try the step, and check two things: it was refused (or, for the background jobs, did nothing), and
the object is exactly as it was before (same state, same version number). A guard test per machine
checks the table still names every state the model has and, where the API lists them, every step,
so a step added later must be classified here before the suite passes again.
"""
import datetime as dt
import json
from typing import Any

import pytest

from api import urls as api_urls
from api.views import _PACK_STEPS
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import utc_now
from modules.jurisdiction.models import CountryPack, PackApproval, PackStatus
from modules.referral.models import Referral, ReferralState
from modules.referral.services import DECISIONS
from modules.rights.models import CaseState, RightsCase
from modules.scheduling.models import Booking, BookingStatus, Slot
from modules.waitlist import services as waitlist
from modules.waitlist.models import WaitEntry, WaitState
from modules.workflow import services as work
from modules.workflow.models import Kind, Status, WorkItem
from tests.test_api import Web, book
from tests.test_clinician import Practice
from tests.test_packs import full, make, step
from tests.test_platform import Platform
from tests.test_referral import Clinic2
from tests.test_waitlist import Waiting

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

NOTE = "Legal duty to keep records"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def forbidden(allowed: dict[str, set[str]], actions: tuple[str, ...]) -> list[tuple[str, str]]:
    """Every (state, step) pair the table does not allow, in a stable order."""
    return [(state, action) for state in allowed for action in actions
            if action not in allowed[state]]


def ids(pairs: list[tuple[str, str]]) -> list[str]:
    return [f"{state}-{action}" for state, action in pairs]


def routes(prefix: str) -> set[str]:
    """The last part of every API route that starts with this prefix (the steps it offers)."""
    out = set()
    for entry in api_urls.urlpatterns:
        route = str(entry.pattern)
        if route.startswith(prefix) and route.count("/") == prefix.count("/"):
            out.add(route.removeprefix(prefix))
    return out


def check_table(allowed: dict[str, set[str]], actions: tuple[str, ...]) -> None:
    """The table only names known steps, and something is forbidden somewhere."""
    for state, steps in allowed.items():
        assert steps <= set(actions), (state, steps - set(actions))
    assert forbidden(allowed, actions)


# ====================================================================== 1. country packs
PACK_ACTIONS = tuple(_PACK_STEPS)
PACK_ALLOWED: dict[str, set[str]] = {
    "draft": {"edit", "validate"},
    "validated": {"submit", "reject"},
    "expert_review": {"reject", "approve-legal", "approve-clinical"},
    "signed": {"suspend", "retire"},
    "suspended": {"suspend", "retire", "revalidate"},     # suspending again is harmless
    "retired": {"retire"},                                # retiring again is harmless
}
PACK_PAIRS = forbidden(PACK_ALLOWED, PACK_ACTIONS)
# Who takes each step, and what they send, so a refusal is about the state and nothing else.
PACK_TRY: dict[str, tuple[str, dict[str, Any]]] = {
    "edit": ("sam", {"manifest": full()}), "validate": ("sam", {}), "submit": ("sam", {}),
    "reject": ("iyer", {"note": NOTE}), "approve-legal": ("tess", {}),
    "approve-clinical": ("iyer", {}), "suspend": ("tess", {"note": NOTE}),
    "retire": ("sam", {}), "revalidate": ("sam", {}),
}
PACK_PATH = {
    "validated": ["validate"], "expert_review": ["validate", "submit"],
    "signed": ["validate", "submit", "approve-legal", "approve-clinical"],
    "suspended": ["validate", "submit", "approve-legal", "approve-clinical", "suspend"],
    "retired": ["validate", "submit", "approve-legal", "approve-clinical", "retire"],
}


@pytest.fixture
def platform():
    w = Platform()
    w.staff("sam", "R11")          # author and support
    w.staff("tess", "R11")         # a second support officer
    w.staff("iyer", "R12")         # a safety officer
    return w


def pack_in(w, state):
    pack = make(w)
    for name in PACK_PATH.get(state, []):
        who, body = PACK_TRY[name]
        assert step(w, pack, name, who, **body).status_code == 200, (state, name)
    return pack


def pack_snapshot(w):
    with tenant_transaction(w.ctx):
        rows = list(CountryPack.objects.order_by("revision").values_list(
            "revision", "status", "entity_version", "content_hash"))
        return rows, PackApproval.objects.count()


def test_the_pack_table_covers_every_state_and_step():
    assert set(PACK_ALLOWED) == set(PackStatus.values)
    check_table(PACK_ALLOWED, PACK_ACTIONS)


@pytest.mark.parametrize(("state", "action"), PACK_PAIRS, ids=ids(PACK_PAIRS))
def test_a_pack_refuses_a_step_its_state_does_not_allow(platform, state, action):
    pack = pack_in(platform, state)
    before = pack_snapshot(platform)
    assert [row[1] for row in before[0]] == [state]
    who, body = PACK_TRY[action]
    r = step(platform, pack, action, who, **body)
    assert r.status_code == 409, r.content
    assert pack_snapshot(platform) == before


# ====================================================================== 2. bookings
# The clinic's own steps (desk staff). "started" is a confirmed appointment whose time has come.
# "keep" (DEV-390) takes back a patient's cancel request: none of these states has one waiting.
BOOKING_ACTIONS = ("confirm", "cancel", "reschedule", "attendance", "ask-provider", "keep")
BOOKING_ALLOWED: dict[str, set[str]] = {
    "requested": {"confirm", "cancel", "reschedule", "ask-provider"},
    "confirmed": {"confirm", "cancel", "reschedule"},     # confirming again is harmless
    "requested_past": {"confirm", "cancel", "ask-provider"},
    "started": {"confirm", "cancel", "attendance"},
    "attended": {"confirm", "attendance"},     # DEV-215: a visit on record cannot be cancelled
    "cancelled": {"cancel"},                              # cancelling again is harmless
}
BOOKING_STATUS = {"requested": "requested", "confirmed": "confirmed", "requested_past": "requested",
                  "started": "confirmed", "attended": "confirmed", "cancelled": "cancelled"}
BOOKING_PAIRS = forbidden(BOOKING_ALLOWED, BOOKING_ACTIONS)


@pytest.fixture
def clinic():
    w = Web()
    w.patient("pat")
    w.staff("desk")
    return w


def booking_in(w, state):
    r = book(w, w.slots[0]["public_id"], subject="pat")
    assert r.status_code == 201, r.content
    booking = json.loads(r.content)["booking"]["id"]
    if state in ("confirmed", "started", "attended", "cancelled"):
        assert w.post(f"bookings/{booking}/confirm", subject="desk").status_code == 200
    if state == "cancelled":
        assert w.post(f"bookings/{booking}/cancel", subject="desk").status_code == 200
    if state in ("started", "attended", "requested_past"):
        with tenant_transaction(w.clinic.ctx):
            Slot.objects.filter(public_id=w.slots[0]["public_id"]).update(
                starts_at="2020-01-01T10:00:00Z", ends_at="2020-01-01T10:30:00Z")
    if state == "attended":
        r = w.post(f"bookings/{booking}/attendance", {"outcome": "attended"}, subject="desk")
        assert r.status_code == 200, r.content
    return booking


def booking_try(w, booking, action):
    body = {"reschedule": {"slot": w.slots[2]["public_id"]},
            "attendance": {"outcome": "attended"}}.get(action, {})
    return w.post(f"bookings/{booking}/{action}", body, subject="desk")


def booking_snapshot(w):
    with tenant_transaction(w.clinic.ctx):
        return list(Booking.objects.order_by("created_at").values_list(
            "public_id", "status", "attendance", "pending_change", "entity_version", "slot_id"))


def test_the_booking_table_covers_every_state_and_step():
    assert set(BOOKING_STATUS.values()) == set(BookingStatus.values)
    assert set(BOOKING_STATUS) == set(BOOKING_ALLOWED)
    read_only = {"history", "calendar", "provider-stage"}
    assert routes("bookings/<str:booking_id>/") - read_only == set(BOOKING_ACTIONS)
    check_table(BOOKING_ALLOWED, BOOKING_ACTIONS)


@pytest.mark.parametrize(("state", "action"), BOOKING_PAIRS, ids=ids(BOOKING_PAIRS))
def test_a_booking_refuses_a_step_its_state_does_not_allow(clinic, state, action):
    booking = booking_in(clinic, state)
    before = booking_snapshot(clinic)
    assert [row[1] for row in before] == [BOOKING_STATUS[state]]
    r = booking_try(clinic, booking, action)
    assert r.status_code == 409, r.content
    assert booking_snapshot(clinic) == before


# ====================================================================== 3. referrals
REFERRAL_ACTIONS = (*DECISIONS, "acknowledge")
REFERRAL_ALLOWED: dict[str, set[str]] = {
    "issued": set(REFERRAL_ACTIONS),
    "needs_info": set(REFERRAL_ACTIONS),      # still open: it may be decided once more is known
    "accepted": set(),
    "declined": set(),
}
REFERRAL_PAIRS = forbidden(REFERRAL_ALLOWED, REFERRAL_ACTIONS)
REASON = "Please attach the ECG"


@pytest.fixture
def referring():
    return Clinic2()


def referral_try(c, referral, action):
    if action == "acknowledge":
        return c.post(f"referrals/{referral}/acknowledge", {}, subject="ops")
    return c.decide(referral, action, "" if action == "accepted" else REASON)


def referral_in(c, state):
    r = c.refer()
    assert r.status_code == 201, r.content
    referral = json.loads(r.content)["referral"]["id"]
    if state != "issued":
        assert referral_try(c, referral, state).status_code == 200
    return referral


def referral_snapshot(c):
    with tenant_transaction(c.clinic.ctx):
        return list(Referral.objects.values_list(
            "public_id", "state", "entity_version", "acknowledged_at", "decided_at"))


def test_the_referral_table_covers_every_state_and_step():
    assert set(REFERRAL_ALLOWED) == set(ReferralState.values)
    assert routes("referrals/<str:referral_id>/") == {"decide", "acknowledge"}
    check_table(REFERRAL_ALLOWED, REFERRAL_ACTIONS)


@pytest.mark.parametrize(("state", "action"), REFERRAL_PAIRS, ids=ids(REFERRAL_PAIRS))
def test_a_referral_refuses_a_step_its_state_does_not_allow(referring, state, action):
    referral = referral_in(referring, state)
    before = referral_snapshot(referring)
    assert [row[1] for row in before] == [state]
    r = referral_try(referring, referral, action)
    # 409 when the decider still has access; 403 once the decision ended the access it gave.
    assert r.status_code in (403, 409), r.content
    assert referral_snapshot(referring) == before


# ====================================================================== 4. waiting-list entries
# Leaving is the patient's step; offering, booking and expiring are the background jobs, which
# work only on entries in the right state and must leave every other entry alone.
WAIT_ACTIONS = ("leave", "offer", "book", "expire")
WAIT_ALLOWED: dict[str, set[str]] = {
    "waiting": {"leave", "offer", "book"},
    "offered": {"leave", "book", "expire"},
    "closed": {"leave"},                                  # leaving again is harmless
}
WAIT_PAIRS = forbidden(WAIT_ALLOWED, WAIT_ACTIONS)


@pytest.fixture
def waiting():
    return Waiting()


def wait_in(w, state):
    r = w.join()
    assert r.status_code == 201, r.content
    entry = json.loads(r.content)["entry"]["id"]
    if state == "offered":
        w.add_times()
        with tenant_transaction(w.clinic.ctx):
            assert waitlist.offer_open_times(w.cardio) == 1
    if state == "closed":
        assert w.post(f"waitlist/{entry}/leave", subject="pat").status_code == 200
    return entry


def wait_try(w, action) -> int:
    """Run a background job; it returns how many entries it changed."""
    if action == "offer":
        w.add_times()                               # a fresh free time, so there is room to offer
    with tenant_transaction(w.clinic.ctx):
        if action == "offer":
            return waitlist.offer_open_times(w.cardio)
        if action == "expire":
            return waitlist.expire_old_offers(older_than=dt.timedelta(0))
        person = WaitEntry.objects.get().person_ref
        return waitlist.close_for_booking(person, w.cardio)


def wait_snapshot(w):
    with tenant_transaction(w.clinic.ctx):
        return list(WaitEntry.objects.values_list(
            "public_id", "state", "closed_reason", "offered_at", "entity_version"))


def test_the_waiting_list_table_covers_every_state_and_step():
    assert set(WAIT_ALLOWED) == set(WaitState.values)
    assert routes("waitlist/<str:entry_id>/") == {"leave"}
    check_table(WAIT_ALLOWED, WAIT_ACTIONS)


@pytest.mark.parametrize(("state", "action"), WAIT_PAIRS, ids=ids(WAIT_PAIRS))
def test_a_waiting_list_entry_ignores_a_job_its_state_does_not_allow(waiting, state, action):
    wait_in(waiting, state)
    before = wait_snapshot(waiting)
    assert [row[1] for row in before] == [state]
    assert wait_try(waiting, action) == 0
    assert wait_snapshot(waiting) == before


# ====================================================================== 5. data-rights cases
# Sub-states matter here: completing needs the identity check, and a lawful hold blocks completing
# an erasure. Every case below is an erasure request, so the hold is meaningful.
RIGHTS_ACTIONS = ("start", "verify", "hold", "release", "complete", "decline", "withdraw")
RIGHTS_ALLOWED: dict[str, set[str]] = {
    "received": {"start", "verify", "hold", "decline", "withdraw"},
    "in_progress": {"start", "verify", "hold", "decline", "withdraw"},
    "verified": {"start", "verify", "hold", "complete", "decline", "withdraw"},
    "on_hold": {"start", "verify", "release", "decline", "withdraw"},
    "completed": set(),
    "declined": set(),
    "withdrawn": {"withdraw"},                            # withdrawing again is harmless
}
RIGHTS_STATUS = {"received": "received", "in_progress": "in_progress", "verified": "in_progress",
                 "on_hold": "in_progress", "completed": "completed", "declined": "declined",
                 "withdrawn": "withdrawn"}
RIGHTS_PATH = {
    "in_progress": ["start"], "verified": ["start", "verify"],
    "on_hold": ["start", "verify", "hold"], "completed": ["start", "verify", "complete"],
    "declined": ["decline"], "withdrawn": ["withdraw"],
}
RIGHTS_PAIRS = forbidden(RIGHTS_ALLOWED, RIGHTS_ACTIONS)


@pytest.fixture
def practice():
    p = Practice()
    p.boss = p.login("boss", "facility_user", "R13")
    return p


def rights_try(p, case, action):
    if action == "withdraw":
        return p.post(f"rights/{case}/withdraw", {}, subject="pat")
    return p.post(f"staff/rights/{case}/{action}", {"note": NOTE}, subject="boss")


def rights_in(p, state):
    r = p.post("rights", {"type": "erasure", "note": "Please delete my data"}, subject="pat")
    assert r.status_code == 201, r.content
    case = json.loads(r.content)["request"]["id"]
    for name in RIGHTS_PATH.get(state, []):
        assert rights_try(p, case, name).status_code == 200, (state, name)
    return case


def rights_snapshot(p):
    with tenant_transaction(p.clinic.ctx):
        return list(RightsCase.objects.values_list(
            "public_id", "state", "lawful_hold", "identity_checked_at", "decided_at",
            "entity_version"))


def test_the_rights_table_covers_every_state_and_step():
    assert set(RIGHTS_STATUS.values()) == set(CaseState.values)
    assert set(RIGHTS_STATUS) == set(RIGHTS_ALLOWED)
    assert routes("rights/<str:case_id>/") == {"withdraw"}
    check_table(RIGHTS_ALLOWED, RIGHTS_ACTIONS)


@pytest.mark.parametrize(("state", "action"), RIGHTS_PAIRS, ids=ids(RIGHTS_PAIRS))
def test_a_rights_case_refuses_a_step_its_state_does_not_allow(practice, state, action):
    case = rights_in(practice, state)
    before = rights_snapshot(practice)
    assert [row[1] for row in before] == [RIGHTS_STATUS[state]]
    r = rights_try(practice, case, action)
    # 409 for a closed case or a hold that is (not) there; 422 for a missing identity check or a
    # hold that blocks erasing.
    assert r.status_code in (409, 422), r.content
    assert rights_snapshot(practice) == before


# ====================================================================== 6. staff work items
# Seen from one member of staff ("me"): claiming and giving back are theirs; closing is done by
# the work queue when the booking, referral or message it is about moves on.
WORK_ACTIONS = ("claim", "release", "close_done", "close_cancelled")
WORK_ALLOWED: dict[str, set[str]] = {
    "open": {"claim", "close_done", "close_cancelled"},
    "mine": {"claim", "release", "close_done", "close_cancelled"},   # claiming again is harmless
    "theirs": {"close_done", "close_cancelled"},
    "done": set(),
    "cancelled": set(),
}
WORK_STATUS = {"open": "open", "mine": "open", "theirs": "open", "done": "done",
               "cancelled": "cancelled"}
WORK_PAIRS = forbidden(WORK_ALLOWED, WORK_ACTIONS)
SUBJECT = "B" * 22


class Desk(Web):
    """A clinic with two members of staff and one piece of open work."""

    def __init__(self):
        super().__init__()
        self.me = self.staff("me")
        self.them = self.staff("them")
        with tenant_transaction(self.clinic.ctx):
            work.open_item(organization_ref=self.clinic.org, kind=Kind.CONFIRM_BOOKING,
                           subject_ref=SUBJECT, due_at=utc_now())
            self.item = WorkItem.objects.get().public_id

    def act(self, action, who=None):
        """Take a step; returns how many items a close changed, or the claimed/released item."""
        with tenant_transaction(self.clinic.ctx):
            if action.startswith("close_"):
                return work.close_item(kind=Kind.CONFIRM_BOOKING, subject_ref=SUBJECT,
                                       status=action.removeprefix("close_"))
            fn = work.claim if action == "claim" else work.release
            return fn(self.item, self.clinic.org, who or self.me)


@pytest.fixture
def desk():
    return Desk()


def work_in(d, state):
    if state == "mine":
        d.act("claim")
    elif state == "theirs":
        d.act("claim", d.them)
    elif state in ("done", "cancelled"):
        assert d.act(f"close_{state}") == 1


def work_snapshot(d):
    with tenant_transaction(d.clinic.ctx):
        return list(WorkItem.objects.values_list(
            "public_id", "status", "assigned_to_ref", "closed_at", "entity_version"))


def test_the_work_table_covers_every_state_and_step():
    assert set(WORK_STATUS.values()) == set(Status.values)
    assert set(WORK_STATUS) == set(WORK_ALLOWED)
    assert routes("staff/work/<str:item_id>/") - {"history"} == {"claim", "release"}
    assert {a.removeprefix("close_") for a in WORK_ACTIONS if a.startswith("close_")} == (
        set(Status.values) - {"open"})
    check_table(WORK_ALLOWED, WORK_ACTIONS)


@pytest.mark.parametrize(("state", "action"), WORK_PAIRS, ids=ids(WORK_PAIRS))
def test_a_work_item_refuses_a_step_its_state_does_not_allow(desk, state, action):
    work_in(desk, state)
    before = work_snapshot(desk)
    assert [row[1] for row in before] == [WORK_STATUS[state]]
    if action.startswith("close_"):
        assert desk.act(action) == 0
    else:
        with pytest.raises(ZoikoError) as refused:
            desk.act(action)
        assert refused.value.code == ErrorCode.CONFLICT_VERSION
    assert work_snapshot(desk) == before
