"""Country capability packs (GHE-04; DEV-128): drafted and reviewed by Zoiko's own staff, bound to
a content fingerprint, approved by two different people. Nothing here makes a pack live."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.jurisdiction.manifest import fingerprint, normalize
from tests.test_api import API
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    w = Platform()
    w.staff("sam", "R11")          # author and support
    w.staff("tess", "R11")         # a second support officer
    w.staff("iyer", "R12")         # a safety officer
    w.staff("noor", "R12")         # a second safety officer
    return w


def call(w, method, path, body=None, who="sam"):
    headers = w.header(w.tenant_public_id, who)
    if method == "POST":
        return w.client.post(API + path, json.dumps(body or {}),
                             content_type="application/json", **headers)
    return w.client.get(API + path, **headers)


def full(days=30, **over):
    expires = (dt.date.today() + dt.timedelta(days=365)).isoformat()
    manifest = {
        "settings": {"data_request_days": days, "emergency_number": "112",
                     "default_language": "en-GB", "time_zone": "Europe/London"},
        "capabilities": {"provider_directory": True, "booking": True},
        "evidence": {"legal": "PROVIDED", "dpia": "PROVIDED", "cyber": "PROVIDED",
                     "clinical": "NOT_APPLICABLE_REVIEW_NEEDED"},
        "validity": {"expires_on": expires},
    }
    manifest.update(over)
    return manifest


def make(w, ref="gb-care-navigation", country="GB", manifest=None, who="sam"):
    r = call(w, "POST", "platform/packs",
             {"pack_ref": ref, "country": country, "service": "care_navigation",
              "manifest": manifest if manifest is not None else full()}, who)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["pack"]


def step(w, pack, name, who="sam", **body):
    return call(w, "POST", f"platform/packs/{pack['id']}/{name}", body, who)


def packs(w, who="sam"):
    r = call(w, "GET", "platform/packs", who=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["packs"]


def to_review(w, **kw):
    pack = make(w, **kw)
    assert step(w, pack, "validate").status_code == 200
    assert step(w, pack, "submit").status_code == 200
    return pack


def status_of(w, pack):
    return next(p for p in packs(w) if p["id"] == pack["id"])["status"]


# ------------------------------------------------------------------ the content
def test_a_draft_is_created_with_a_fingerprint_and_nothing_enabled_by_default(world):
    pack = make(world, manifest={})
    assert pack["status"] == "draft" and pack["revision"] == 1 and len(pack["content_hash"]) == 64
    assert not any(pack["manifest"]["capabilities"].values())
    assert pack["manifest"]["evidence"] == {"legal": "PENDING", "dpia": "PENDING",
                                            "cyber": "PENDING", "clinical": "PENDING"}


def test_the_fingerprint_changes_with_any_change_to_the_content(world):
    a = normalize(full(days=30))
    b = normalize(full(days=31))
    assert fingerprint("x", "GB", "s", 1, a) != fingerprint("x", "GB", "s", 1, b)
    again = fingerprint("x", "GB", "s", 1, normalize(full(days=30)))
    assert fingerprint("x", "GB", "s", 1, a) == again


@pytest.mark.parametrize("bad", [
    {"settings": {"data_request_days": 0}}, {"settings": {"data_request_days": 400}},
    {"settings": {"data_request_days": True}}, {"settings": {"data_request_days": "30"}},
    {"settings": {"emergency_number": "call us"}}, {"settings": {"time_zone": "Mars/Base"}},
    {"settings": {"default_language": "English"}}, {"settings": {"surprise": 1}},
    {"capabilities": {"clinical_ai": True}}, {"capabilities": {"diagnostics": True}},
    {"capabilities": {"record_exchange": True}}, {"capabilities": {"teleport": True}},
    {"capabilities": {"booking": "yes"}}, {"evidence": {"legal": "DONE"}},
    {"evidence": {"tax": "PROVIDED"}}, {"validity": {"expires_on": "next year"}},
    {"unknown_section": {}},
])
def test_anything_unknown_or_not_built_is_refused_not_ignored(world, bad):
    r = call(world, "POST", "platform/packs", {
        "pack_ref": "gb-care", "country": "GB", "service": "care_navigation", "manifest": bad})
    assert r.status_code == 422


@pytest.mark.parametrize("fields", [
    {"pack_ref": "GB care", "country": "GB"}, {"pack_ref": "gb-care", "country": "gb"},
    {"pack_ref": "gb-care", "country": "GBR"}, {"pack_ref": "x", "country": "GB"},
])
def test_the_name_and_the_country_code_must_be_well_formed(world, fields):
    r = call(world, "POST", "platform/packs",
             {"service": "care_navigation", "manifest": {}, **fields})
    assert r.status_code == 422


def test_a_name_can_be_used_once(world):
    make(world)
    r = call(world, "POST", "platform/packs", {
        "pack_ref": "gb-care-navigation", "country": "GB", "service": "care_navigation",
        "manifest": {}})
    assert r.status_code == 409


# ------------------------------------------------------------------ who may do what
def test_only_zoiko_support_can_start_a_pack_and_safety_can_only_read(world):
    body = {"pack_ref": "gb-care", "country": "GB", "service": "care_navigation", "manifest": {}}
    assert call(world, "POST", "platform/packs", body, "iyer").status_code == 403
    make(world)
    assert len(packs(world, "iyer")) == 1


def test_a_customers_staff_and_patients_cannot_see_packs(world):
    from tests.test_clinician import Practice
    p = Practice()
    make(world)
    assert p.get("platform/packs", subject="ops").status_code == 403
    assert p.get("platform/packs", subject="pat").status_code == 403


def test_a_draft_can_be_edited_and_the_fingerprint_follows(world):
    pack = make(world, manifest={})
    r = step(world, pack, "edit", manifest=full(days=45))
    edited = json.loads(r.content)["pack"]
    assert r.status_code == 200 and edited["content_hash"] != pack["content_hash"]
    assert edited["manifest"]["settings"]["data_request_days"] == 45


# ------------------------------------------------------------------ the review
def test_it_cannot_be_checked_while_anything_required_is_missing(world):
    pack = make(world, manifest=full(evidence={"legal": "PROVIDED", "dpia": "PENDING",
                                               "cyber": "PROVIDED", "clinical": "PENDING"},
                                     validity={"expires_on": None}))
    listed = next(p for p in packs(world) if p["id"] == pack["id"])
    assert set(listed["missing"]) == {"evidence.dpia", "evidence.clinical", "validity.expires_on"}
    assert step(world, pack, "validate").status_code == 422
    assert status_of(world, pack) == "draft"


def test_the_steps_must_be_taken_in_order(world):
    pack = make(world)
    assert step(world, pack, "submit").status_code == 409                # not checked yet
    assert step(world, pack, "approve-legal", "tess").status_code == 409  # not with the reviewers
    step(world, pack, "validate")
    assert step(world, pack, "validate").status_code == 409
    assert step(world, pack, "approve-clinical", "iyer").status_code == 409
    assert step(world, pack, "edit", manifest=full()).status_code == 409  # no longer a draft


def test_two_different_people_approve_and_only_then_it_is_signed(world):
    pack = to_review(world)
    assert step(world, pack, "approve-legal", "tess").status_code == 200
    assert status_of(world, pack) == "expert_review"
    r = step(world, pack, "approve-clinical", "iyer")
    signed = json.loads(r.content)["pack"]
    assert signed["status"] == "signed" and set(signed["approvals"]) == {"legal", "clinical"}


def test_nobody_approves_their_own_work(world):
    pack = to_review(world)
    assert step(world, pack, "approve-legal", "sam").status_code == 403    # the author
    assert status_of(world, pack) == "expert_review"


def test_each_approval_needs_the_right_kind_of_staff(world):
    pack = to_review(world)
    assert step(world, pack, "approve-legal", "iyer").status_code == 403     # safety, not support
    assert step(world, pack, "approve-clinical", "tess").status_code == 403  # support, not safety
    assert step(world, pack, "approve-sideways", "tess").status_code == 404


def test_repeating_an_approval_changes_nothing(world):
    pack = to_review(world)
    assert step(world, pack, "approve-legal", "tess").status_code == 200
    assert step(world, pack, "approve-legal", "tess").status_code == 200
    assert status_of(world, pack) == "expert_review"


def test_sending_it_back_clears_the_approvals_and_needs_a_reason(world):
    pack = to_review(world)
    step(world, pack, "approve-legal", "tess")
    assert step(world, pack, "reject", "iyer").status_code == 422           # no reason
    r = step(world, pack, "reject", "iyer", note="Retention period is wrong")
    back = json.loads(r.content)["pack"]
    assert back["status"] == "draft" and back["approvals"] == {}
    assert back["note"] == "Retention period is wrong"


def test_changing_a_pack_after_a_rejection_means_a_fresh_set_of_approvals(world):
    pack = to_review(world)
    step(world, pack, "approve-legal", "tess")
    step(world, pack, "reject", "tess", note="Fix the emergency number")
    step(world, pack, "edit", manifest=full(days=14))
    step(world, pack, "validate")
    step(world, pack, "submit")
    assert step(world, pack, "approve-clinical", "iyer").status_code == 200
    assert status_of(world, pack) == "expert_review"                      # legal approval is gone


# ------------------------------------------------------------------ after signing
def signed(world, **kw):
    pack = to_review(world, **kw)
    step(world, pack, "approve-legal", "tess")
    step(world, pack, "approve-clinical", "iyer")
    assert status_of(world, pack) == "signed"
    return pack


def test_either_kind_of_reviewer_can_suspend_but_it_needs_a_reason(world):
    pack = signed(world)
    assert step(world, pack, "suspend", "iyer").status_code == 422
    assert step(world, pack, "suspend", "iyer", note="Regulator query").status_code == 200
    assert step(world, pack, "suspend", "tess", note="Again").status_code == 200   # harmless
    assert status_of(world, pack) == "suspended"


def test_a_suspended_pack_comes_back_only_as_a_new_draft_revision(world):
    pack = signed(world)
    step(world, pack, "suspend", "tess", note="Evidence expired")
    assert step(world, pack, "approve-legal", "tess").status_code == 409     # not a way back
    r = step(world, pack, "revalidate")
    newer = json.loads(r.content)["pack"]
    assert newer["status"] == "draft" and newer["revision"] == 2 and newer["approvals"] == {}
    assert status_of(world, pack) == "retired"


def test_only_a_signed_or_suspended_pack_can_be_retired(world):
    pack = make(world)
    assert step(world, pack, "retire").status_code == 409
    done = signed(world, ref="gb-other")
    assert step(world, done, "retire").status_code == 200
    assert step(world, done, "retire").status_code == 200                    # harmless
    assert step(world, done, "suspend", "tess", note="Too late").status_code == 409


def test_a_signed_pack_past_its_expiry_is_shown_as_expired(world):
    pack = signed(world)
    with tenant_transaction(world.ctx):
        from modules.jurisdiction.models import CountryPack
        row = CountryPack.objects.get(public_id=pack["id"])
        row.manifest["validity"]["expires_on"] = "2020-01-01"
        row.save(update_fields=["manifest"])
    assert next(p for p in packs(world) if p["id"] == pack["id"])["expired"] is True


def test_there_is_no_way_to_make_a_pack_active(world):
    pack = signed(world)
    for name in ("activate", "stage", "go-live", "active"):
        assert step(world, pack, name).status_code == 404
    from modules.jurisdiction.models import PackStatus
    assert "active" not in PackStatus.values


# ------------------------------------------------------------------ the record
def test_every_step_is_audited(world):
    pack = signed(world)
    with tenant_transaction(world.ctx):
        found = list(AuditEvent.objects.filter(object_ref=pack["id"]).order_by("chain_seq"))
    assert [e.action for e in found] == [
        "jurisdiction.created", "jurisdiction.validated", "jurisdiction.submitted",
        "jurisdiction.approved_legal", "jurisdiction.approved_clinical", "jurisdiction.signed"]
