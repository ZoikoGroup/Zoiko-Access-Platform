"""A customer's country and the data-request deadline a signed country pack gives it (DEV-133):
support sets the country, the deadline is stamped on a request when it is filed, and it is never
recomputed afterwards."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock
from modules.evidence.models import AuditEvent
from modules.jurisdiction import api as jurisdiction
from modules.jurisdiction.models import CountryPack
from modules.trust import api as trust
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_packs import full, signed, step
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


def set_country(world, p, country, who="sam"):
    return world.client.post(
        API + f"platform/customers/{p.clinic.tenant_public_id}/country",
        json.dumps({"country": country}), content_type="application/json",
        **world.header(world.tenant_public_id, who))


def file_request(p, kind="access"):
    r = p.post("rights", {"type": kind, "note": ""}, subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["request"]


def mine(p):
    return json.loads(p.get("rights", subject="pat").content)["requests"]


def hours_until(iso):
    return (dt.datetime.fromisoformat(iso) - dt.datetime.now(dt.UTC)).total_seconds() / 3600


# ------------------------------------------------------------------ setting the country
def test_support_records_a_customers_country_and_the_overview_shows_it(world, p):
    r = set_country(world, p, "GB")
    assert r.status_code == 200 and json.loads(r.content)["customer"]["country"] == "GB"
    overview = json.loads(world.overview("sam").content)["tenants"]
    mine_row = next(t for t in overview if t["id"] == p.clinic.tenant_public_id)
    assert mine_row["country"] == "GB"


@pytest.mark.parametrize("bad", ["gb", "GBR", "G1", "G", " GB", "GB ", 5, None, ["GB"], {"a": 1}])
def test_only_a_two_letter_iso_code_or_nothing_is_accepted(world, p, bad):
    assert set_country(world, p, bad).status_code == 422


def test_an_empty_country_clears_it(world, p):
    set_country(world, p, "GB")
    assert set_country(world, p, "").status_code == 200
    overview = json.loads(world.overview("sam").content)["tenants"]
    assert next(t for t in overview if t["id"] == p.clinic.tenant_public_id)["country"] == ""


def test_only_zoiko_support_can_set_it(world, p):
    assert set_country(world, p, "GB", who="iyer").status_code == 403       # safety: read only here
    assert p.post(f"platform/customers/{p.clinic.tenant_public_id}/country",
                  {"country": "GB"}, subject="boss").status_code == 403     # a customer's own admin
    assert p.post(f"platform/customers/{p.clinic.tenant_public_id}/country",
                  {"country": "GB"}, subject="pat").status_code == 403


def test_the_platforms_own_tenant_and_unknown_customers_are_refused(world, p):
    r = world.client.post(
        API + f"platform/customers/{world.tenant_public_id}/country",
        json.dumps({"country": "GB"}), content_type="application/json",
        **world.header(world.tenant_public_id, "sam"))
    assert r.status_code == 422
    r = world.client.post(
        API + "platform/customers/nonexistent-id-00000/country",
        json.dumps({"country": "GB"}), content_type="application/json",
        **world.header(world.tenant_public_id, "sam"))
    assert r.status_code == 404


def test_a_change_is_audited_in_both_places_and_repeating_it_adds_nothing(world, p):
    set_country(world, p, "GB")
    set_country(world, p, "GB")
    with tenant_transaction(p.clinic.ctx):
        mine_rows = AuditEvent.objects.filter(action="tenant.country").count()
    with tenant_transaction(world.ctx):
        theirs = AuditEvent.objects.filter(action="platform.customer_country").count()
    assert mine_rows == 1 and theirs == 1


# ------------------------------------------------------------------ the deadline on a request
def test_with_no_country_or_no_signed_pack_a_request_has_no_deadline(world, p):
    assert file_request(p)["due_at"] is None
    set_country(world, p, "GB")
    assert file_request(p, "correction")["due_at"] is None                  # a country but no pack


def test_a_pack_that_is_not_signed_gives_no_deadline(world, p):
    set_country(world, p, "GB")
    from tests.test_packs import make, to_review
    to_review(world, ref="gb-in-review")
    make(world, ref="gb-just-a-draft")
    assert file_request(p)["due_at"] is None


def test_a_signed_pack_for_the_customers_country_sets_the_due_date_and_names_its_source(world, p):
    signed(world, ref="gb-care-navigation", manifest=full(days=30))
    set_country(world, p, "GB")
    made = file_request(p)
    assert 29 * 24 < hours_until(made["due_at"]) < 30 * 24 + 1
    assert made["due_basis"] == "gb-care-navigation@1"
    assert mine(p)[0]["due_at"] == made["due_at"]
    staff = json.loads(p.get("staff/rights", subject="ops").content)["requests"]
    assert staff[0]["due_at"] == made["due_at"] and staff[0]["due_basis"] == "gb-care-navigation@1"


def test_a_pack_for_another_country_does_not_apply(world, p):
    signed(world, ref="gb-care-navigation", manifest=full(days=30))
    set_country(world, p, "DE")
    assert file_request(p)["due_at"] is None


def test_the_strictest_of_several_signed_packs_wins(world, p):
    signed(world, ref="gb-long", manifest=full(days=30))
    signed(world, ref="gb-short", manifest=full(days=14))
    set_country(world, p, "GB")
    made = file_request(p)
    assert 13 * 24 < hours_until(made["due_at"]) < 14 * 24 + 1
    assert made["due_basis"] == "gb-short@1"


def test_a_suspended_pack_stops_giving_new_deadlines_but_old_ones_stand(world, p):
    pack = signed(world, ref="gb-care-navigation", manifest=full(days=30))
    set_country(world, p, "GB")
    first = file_request(p)
    step(world, pack, "suspend", "iyer", note="Regulator query")
    second = file_request(p, "correction")
    assert second["due_at"] is None
    assert next(r for r in mine(p) if r["id"] == first["id"])["due_at"] == first["due_at"]


def test_an_expired_pack_gives_no_deadline(world, p):
    pack = signed(world, ref="gb-care-navigation", manifest=full(days=30))
    with tenant_transaction(world.ctx):
        row = CountryPack.objects.get(public_id=pack["id"])
        row.manifest["validity"]["expires_on"] = "2020-01-01"
        row.save(update_fields=["manifest"])
    set_country(world, p, "GB")
    assert file_request(p)["due_at"] is None


def test_a_later_revision_never_changes_a_deadline_already_given(world, p):
    pack = signed(world, ref="gb-care-navigation", manifest=full(days=30))
    set_country(world, p, "GB")
    first = file_request(p)
    step(world, pack, "suspend", "tess", note="Revising the deadline")
    revised = json.loads(step(world, pack, "revalidate").content)["pack"]
    step(world, revised, "edit", manifest=full(days=7))
    step(world, revised, "validate")
    step(world, revised, "submit")
    step(world, revised, "approve-legal", "tess")
    step(world, revised, "approve-clinical", "iyer")
    second = file_request(p, "correction")
    assert 6 * 24 < hours_until(second["due_at"]) < 7 * 24 + 1
    assert second["due_basis"] == "gb-care-navigation@2"
    assert next(r for r in mine(p) if r["id"] == first["id"])["due_at"] == first["due_at"]


def test_clearing_the_country_stops_new_deadlines(world, p):
    signed(world, ref="gb-care-navigation", manifest=full(days=30))
    set_country(world, p, "GB")
    assert file_request(p)["due_at"] is not None
    set_country(world, p, "")
    assert file_request(p, "correction")["due_at"] is None


def test_the_lookup_itself_only_counts_signed_unexpired_packs(world):
    signed(world, ref="gb-care-navigation", manifest=full(days=30))
    with tenant_transaction(world.ctx):
        assert jurisdiction.data_request_days("GB") == (30, "gb-care-navigation@1")
        assert jurisdiction.data_request_days("DE") is None
        assert jurisdiction.data_request_days("") is None
        far = FixedClock(dt.datetime.now(dt.UTC) + dt.timedelta(days=800))
        assert jurisdiction.data_request_days("GB", far) is None             # past its review date
