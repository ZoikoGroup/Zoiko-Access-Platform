"""Decided 27 November 2026 (DEV-398): retention is a setting per customer, starting from periods
proposed for Legal to confirm, and records past a period in force are ANONYMISED (never deleted)
only in a batch that one administrator prepares and a different one approves."""
import json

import pytest

from foundation import retention as registry
from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from modules.evidence.models import AuditEvent
from modules.messaging.models import Message, Thread
from modules.retention import api as retention
from modules.rights.models import RightsCase
from modules.scheduling.models import Booking, BookingEvent
from tests.test_api import book
from tests.test_retention import FAR, _approved, _org, report, row
from tests.test_retention import p as p  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
YEARS = {"bookings": 8, "message_threads": 8, "audit_log": 10, "unused_accounts": 2}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def two_bookings(p):
    for slot in p.slots[:2]:
        assert book(p, slot["public_id"]).status_code in (200, 201)


def prepare(p, key="bookings", who="boss"):
    with tenant_transaction(p.clinic.ctx):
        return retention.prepare_batch(organization_ref=_org(p), data_class=key,
                                       by=_id(p, who), clock=FAR)


def approve(p, batch, who="boss2"):
    with tenant_transaction(p.clinic.ctx):
        return retention.approve_batch(organization_ref=_org(p), batch_ref=batch["public_id"],
                                       by=_id(p, who), clock=FAR)


def _id(p, who):
    from modules.trust.models import Actor
    with tenant_transaction(p.clinic.ctx):
        return Actor.objects.get(subject=who).public_id


# ------------------------------------------------------------------ suggested periods
def test_each_kind_shows_its_suggested_period_but_none_is_in_force(p):
    body = report(p)
    for key, years in YEARS.items():
        found = row(body, key)
        assert found["suggested_days"] == years * registry.YEAR and found["rule"] is None
    assert row(body, "bookings")["can_anonymise"] is True
    assert row(body, "audit_log")["can_anonymise"] is False        # a tamper-evident chain
    assert row(body, "unused_accounts")["can_anonymise"] is False


# ------------------------------------------------------------------ batches
def test_a_batch_needs_a_period_in_force_and_something_due(p):
    two_bookings(p)
    with pytest.raises(ZoikoError) as caught:
        prepare(p)
    assert "no_period_in_force" in str(caught.value.fields)
    _approved(p)
    with tenant_transaction(p.clinic.ctx):
        with pytest.raises(ZoikoError) as nothing:                    # today nothing is that old
            retention.prepare_batch(organization_ref=_org(p), data_class="bookings",
                                    by=_id(p, "boss"))
    assert "nothing_due" in str(nothing.value.fields)


def test_preparing_changes_nothing_and_only_a_different_administrator_approves(p):
    two_bookings(p)
    _approved(p)
    batch = prepare(p)
    assert batch["status"] == "prepared" and batch["expected_count"] == 2
    with tenant_transaction(p.clinic.ctx):
        assert Booking.objects.filter(person_ref=registry.ERASED).count() == 0
    with pytest.raises(ZoikoError) as same:
        approve(p, batch, who="boss")
    assert same.value.code == "POLICY_DENIED"
    done = approve(p, batch)
    assert done["status"] == "done" and done["done_count"] == 2


def test_anonymised_bookings_keep_their_history_and_lose_the_patient(p):
    two_bookings(p)
    _approved(p)
    with tenant_transaction(p.clinic.ctx):
        events_before = BookingEvent.objects.count()
    approve(p, prepare(p))
    with tenant_transaction(p.clinic.ctx):
        assert Booking.objects.count() == 2                           # nothing deleted
        assert set(Booking.objects.values_list("person_ref", flat=True)) == {registry.ERASED}
        assert BookingEvent.objects.count() == events_before          # history whole
        assert row(retention.report(_org(p), clock=FAR))["past_period"] == 0


def test_a_patient_under_a_lawful_hold_is_left_out(p):
    two_bookings(p)
    _approved(p)
    batch = prepare(p)
    with tenant_transaction(p.clinic.ctx):
        RightsCase.objects.create(
            ref_no="DSR-TEST0002", person_ref=p.person, organization_ref=_org(p),
            requested_by_ref=p.patient_actor, request_type="erasure", lawful_hold=True,
            hold_reason="Litigation", cell_id=p.clinic.ctx.cell_id,
            tenant_id=p.clinic.ctx.tenant_id)
    assert approve(p, batch)["done_count"] == 0                       # held since it was prepared
    with tenant_transaction(p.clinic.ctx):
        assert Booking.objects.filter(person_ref=registry.ERASED).count() == 0


def test_closed_conversations_lose_what_was_written_and_open_ones_are_untouched(p):
    made = p.post("messages/threads", {"subject": "A private question",
                                       "body": "My private symptoms"}, subject="pat")
    thread = json.loads(made.content)["thread"]["id"]
    p.post("messages/threads", {"subject": "Still talking", "body": "Open one"}, subject="pat")
    assert p.post(f"messages/threads/{thread}/close", subject="ops").status_code == 200
    _approved(p, key="message_threads")
    done = approve(p, prepare(p, key="message_threads"))
    assert done["done_count"] == 1
    with tenant_transaction(p.clinic.ctx):
        assert Thread.objects.count() == 2 and Message.objects.count() == 2
    r = p.get(f"messages/threads/{thread}", subject="ops")
    text = r.content.decode()
    assert "My private symptoms" not in text and "A private question" not in text


def test_one_waiting_batch_per_kind_and_a_withdrawn_one_never_runs(p):
    two_bookings(p)
    _approved(p)
    batch = prepare(p)
    with pytest.raises(ZoikoError) as twice:
        prepare(p)
    assert twice.value.code == "CONFLICT_VERSION"
    with tenant_transaction(p.clinic.ctx):
        retention.withdraw_batch(organization_ref=_org(p), batch_ref=batch["public_id"],
                                 by=_id(p, "boss2"))
    with pytest.raises(ZoikoError) as late:
        approve(p, batch)
    assert late.value.code == "CONFLICT_VERSION"
    with tenant_transaction(p.clinic.ctx):
        assert Booking.objects.filter(person_ref=registry.ERASED).count() == 0


def test_kinds_that_are_only_counted_cannot_be_put_in_a_batch(p):
    _approved(p, key="audit_log")
    with pytest.raises(ZoikoError) as caught:
        prepare(p, key="audit_log")
    assert "not_anonymisable" in str(caught.value.fields)


def test_only_administrators_use_the_batch_screen_and_every_step_is_audited(p):
    assert p.post("staff/retention-batches", {"data_class": "bookings"},
                  subject="ops").status_code == 403
    two_bookings(p)
    _approved(p)
    approve(p, prepare(p))
    with tenant_transaction(p.clinic.ctx):
        actions = set(AuditEvent.objects.filter(action__startswith="retention.batch_")
                      .values_list("action", flat=True))
    assert actions == {"retention.batch_prepared", "retention.batch_anonymised"}
