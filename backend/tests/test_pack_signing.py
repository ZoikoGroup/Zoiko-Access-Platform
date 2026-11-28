"""Country packs are signed when both approvals are in, and the signature is checked on every read
(GHE-16 AT-10, DEV-210). A row forged or edited in the database counts for nothing."""
import pytest

from foundation.cell_context import tenant_transaction
from modules.jurisdiction import services, signing
from modules.jurisdiction.models import CountryPack, PackStatus
from modules.trust import api as trust
from tests.test_clinician import Practice
from tests.test_emergency_number import number, set_country, with_number
from tests.test_packs import make, packs, signed, step, to_review
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12"), ("noor", "R12")):
        w.staff(subject, role)
    return w


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def row_of(world, pack):
    return CountryPack.objects.get(public_id=pack["id"])


def on_now(world, country="GB"):
    with tenant_transaction(world.ctx):
        return services.enabled_capabilities(country)


def shown(world, pack):
    return next(x for x in packs(world) if x["id"] == pack["id"])


def test_a_signed_pack_carries_a_good_signature_from_the_key_in_use(world):
    pack = signed(world)
    assert shown(world, pack)["signature"] == {"key_id": signing.key_id(), "valid": True}
    assert "booking" in on_now(world)


def test_a_pack_not_yet_signed_has_no_signature(world):
    pack = to_review(world)
    assert shown(world, pack)["signature"] is None
    with tenant_transaction(world.ctx):
        assert row_of(world, pack).signature == ""


def test_changing_a_signed_packs_rules_in_the_database_makes_it_count_for_nothing(world, p):
    pack = signed(world, manifest=with_number("999"))
    set_country(world, p, "GB")
    assert number(p) == "999"
    with tenant_transaction(world.ctx):
        row = row_of(world, pack)
        row.manifest["settings"]["emergency_number"] = "123"      # a forged change
        row.save(update_fields=["manifest"])
    assert number(p) is None                       # nothing shown, not the forged number
    assert on_now(world) == frozenset()
    assert shown(world, pack)["signature"]["valid"] is False


def test_marking_a_pack_signed_by_hand_switches_nothing_on(world):
    pack = to_review(world)
    with tenant_transaction(world.ctx):
        CountryPack.objects.filter(public_id=pack["id"]).update(status=PackStatus.SIGNED)
    assert on_now(world) == frozenset()


def test_a_copied_signature_does_not_fit_another_pack(world):
    first = signed(world, ref="gb-care-navigation")
    second = to_review(world, ref="gb-care-other")
    with tenant_transaction(world.ctx):
        source = row_of(world, first)
        CountryPack.objects.filter(public_id=second["id"]).update(
            status=PackStatus.SIGNED, signature=source.signature,
            signing_key_id=source.signing_key_id)
        assert signing.is_valid(row_of(world, second)) is False


def test_suspending_removes_the_signature_so_flipping_it_back_does_nothing(world):
    pack = signed(world)
    assert step(world, pack, "suspend", "iyer", note="checking the number").status_code == 200
    with tenant_transaction(world.ctx):
        assert row_of(world, pack).signature == ""
        CountryPack.objects.filter(public_id=pack["id"]).update(status=PackStatus.SIGNED)
    assert on_now(world) == frozenset()


def test_a_signature_from_another_key_is_refused(world, settings):
    pack = signed(world)
    settings.PACK_SIGNING_KEY = signing.generate_key()
    assert on_now(world) == frozenset()
    assert shown(world, pack)["signature"]["valid"] is False


@pytest.mark.parametrize("bad", ["", "not base64!", "QUJD", "A" * 88])
def test_a_missing_or_malformed_signature_is_refused(world, bad):
    pack = signed(world)
    with tenant_transaction(world.ctx):
        CountryPack.objects.filter(public_id=pack["id"]).update(signature=bad)
        assert signing.is_valid(row_of(world, pack)) is False


def test_a_configured_key_must_be_32_bytes_of_base64(settings):
    settings.PACK_SIGNING_KEY = "QUJD"
    with pytest.raises(ValueError):
        signing.key_id()


def test_a_draft_pack_made_in_the_app_is_unaffected(world):
    pack = make(world)
    assert shown(world, pack)["status"] == "draft"
