import copy
import datetime as dt
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, FixedClock
from modules.packs import api as packs
from modules.packs.models import CountryPack, PackStatus
from modules.packs.signing import (
    build_signed_notice,
    build_signed_pack,
    generate_keypair,
    parse_public_keys,
    sign,
    verify,
)

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAY = dt.timedelta(days=1)
CAPS = {"booking.request": {"enabled": True}, "referral.exchange": {"enabled": False},
        "messaging.secure": {"enabled": True}}


@pytest.fixture
def keys(settings):
    private, public = generate_keypair()
    settings.PACK_PUBLIC_KEYS = f"g1:{public}"
    return private


@pytest.fixture
def clock():
    return FixedClock(NOW)


def pack(private, *, country="GB", version=1, epoch=0, caps=None, valid_from=None, expires=None,
         key_id="g1", around=NOW):
    return build_signed_pack(
        private_key_b64=private, key_id=key_id, country=country, version=version,
        issued_at=around - DAY, valid_from=valid_from or around - DAY,
        expires_at=expires or around + 30 * DAY, revocation_epoch=epoch,
        capabilities=copy.deepcopy(caps or CAPS))


# ------------------------------------------------------------------ the happy path
def test_an_installed_pack_switches_its_capabilities_on(keys, clock):
    packs.install_pack(pack(keys), clock=clock)
    assert packs.capability_enabled("GB", "booking.request", clock=clock)
    assert packs.capability_enabled("GB", "messaging.secure", clock=clock)
    assert packs.enabled_capabilities("GB", clock=clock) == ["booking.request", "messaging.secure"]


def test_a_capability_the_pack_does_not_enable_is_off(keys, clock):
    packs.install_pack(pack(keys), clock=clock)
    assert not packs.capability_enabled("GB", "referral.exchange", clock=clock)  # listed, disabled
    assert not packs.capability_enabled("GB", "billing.claims", clock=clock)     # not listed
    assert not packs.capability_enabled("FR", "booking.request", clock=clock)    # no pack: off


def test_require_capability_refuses_with_the_jurisdiction_code(keys, clock):
    packs.install_pack(pack(keys), clock=clock)
    packs.require_capability("GB", "booking.request", clock=clock)
    with pytest.raises(ZoikoError) as caught:
        packs.require_capability("GB", "billing.claims", clock=clock)
    assert caught.value.code == ErrorCode.JURISDICTION_INACTIVE


def test_installing_the_same_pack_again_changes_nothing(keys, clock):
    doc = pack(keys)
    first = packs.install_pack(doc, clock=clock)
    assert packs.install_pack(doc, clock=clock)["public_id"] == first["public_id"]
    assert CountryPack.objects.count() == 1


def test_a_newer_version_replaces_the_older_one(keys, clock):
    packs.install_pack(pack(keys, version=1), clock=clock)
    packs.install_pack(
        pack(keys, version=2, caps={"booking.request": {"enabled": False}}), clock=clock)
    assert not packs.capability_enabled("GB", "booking.request", clock=clock)
    statuses = {p.version: p.status for p in CountryPack.objects.all()}
    assert statuses == {1: PackStatus.SUPERSEDED, 2: PackStatus.ACTIVE}


def test_an_older_or_equal_version_is_refused_so_there_is_no_rollback(keys, clock):
    packs.install_pack(pack(keys, version=3), clock=clock)
    for version in (1, 2):
        with pytest.raises(packs.PackRejected) as caught:
            packs.install_pack(pack(keys, version=version), clock=clock)
        assert caught.value.reason == "version_not_newer"
    other = pack(keys, version=3, caps={"booking.request": {"enabled": False}})
    with pytest.raises(packs.PackRejected):
        packs.install_pack(other, clock=clock)         # same version, different content


# ------------------------------------------------------------------ trust: who signed it
def test_no_trusted_key_means_nothing_is_accepted(settings, clock):
    private, _ = generate_keypair()
    settings.PACK_PUBLIC_KEYS = ""
    with pytest.raises(packs.PackRejected) as caught:
        packs.install_pack(pack(private), clock=clock)
    assert caught.value.reason == "unknown_key"


def test_a_pack_signed_by_someone_else_is_refused(keys, clock):
    other_private, _ = generate_keypair()
    with pytest.raises(packs.PackRejected) as caught:
        packs.install_pack(pack(other_private), clock=clock)
    assert caught.value.reason == "bad_signature"


def test_a_pack_whose_content_was_changed_after_signing_is_refused(keys, clock):
    doc = pack(keys)
    doc["capabilities"]["referral.exchange"]["enabled"] = True
    with pytest.raises(packs.PackRejected) as caught:
        packs.install_pack(doc, clock=clock)
    assert caught.value.reason == "bad_signature"


def test_an_unknown_key_id_is_refused(keys, clock):
    with pytest.raises(packs.PackRejected) as caught:
        packs.install_pack(pack(keys, key_id="other"), clock=clock)
    assert caught.value.reason == "unknown_key"


# ------------------------------------------------------------------ time and revocation
def test_a_pack_that_is_not_yet_valid_or_already_expired_is_refused(keys, clock):
    with pytest.raises(packs.PackRejected) as early:
        packs.install_pack(pack(keys, valid_from=NOW + DAY, expires=NOW + 5 * DAY), clock=clock)
    assert early.value.reason == "not_yet_valid"
    with pytest.raises(packs.PackRejected) as late:
        packs.install_pack(pack(keys, valid_from=NOW - 9 * DAY, expires=NOW - DAY), clock=clock)
    assert late.value.reason == "expired"


