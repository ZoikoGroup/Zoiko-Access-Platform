import datetime as dt
import json

import pytest

from foundation import dispatch
from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import relay_once
from foundation.timeutil import UTC, FixedClock
from modules.bookingflow import api as bookingflow
from modules.connector import adapters
from modules.connector import api as connector
from modules.connector.models import ExternalOperation
from modules.connector.simulator import simulator
from modules.policy.api import decide
from modules.scheduling import api as scheduling
from modules.scheduling.models import Slot
from modules.workflow import api as workflow
from tests.test_api import Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture(autouse=True)
def partner():
    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    yield simulator
    adapters.clear()


@pytest.fixture(autouse=True)
def only_this_module_listens():
    """Events reach only the booking flow, so nothing else in the product can get in the way."""
    saved = dispatch.subscriptions()
    dispatch.clear_subscriptions()
    bookingflow.register_subscribers()
    yield
    dispatch.clear_subscriptions()
    for s in saved:
        dispatch.subscribe(s.consumer, s.prefix, s.handler)


class Flow:
    """A clinic with a receptionist, an administrator and a patient, using the pretend partner."""

    def __init__(self, system="simulator"):
        self.web = Web()
        self.web.patient("pat")
        self.web.staff("desk")
        self.web.login("boss", "facility_user", "R13")
        self.ctx = self.web.clinic.ctx
        self.org = self.web.clinic.org
        if system:
            done = self.web.post("staff/booking-system", {"system": system}, subject="boss")
            assert done.status_code == 200, done.content

    def request(self, index=0, subject="pat"):
        made = book(self.web, self.web.slots[index]["public_id"], subject=subject)
        assert made.status_code == 201, made.content
        return json.loads(made.content)["booking"]["id"]

    def ask(self, booking, subject="desk"):
        return self.web.post(f"bookings/{booking}/confirm", subject=subject)

    def pump(self):
        for _ in range(6):
            relay_once(dispatch.dispatch)

    def work(self, minutes=0):
        """One worker pass of the connector, with the clock moved on."""
        clock = FixedClock(dt.datetime.now(UTC) + dt.timedelta(minutes=minutes + 1))
        with bind_context(self.ctx):
            connector.send_due(clock=clock)
            connector.reconcile_due(clock=clock)
        self.pump()

    def status(self, booking):
        with tenant_transaction(self.ctx):
            return scheduling.get_booking(booking)["status"]

    def journey(self, booking):
        with tenant_transaction(self.ctx):
            return workflow.instances_for([booking], bookingflow.DEFINITION).get(booking)

    def operations(self, booking):
        with tenant_transaction(self.ctx):
            return connector.list_operations(subject_ref=booking)

    def stage_for(self, booking, subject="desk"):
        rows = json.loads(self.web.get("bookings", subject=subject).content)["bookings"]
        return {r["id"]: r["provider_stage"] for r in rows}[booking]


@pytest.fixture
def flow():
    return Flow()


# ------------------------------------------------------------------ the default does not change
def test_a_clinic_without_a_booking_system_confirms_by_hand_as_before():
    plain = Flow(system="")
    booking = plain.request()
    plain.pump()
    done = plain.ask(booking)
    assert done.status_code == 200
    assert json.loads(done.content)["booking"]["status"] == "confirmed"
    assert json.loads(done.content)["booking"]["provider_stage"] is None
    assert plain.journey(booking) is None and plain.operations(booking) == []


# ------------------------------------------------------------------ the setting
def test_the_administrator_chooses_how_bookings_are_confirmed_and_others_cannot():
    plain = Flow(system="")
    seen = json.loads(plain.web.get("staff/booking-system", subject="desk").content)
    assert seen["system"] == "" and seen["can_set"] is False
    assert [o["key"] for o in seen["options"]] == ["", "simulator"]
    assert plain.web.post("staff/booking-system", {"system": "simulator"},
                          subject="desk").status_code == 403
    assert plain.web.post("staff/booking-system", {"system": "nope"},
                          subject="boss").status_code == 422        # not a connector we have
    assert plain.web.post("staff/booking-system", {"system": 5}, subject="boss").status_code == 422
    chosen = plain.web.post("staff/booking-system", {"system": "simulator"}, subject="boss")
    assert json.loads(chosen.content)["system"] == "simulator"
    back = plain.web.post("staff/booking-system", {"system": ""}, subject="boss")
    assert json.loads(back.content)["system"] == ""
    assert plain.web.get("staff/booking-system", subject="pat").status_code == 403


