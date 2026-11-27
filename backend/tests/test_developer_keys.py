"""Replacing and expiring a partner's API key: the old key keeps working for a grace period, only
hashes are stored, and only a clinic's own administrator may do either."""
import datetime as dt
import hashlib
import json

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, FixedClock, utc_now
from modules.developer import api as developer
from modules.developer.models import ApiClient
from modules.evidence.models import AuditEvent
from tests.test_console import PUBLIC, Console, make_client

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def c():
    return Console()


def made(c):
    body = c.json(make_client(c))
    return body["client"]["public_id"], body["key"]


def ping(key):
    return Client().get(PUBLIC + "ping", HTTP_AUTHORIZATION=f"Bearer {key}").status_code


def rotate(c, client, subject="boss", **body):
    return c.post(f"staff/developer/clients/{client}/rotate", body, subject=subject)


def expiry(c, client, subject="boss", **body):
    return c.post(f"staff/developer/clients/{client}/expiry", body, subject=subject)


def secret_of(key):
    return key.split(".")[3]


def digest(key):
    return hashlib.sha256(secret_of(key).encode()).hexdigest()


def at(hours=0, days=0):
    return FixedClock(utc_now() + dt.timedelta(hours=hours, days=days))


def authenticate(key, clock=None):
    return developer.authenticate_key(f"Bearer {key}", clock=clock)


def refused(key, clock=None):
    with pytest.raises(ZoikoError) as caught:
        authenticate(key, clock)
    return caught.value.code == ErrorCode.UNAUTHENTICATED


# ------------------------------------------------------------------ replacing the key
def test_the_new_key_is_shown_once_and_both_keys_work_during_the_grace_period(c):
    client, old = made(c)
    r = rotate(c, client)
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    body = c.json(r)
    new = body["key"]
    assert new.startswith("zk1.") and new != old and new.split(".")[2] == client
    assert ping(new) == 200
    assert ping(old) == 200                                     # the partner can swap without a gap
    assert body["client"]["rotated_at"] and body["client"]["previous_key_valid_until"]
    listed = json.dumps(c.json(c.get("staff/developer", "boss")))
    assert new not in listed and old not in listed              # a key is never shown again


def test_with_no_grace_the_old_key_stops_at_once(c):
    client, old = made(c)
    new = c.json(rotate(c, client, grace_hours=0))["key"]
    assert ping(old) == 401 and ping(new) == 200
    (shown,) = c.json(c.get("staff/developer", "boss"))["clients"]
    assert shown["previous_key_valid_until"] is None


def test_the_old_key_stops_when_its_grace_ends(c):
    client, old = made(c)
    new = c.json(rotate(c, client, grace_hours=2))["key"]
    assert authenticate(old, at(hours=1)).client_ref == client       # still inside the grace
    assert refused(old, at(hours=3)) is True                          # grace over
    assert authenticate(new, at(hours=3)).client_ref == client       # the new key is unaffected


def test_rotating_again_ends_the_key_before_last_so_at_most_two_keys_work(c):
    client, first = made(c)
    second = c.json(rotate(c, client))["key"]
    third = c.json(rotate(c, client))["key"]
    assert ping(first) == 401                                         # two rotations ago: gone
    assert ping(second) == 200 and ping(third) == 200


def test_only_hashes_are_stored_for_both_keys(c):
    client, old = made(c)
    new = c.json(rotate(c, client))["key"]
    with tenant_transaction(c.ctx):
        row = ApiClient.objects.get(public_id=client)
    assert row.key_hash == digest(new) and row.previous_key_hash == digest(old)
    assert secret_of(new) not in (row.key_hash + row.previous_key_hash)


def test_rotation_is_audited_without_any_key(c):
    client, old = made(c)
    new = c.json(rotate(c, client, grace_hours=6))["key"]
    with tenant_transaction(c.ctx):
        rows = list(AuditEvent.objects.filter(action="developer.client_key_rotated"))
    assert len(rows) == 1 and rows[0].object_ref == client and rows[0].reason_code == "grace_6h"
    blob = json.dumps([rows[0].details, rows[0].reason_code, rows[0].object_ref])
    assert secret_of(new) not in blob and secret_of(old) not in blob