def test_a_pack_stops_working_the_moment_it_expires(keys, clock):
    packs.install_pack(pack(keys, expires=NOW + 2 * DAY), clock=clock)
    assert packs.capability_enabled("GB", "booking.request", clock=clock)
    clock.advance(3 * DAY)
    assert not packs.capability_enabled("GB", "booking.request", clock=clock)   # fails closed
    assert packs.active_pack("GB", clock=clock) is None


def test_a_tampered_database_row_stops_counting(keys, clock):
    packs.install_pack(pack(keys), clock=clock)
    row = CountryPack.objects.get()
    row.capabilities = {**row.capabilities, "referral.exchange": {"enabled": True}}
    row.save()
    assert not packs.capability_enabled("GB", "referral.exchange", clock=clock)
    assert packs.active_pack("GB", clock=clock) is None


def test_a_revocation_notice_switches_older_packs_off_at_once(keys, clock):
    packs.install_pack(pack(keys, epoch=1), clock=clock)
    notice = build_signed_notice(private_key_b64=keys, key_id="g1", min_epoch=2, issued_at=NOW)
    done = packs.apply_revocation_notice(notice, clock=clock)
    assert done == {"min_epoch": 2, "revoked_packs": 1}
    assert not packs.capability_enabled("GB", "booking.request", clock=clock)
    assert CountryPack.objects.get().status == PackStatus.REVOKED


def test_a_pack_below_the_revocation_floor_cannot_be_installed(keys, clock):
    packs.apply_revocation_notice(
        build_signed_notice(private_key_b64=keys, key_id="g1", min_epoch=5, issued_at=NOW),
        clock=clock)
    with pytest.raises(packs.PackRejected) as caught:
        packs.install_pack(pack(keys, epoch=4), clock=clock)
    assert caught.value.reason == "revoked"
    packs.install_pack(pack(keys, epoch=5), clock=clock)          # at the floor is fine


def test_the_revocation_floor_never_goes_down_and_must_be_signed(keys, clock):
    high = build_signed_notice(private_key_b64=keys, key_id="g1", min_epoch=7, issued_at=NOW)
    packs.apply_revocation_notice(high, clock=clock)
    low = build_signed_notice(private_key_b64=keys, key_id="g1", min_epoch=3, issued_at=NOW)
    with pytest.raises(packs.PackRejected) as caught:
        packs.apply_revocation_notice(low, clock=clock)
    assert caught.value.reason == "epoch_goes_down"
    forged = {**low, "min_epoch": 99}
    with pytest.raises(packs.PackRejected) as caught:
        packs.apply_revocation_notice(forged, clock=clock)
    assert caught.value.reason == "bad_signature"


# ------------------------------------------------------------------ malformed documents
@pytest.mark.parametrize("change", [
    {"country": "gb"}, {"country": "GBR"}, {"version": 0}, {"version": True},
    {"capabilities": {}}, {"capabilities": {"Bad Name": {"enabled": True}}},
    {"capabilities": {"booking": {"enabled": "yes"}}}, {"issued_at": "not a time"},
    {"issued_at": "2026-11-10T12:00:00"},                      # no zone: refused, never guessed
    {"revocation_epoch": -1},
])
def test_malformed_packs_are_refused_before_trust_is_even_considered(keys, clock, change):
    body = {**pack(keys), **change}
    body.pop("signature")
    doc = {**body, "signature": sign(keys, body)}              # validly signed, still malformed
    with pytest.raises(packs.PackRejected):
        packs.install_pack(doc, clock=clock)


def test_extra_or_missing_fields_are_refused(keys, clock):
    doc = pack(keys)
    with pytest.raises(packs.PackRejected):
        packs.install_pack({**doc, "extra": 1}, clock=clock)
    del doc["version"]
    with pytest.raises(packs.PackRejected):
        packs.install_pack(doc, clock=clock)
    with pytest.raises(packs.PackRejected):
        packs.install_pack("not a document", clock=clock)


def test_a_country_that_is_not_two_capital_letters_never_matches():
    assert packs.active_pack("gb") is None
    assert packs.active_pack("") is None
    assert not packs.capability_enabled("G", "x")


# ------------------------------------------------------------------ signing helpers
def test_signatures_verify_only_for_the_exact_body():
    private, public = generate_keypair()
    signature = sign(private, {"a": 1})
    assert verify(public, {"a": 1}, signature)
    assert not verify(public, {"a": 2}, signature)
    assert not verify(public, {"a": 1}, "not base64!!")
    assert not verify("not a key", {"a": 1}, signature)


def test_public_key_settings_must_be_well_formed():
    assert parse_public_keys("") == {}
    assert parse_public_keys("a:AAA, b:BBB") == {"a": "AAA", "b": "BBB"}
    for bad in ("a", "a:", ":b", "a:1,a:2"):
        with pytest.raises(ValueError):
            parse_public_keys(bad)


# ------------------------------------------------------------------ the install command
def test_the_command_installs_a_pack_and_reports_a_refusal(keys, tmp_path, capsys):
    real_now = dt.datetime.now(UTC)
    good = tmp_path / "pack.json"
    good.write_text(json.dumps(pack(keys, around=real_now)))
    call_command("install_pack", str(good))
    assert json.loads(capsys.readouterr().out)["country"] == "GB"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(CommandError):
        call_command("install_pack", str(bad))
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({**pack(keys, around=real_now), "version": 9}))
    with pytest.raises(CommandError, match="bad_signature"):
        call_command("install_pack", str(forged))


def test_packs_are_cell_wide_and_hold_no_tenant_column():
    names = {f.name for f in CountryPack._meta.get_fields()}
    assert "tenant_id" not in names
    with connection.cursor() as cursor:
        cursor.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'packs_countrypack'")
        assert cursor.fetchone()[0] is False        # no patient data, so no row-level security
