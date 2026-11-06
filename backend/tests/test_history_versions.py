import copy
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from foundation.cell_context import tenant_transaction
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.timeutil import utc_now
from modules.scheduling.history import (
    DIGEST_VERSION,
    _legacy_digest,
    _parts,
    _verify,
    log_event,
)
from modules.scheduling.models import Booking, BookingEvent, Slot
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.add_times(practice.doc, count=4)
    return practice


def booked(p):
    r = book(p, p.slot_of("doc"), subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["booking"]["id"]


def rows(p, booking):
    with tenant_transaction(p.clinic.ctx):
        return list(BookingEvent.objects.filter(booking__public_id=booking).order_by("seq"))


def unlogged_booking(p):
    """A booking written straight into the table, with no history yet (like one from before)."""
    slot_ref = p.slot_of("doc")                         # a web call: outside any context
    with tenant_transaction(p.clinic.ctx):
        slot = Slot.objects.get(public_id=slot_ref)
        return Booking.objects.create(slot=slot, person_ref=p.person, booked_by_ref=p.doc).public_id


def legacy_row(p, booking, seq, prev, event_type="requested", state_from="", state_to="requested"):
    """A row written the way version 1 did (the first digest layout), straight into the table."""
    at = utc_now()
    digest = _legacy_digest(prev, booking, seq, event_type, state_from, state_to, "patient",
                            "someone", "", "", at.isoformat())
    with tenant_transaction(p.clinic.ctx):
        return BookingEvent.objects.create(
            booking=Booking.objects.get(public_id=booking), seq=seq, event_type=event_type,
            state_from=state_from, state_to=state_to, source="patient", actor_ref="someone",
            occurred_at=at, digest_version=1, prev_digest=prev, record_digest=digest)


# ------------------------------------------------------------------ new rows
def test_new_steps_are_written_with_the_current_version_on_the_shared_helper(p):
    booking = booked(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    first, second = rows(p, booking)
    assert DIGEST_VERSION == 2 and first.digest_version == second.digest_version == 2
    expected = chain_digest(GENESIS, 1, *_parts(
        booking, "requested", "", "requested", "patient", first.actor_ref, "", "",
        first.occurred_at.isoformat()))
    assert first.record_digest == expected


def test_a_row_cannot_be_written_without_saying_which_version(p):
    booking = booked(p)
    with tenant_transaction(p.clinic.ctx), pytest.raises(IntegrityError), transaction.atomic():
        BookingEvent.objects.create(
            booking=Booking.objects.get(public_id=booking), seq=9, event_type="requested",
            state_to="requested", source="clinic", actor_ref="x", occurred_at=utc_now(),
            prev_digest=GENESIS, record_digest="f" * 64)


# ------------------------------------------------------------------ old rows
def test_a_legacy_chain_checks_out_on_its_own_and_when_extended_by_new_steps(p):
    ref = unlogged_booking(p)
    first = legacy_row(p, ref, 1, GENESIS)
    second = legacy_row(p, ref, 2, first.record_digest, "confirmed", "requested", "confirmed")
    assert _verify(ref, [first, second]) == (True, None)
    with tenant_transaction(p.clinic.ctx):
        log_event(Booking.objects.get(public_id=ref), "cancelled", state_from="confirmed",
                  state_to="cancelled", source="patient", actor_ref="someone")
    chain = rows(p, ref)
    assert [r.digest_version for r in chain] == [1, 1, 2]
    assert chain[2].prev_digest == chain[1].record_digest and chain[2].seq == 3
    assert _verify(ref, chain) == (True, None)                       # a mixed chain is fine


def test_tampering_with_an_old_row_is_still_noticed(p):
    ref = unlogged_booking(p)
    first = legacy_row(p, ref, 1, GENESIS)
    for field, value in (("actor_ref", "other"), ("state_to", "cancelled"), ("event_type", "done"),
                         ("reason_code", "x"), ("related_ref", "y"), ("source", "system"),
                         ("occurred_at", first.occurred_at + dt.timedelta(seconds=1))):
        edited = copy.copy(first)                       # an in-memory copy to tamper with
        setattr(edited, field, value)
        assert _verify(ref, [edited]) == (False, 1), field
    assert _verify(ref, [first]) == (True, None)                     # the untouched row is fine


def test_the_version_label_cannot_be_flipped_to_make_a_row_pass(p):
    booking = booked(p)
    (new_row,) = rows(p, booking)
    new_row.digest_version = 1                                       # claims the old layout
    assert _verify(booking, [new_row]) == (False, 1)
    fresh = unlogged_booking(p)
    old_row = legacy_row(p, fresh, 1, GENESIS)
    old_row.digest_version = DIGEST_VERSION                          # claims the new layout
    assert _verify(fresh, [old_row]) == (False, 1)


@pytest.mark.parametrize("version", [0, 3, 99])
def test_a_row_with_an_unknown_version_fails_the_check(p, version):
    booking = booked(p)
    (row,) = rows(p, booking)
    row.digest_version = version
    assert _verify(booking, [row]) == (False, 1)


# ------------------------------------------------------------------ the real migration
def test_the_real_migration_gives_existing_rows_version_1_and_they_still_check_out():
    """Go back to before the column existed, write a row the old way, run the migration forward."""
    with transaction.atomic():
        executor = MigrationExecutor(connection)
        executor.migrate([("scheduling", "0008_bookingevent")])
        old = MigrationExecutor(connection).loader.project_state(
            [("scheduling", "0008_bookingevent")]).apps
        OldSlot, OldBooking, OldEvent = (old.get_model("scheduling", n)
                                         for n in ("Slot", "Booking", "BookingEvent"))
        tenant = "6f1f4a3e-0000-4000-8000-000000000001"
        common = {"cell_id": "TEST-01", "tenant_id": tenant}
        start = utc_now() + dt.timedelta(days=2)
        slot = OldSlot.objects.create(
            service_ref="svc", location_ref="loc", organization_ref="org", starts_at=start,
            ends_at=start + dt.timedelta(minutes=30), local_zone="UTC", **common)
        booking = OldBooking.objects.create(
            slot=slot, person_ref="person", booked_by_ref="who", **common)
        at = utc_now()
        digest = _legacy_digest(GENESIS, booking.public_id, 1, "requested", "", "requested",
                                "patient", "someone", "", "", at.isoformat())
        OldEvent.objects.create(
            booking=booking, seq=1, event_type="requested", state_from="", state_to="requested",
            source="patient", actor_ref="someone", occurred_at=at, prev_digest=GENESIS,
            record_digest=digest, **common)

        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")   # a real migration has none pending

        MigrationExecutor(connection).migrate([("scheduling", "0009_bookingevent_digest_version")])

        row = BookingEvent.objects.get(booking__public_id=booking.public_id)
        assert row.digest_version == 1                               # existing rows are version 1
        assert _verify(booking.public_id, [row]) == (True, None)     # and still check out
        with connection.cursor() as cursor:                  # the table still holds just that row
            cursor.execute("SELECT count(*) FROM scheduling_bookingevent")
            assert cursor.fetchone()[0] == 1
        transaction.set_rollback(True)                       # leave the test database as it was
    assert MigrationExecutor(connection).loader.applied_migrations.keys() >= {
        ("scheduling", "0009_bookingevent_digest_version")}


# ------------------------------------------------------------------ the shared helper
def make(seq, prev, version, parts):
    digest = chain_digest(prev, seq, *parts) if version == 2 else f"legacy-{seq}-{prev[:6]}"
    return SimpleNamespace(seq=seq, prev_digest=prev, record_digest=digest, version=version,
                           parts=parts)


def test_the_helper_needs_exactly_one_way_to_make_a_digest():
    with pytest.raises(ValueError):
        verify_chain([])
    with pytest.raises(ValueError):
        verify_chain([], lambda row: [], digest_of=lambda prev, row: "x")


def test_a_custom_digest_function_decides_what_each_row_must_be():
    def digest_of(prev, row):
        if row.version == 1:
            return f"legacy-{row.seq}-{prev[:6]}"
        return chain_digest(prev, row.seq, *row.parts)

    a = make(1, GENESIS, 1, ["x"])
    b = make(2, a.record_digest, 2, ["y"])
    c = make(3, b.record_digest, 1, ["z"])
    assert verify_chain([a, b, c], digest_of=digest_of) == (True, None)
    assert verify_chain([a, c], digest_of=digest_of) == (False, 2)
    b.parts = ["changed"]
    assert verify_chain([a, b, c], digest_of=digest_of) == (False, 2)


def test_a_row_the_function_cannot_check_fails():
    a = make(1, GENESIS, 2, ["x"])
    assert verify_chain([a], digest_of=lambda prev, row: None) == (False, 1)


# ------------------------------------------------------------------ the layouts are pinned
# These fixed values pin how digests are made. If one of these tests fails, existing history rows
# in real databases would stop checking out: that needs a NEW version number, never an edit here.
_ARGS = ("booking-1", "confirmed", "requested", "confirmed", "clinic", "actor-1", "", "",
         "2026-01-02T03:04:05+00:00")


def test_version_1_digests_are_pinned_to_a_known_value():
    assert _legacy_digest(GENESIS, "booking-1", 2, *_ARGS[1:]) == (
        "27290cd89144827e75cb26b62bc88c4851b59523d8ee2d760e6b563f3fde20be")


def test_version_2_digests_are_pinned_to_a_known_value():
    assert chain_digest(GENESIS, 2, *_parts(*_ARGS)) == (
        "a7aa4936ebccca10269f966db5399027066ddbf46f21a62ec732eda297d9dca5")


def test_the_shared_helper_digest_is_pinned_to_a_known_value():
    assert chain_digest(GENESIS, 1, "a", "b") == (
        "5c9e48c3ab228a170871605a03f76b0980beb9aa4d396c5c0fa3f0bb8bb4008d")


def test_the_two_layouts_differ_so_a_version_label_matters():
    legacy = _legacy_digest(GENESIS, "booking-1", 2, *_ARGS[1:])
    assert legacy != chain_digest(GENESIS, 2, *_parts(*_ARGS))