@pytest.mark.parametrize("bad", [-1, 169, "24", 1.5, True, None, [24]])
def test_a_grace_period_must_be_whole_hours_from_0_to_168(c, bad):
    client, old = made(c)
    assert rotate(c, client, grace_hours=bad).status_code == 422
    assert ping(old) == 200                                           # nothing changed


def test_the_default_grace_period_is_a_day(c):
    client, old = made(c)
    assert rotate(c, client).status_code == 200
    assert authenticate(old, at(hours=23)).client_ref == client
    assert refused(old, at(hours=25)) is True


# ------------------------------------------------------------------ who may do it
def test_only_the_clinic_administrator_may_replace_a_key(c):
    client, old = made(c)
    for subject in ("desk", "eng", "pat"):
        assert rotate(c, client, subject=subject).status_code == 403, subject
    assert ping(old) == 200
    with tenant_transaction(c.ctx):
        assert ApiClient.objects.get(public_id=client).rotated_at is None


def test_another_clinic_cannot_replace_a_key_it_does_not_own(c):
    client, old = made(c)
    assert rotate(c, client, subject="rival").status_code == 404
    assert expiry(c, client, subject="rival", days=30).status_code == 404
    assert ping(old) == 200


def test_an_unknown_client_is_not_found_and_a_revoked_one_cannot_be_changed(c):
    assert rotate(c, "nosuchclient000000000").status_code == 404
    client, key = made(c)
    c.post(f"staff/developer/clients/{client}/revoke")
    assert rotate(c, client).status_code == 409
    assert expiry(c, client, days=30).status_code == 409
    assert ping(key) == 401


# ------------------------------------------------------------------ expiry
def test_a_key_can_be_made_to_run_out_and_the_date_is_shown(c):
    client, key = made(c)
    r = expiry(c, client, days=30)
    assert r.status_code == 200
    shown = c.json(r)["client"]
    due = dt.datetime.fromisoformat(shown["expires_at"])
    assert abs((due - (utc_now() + dt.timedelta(days=30))).total_seconds()) < 120
    assert shown["expired"] is False and shown["expiring_soon"] is False
    assert ping(key) == 200


def test_a_key_that_runs_out_soon_is_flagged_and_one_that_has_run_out_is_refused(c):
    client, key = made(c)
    soon = c.json(expiry(c, client, days=7))["client"]
    assert soon["expiring_soon"] is True and soon["expired"] is False
    assert authenticate(key, at(days=6)).client_ref == client
    assert refused(key, at(days=8)) is True


def test_an_expiry_can_be_moved_or_removed(c):
    client, key = made(c)
    expiry(c, client, days=7)
    moved = c.json(expiry(c, client, days=365))["client"]
    assert moved["expiring_soon"] is False
    assert authenticate(key, at(days=30)).client_ref == client
    cleared = c.json(expiry(c, client, days=None))["client"]
    assert cleared["expires_at"] is None and cleared["expiring_soon"] is False
    assert authenticate(key, at(days=1000)).client_ref == client


@pytest.mark.parametrize("bad", [0, -5, 731, "30", 1.5, True, [30]])
def test_an_expiry_must_be_whole_days_from_1_to_730(c, bad):
    client, key = made(c)
    assert expiry(c, client, days=bad).status_code == 422
    with tenant_transaction(c.ctx):
        assert ApiClient.objects.get(public_id=client).expires_at is None


def test_the_days_field_must_be_sent_and_only_the_administrator_may_set_it(c):
    client, key = made(c)
    assert expiry(c, client).status_code == 422                       # nothing said: no change
    assert expiry(c, client, subject="desk", days=30).status_code == 403
    assert expiry(c, client, subject="pat", days=30).status_code == 403


def test_a_replaced_key_keeps_the_clients_expiry(c):
    client, old = made(c)
    expiry(c, client, days=7)
    new = c.json(rotate(c, client))["key"]
    assert refused(new, at(days=8)) is True and refused(old, at(days=8)) is True
    assert authenticate(new, at(days=2)).client_ref == client


def test_the_staff_list_shows_the_expiry_and_rotation_state(c):
    client, key = made(c)
    expiry(c, client, days=7)
    rotate(c, client, grace_hours=12)
    (shown,) = c.json(c.get("staff/developer", "boss"))["clients"]
    assert shown["expiring_soon"] is True and shown["rotated_at"]
    assert shown["previous_key_valid_until"]
    assert dt.datetime.fromisoformat(shown["previous_key_valid_until"]) > dt.datetime.now(UTC)