def test_a_connector_this_deployment_does_not_have_cannot_be_chosen(flow):
    adapters.clear()                       # the connector is no longer available here
    assert flow.web.post("staff/booking-system", {"system": "simulator"},
                         subject="boss").status_code == 422
    with tenant_transaction(flow.ctx):
        with pytest.raises(ZoikoError) as caught:
            bookingflow.choose_system(flow.org, "simulator", by="x")
        assert caught.value.code == ErrorCode.VALIDATION_FAILED


# ------------------------------------------------------------------ the journey and the question
def test_requesting_a_booking_starts_its_journey_only_for_a_clinic_with_a_system(flow):
    booking = flow.request()
    flow.pump()
    assert flow.journey(booking)["state"] == "requested"
    plain = Flow(system="")
    other = plain.request()
    plain.pump()
    assert plain.journey(other) is None


def test_asking_the_booking_system_does_not_confirm_the_booking(flow):
    booking = flow.request()
    flow.pump()
    asked = flow.ask(booking)
    body = json.loads(asked.content)["booking"]
    assert asked.status_code == 200 and body["status"] == "requested"
    assert body["provider_stage"] == {"stage": "awaiting_provider", "operation": "pending"}
    flow.work()                                           # the partner says "received"
    assert flow.status(booking) == "requested"            # a receipt is not a confirmation
    assert flow.stage_for(booking) == {"stage": "awaiting_provider", "operation": "accepted"}
    assert flow.journey(booking)["state"] == "awaiting_provider"


def test_only_the_partners_own_confirmation_confirms_the_booking(flow):
    booking = flow.request()
    flow.pump()
    flow.ask(booking)
    flow.work()
    flow.work(minutes=5)                                  # the partner has now confirmed
    assert flow.status(booking) == "confirmed"
    assert flow.journey(booking)["state"] == "confirmed"
    with tenant_transaction(flow.ctx):
        steps = workflow.instance_history(flow.journey(booking)["public_id"])["steps"]
        history = scheduling.booking_history(booking)
    assert [s["transition"] for s in steps] == ["start", "ask_provider", "provider_confirm"]
    assert steps[-1]["actor_kind"] == "partner"
    assert [e["kind"] for e in steps[-1]["evidence"]] == ["source_confirmed_outcome"]
    last = history["events"][-1]
    assert (last["event_type"], last["source"]) == ("confirmed", "system")
    assert flow.stage_for(booking) == {"stage": "confirmed", "operation": ""}


def test_a_refusal_by_the_booking_system_cancels_the_booking(flow):
    booking = flow.request()
    flow.pump()
    flow.ask(booking)
    with tenant_transaction(flow.ctx):
        ExternalOperation.objects.filter(subject_ref=booking).update(request={"sim": "reject"})
    flow.work()
    assert flow.status(booking) == "cancelled"
    assert flow.journey(booking)["state"] == "refused"
    assert flow.journey(booking)["status"] == "cancelled"


def test_a_lost_reply_leaves_the_booking_requested_and_a_manual_settlement_is_not_enough(flow):
    booking = flow.request()
    flow.pump()
    flow.ask(booking)
    with tenant_transaction(flow.ctx):
        ExternalOperation.objects.filter(subject_ref=booking).update(request={"sim": "timeout"})
    flow.work()
    assert flow.status(booking) == "requested"
    assert flow.stage_for(booking) == {"stage": "awaiting_provider", "operation": "unknown"}
    assert flow.stage_for(booking, subject="pat") == {         # a patient sees only the stage
        "stage": "awaiting_provider", "operation": ""}
    waiting = json.loads(flow.web.get("staff/exceptions", subject="desk").content)
    assert [o["subject_ref"] for o in waiting["connector"]["attention"]] == [booking]
    (op,) = flow.operations(booking)
    flow.web.post(f"staff/connector/{op['public_id']}/resolve", {"outcome": "confirmed"},
                  subject="desk")
    flow.pump()
    assert flow.status(booking) == "requested"            # a person's word is not the provider's
    assert flow.journey(booking)["state"] == "awaiting_provider"
    flow.ask(booking)                                     # so the clinic may ask again
    assert len(flow.operations(booking)) == 2


def test_asking_twice_while_waiting_asks_the_system_once(flow):
    booking = flow.request()
    flow.pump()
    flow.ask(booking)
    assert flow.ask(booking).status_code == 200
    assert len(flow.operations(booking)) == 1


# ------------------------------------------------------------------ who may ask
def test_only_clinic_staff_may_ask_the_booking_system(flow):
    booking = flow.request()
    flow.pump()
    assert flow.ask(booking, subject="pat").status_code == 403
    assert flow.operations(booking) == []
    assert flow.ask(booking, subject="boss").status_code == 200       # the administrator may


def test_another_clinics_staff_cannot_ask_for_this_clinics_booking(flow):
    booking = flow.request()
    flow.pump()
    with tenant_transaction(flow.ctx):
        from modules.trust import api as trust
        other_org = trust.create_organization(name="Other Clinic").public_id
        rival = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="rival")
        trust.assign_role(actor_public_id=rival.public_id, organization_public_id=other_org,
                          role_code="R13", by="system")
    assert flow.ask(booking, subject="rival").status_code == 403
    assert flow.operations(booking) == []


def test_a_booking_that_is_no_longer_requested_cannot_be_asked(flow):
    booking = flow.request()
    flow.pump()
    flow.web.post(f"bookings/{booking}/cancel", {}, subject="pat")
    flow.pump()
    assert flow.ask(booking).status_code == 409


# ------------------------------------------------------------------ the rest of the journey
def test_cancelling_ends_the_journey_and_a_late_confirmation_changes_nothing(flow):
    booking = flow.request()
    flow.pump()
    flow.ask(booking)
    flow.work()                                           # received, not yet confirmed
    flow.web.post(f"bookings/{booking}/cancel", {}, subject="pat")
    flow.pump()
    assert flow.journey(booking)["status"] == "cancelled"
    flow.work(minutes=5)                                  # the partner confirms after all
    assert flow.status(booking) == "cancelled"            # too late: the booking stays cancelled
    assert flow.journey(booking)["state"] == "cancelled"


def test_recording_attendance_completes_the_journey(flow):
    booking = flow.request()
    flow.pump()
    flow.ask(booking)
    flow.work()
    flow.work(minutes=5)
    assert flow.status(booking) == "confirmed"
    past = dt.datetime(2020, 1, 1, 10, 0, tzinfo=UTC)
    with tenant_transaction(flow.ctx):                    # the visit has now happened
        Slot.objects.filter(public_id=flow.web.slots[0]["public_id"]).update(
            starts_at=past, ends_at=past + dt.timedelta(minutes=30))
    done = flow.web.post(f"bookings/{booking}/attendance", {"outcome": "attended"}, subject="desk")
    assert done.status_code == 200, done.content
    flow.pump()
    journey = flow.journey(booking)
    assert (journey["state"], journey["status"]) == ("attended", "completed")


def test_a_booking_made_before_the_system_was_chosen_can_still_be_asked():
    late = Flow(system="")
    booking = late.request()
    late.pump()
    assert late.journey(booking) is None
    late.web.post("staff/booking-system", {"system": "simulator"}, subject="boss")
    assert late.ask(booking).status_code == 200            # its journey starts when first asked
    assert late.journey(booking)["state"] == "awaiting_provider"


def test_the_connector_accepts_a_booking_permission_only_for_that_exact_booking(flow):
    booking = flow.request()
    flow.pump()
    desk = json.loads(flow.web.get("me", subject="desk").content)["actor"]
    with tenant_transaction(flow.ctx):
        person = scheduling.get_booking(booking)["person_ref"]
        decision = decide(actor_public_id=desk, operation="write:booking",
                          purpose_code="OPERATIONS", organization_public_id=flow.org,
                          subject_ref=person)
    asked = {"connector_key": "simulator", "operation_type": "booking.confirm",
             "subject_ref": booking, "organization_ref": flow.org, "request": {"x": "y"},
             "requested_by_ref": desk, "decision": decision, "authorized_as": "write:booking"}
    with tenant_transaction(flow.ctx):
        with pytest.raises(ZoikoError) as other_patient:      # a receipt for a different patient
            connector.request_operation(idempotency_key="k" * 20, receipt_subject_ref="someone",
                                        **asked)
        assert other_patient.value.code == ErrorCode.POLICY_DENIED
        with pytest.raises(ZoikoError) as wrong_permission:   # and for the wrong permission
            connector.request_operation(idempotency_key="j" * 20, receipt_subject_ref=person,
                                        **{**asked, "authorized_as": "write:partner"})
        assert wrong_permission.value.code == ErrorCode.POLICY_DENIED
        made = connector.request_operation(idempotency_key="i" * 20, receipt_subject_ref=person,
                                           **asked)
    assert made["state"] == "pending"
