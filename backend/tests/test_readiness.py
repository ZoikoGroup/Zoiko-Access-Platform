"""Go-live readiness for a release in a country (GHE-20; DEV-137): attestations bound to the exact
release, independent signers, vetoes that stand, expiry, and a country-pack check done by the
system."""
import datetime as dt
import json

import pytest
from django.db import DatabaseError, transaction

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.readiness.models import Decision, Evidence, GateDecision, Release
from tests.test_packs import call, full, signed
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
G3 = ["signed_build", "security_signoff", "privacy_signoff", "requirements_coverage",
      "recovery_exercised", "operations_staffed"]
G4_SUPPORT = ["legal_authority", "operating_entity"]
G4_SAFETY = ["clinical_partner", "clinical_safety_signoff"]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def make_world():
    w = Platform()
    for subject in ("sam", "tess", *[f"s{i}" for i in range(1, 9)]):
        w.staff(subject, "R11")
    for subject in ("iyer", "c1", "c2"):
        w.staff(subject, "R12")
    return w


def settle_hazards(w, leave=None):
    """Seed the hazard register and accept every hazard (optionally leaving one in `leave`)."""
    from modules.safety import api as safety
    from modules.safety.models import Hazard
    with tenant_transaction(w.ctx):
        safety.ensure_seed("system")
        Hazard.objects.update(status="ACCEPTED")
        if leave:
            first = Hazard.objects.order_by("ref_no").first()
            Hazard.objects.filter(pk=first.pk).update(status=leave)


@pytest.fixture
def world():
    """A platform with its hazard register seeded and every hazard accepted."""
    w = make_world()
    settle_hazards(w)
    return w


@pytest.fixture
def bare():
    """A platform whose hazard register has never been touched."""
    return make_world()


def make(w, ref="rel-2026-11-1", caps=("booking",), country="GB", who="sam", **over):
    body = {"release_ref": ref, "build": "build-2026.11.1", "cell": "DEV-01", "country": country,
            "capabilities": list(caps), **over}
    return call(w, "POST", "platform/readiness", body, who)


def release(w, **kw):
    r = make(w, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["release"]


def attest(w, rel, requirement, who, reference="artefact-0001", days=90, note=""):
    expires = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    return call(w, "POST", f"platform/readiness/{rel['public_id']}/sign", {
        "requirement": requirement, "reference": reference, "expires_on": expires,
        "note": note}, who)


def stop(w, rel, requirement, who="s8", note="Hazard not closed"):
    return call(w, "POST", f"platform/readiness/{rel['public_id']}/veto",
                {"requirement": requirement, "note": note}, who)


def show(w, rel, who="sam"):
    r = call(w, "GET", f"platform/readiness/{rel['public_id']}", who=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["release"]


def sign_g3(w, rel):
    for n, requirement in enumerate(G3, start=1):
        assert attest(w, rel, requirement, f"s{n}").status_code == 200


def sign_g4(w, rel):
    for requirement, who in zip(G4_SUPPORT, ("s7", "s8"), strict=True):
        assert attest(w, rel, requirement, who).status_code == 200
    for requirement, who in zip(G4_SAFETY, ("c1", "c2"), strict=True):
        assert attest(w, rel, requirement, who).status_code == 200


def decision_of(w, rel):
    return show(w, rel)["decision"]


# ------------------------------------------------------------------ creating a release
def test_a_new_release_is_not_ready_and_lists_everything_missing(world):
    rel = release(world)
    assert rel["decision"] == "no_go" and len(rel["tuple_digest"]) == 64
    assert len(rel["blockers"]) == 11                       # six G3, four G4, and the country pack
    assert "g4.country_pack:missing" in rel["blockers"]
    assert sorted(q["status"] for q in rel["requirements"]) == ["missing"] * 11 + ["signed"]


def test_only_support_creates_a_release_and_nobody_else_can_see_them(world):
    assert make(world, who="iyer").status_code == 403
    release(world)
    from tests.test_clinician import Practice
    p = Practice()
    assert p.get("platform/readiness", subject="ops").status_code == 403
    assert p.get("platform/readiness", subject="pat").status_code == 403


@pytest.mark.parametrize("over", [
    {"release_ref": "R1"}, {"release_ref": "Bad Ref"}, {"build": "x"}, {"build": "has space"},
    {"cell": "dev-01"}, {"cell": "X"}, {"country": "gb"}, {"country": "GBR"},
    {"capabilities": []}, {"capabilities": ["teleport"]}, {"capabilities": ["booking", "booking"]},
    {"capabilities": "booking"}, {"capabilities": None},
    {"capabilities": ["booking", "messaging", "referrals", "waiting_list", "provider_directory",
                      "diagnostics", "clinical_ai", "record_exchange", "booking"]},
])
def test_the_tuple_must_be_well_formed(world, over):
    assert make(world, **over).status_code == 422


def test_a_release_name_is_used_once_and_the_fingerprint_follows_the_tuple(world):
    a = release(world, ref="rel-a", caps=("booking", "messaging"))
    b = release(world, ref="rel-b", caps=("messaging", "booking"))
    assert make(world, ref="rel-a").status_code == 409
    assert a["tuple_digest"] != b["tuple_digest"]                       # the name is part of it
    assert a["capabilities"] == b["capabilities"] == ["booking", "messaging"]


# ------------------------------------------------------------------ signing
def test_each_requirement_needs_the_right_kind_of_officer(world):
    rel = release(world)
    assert attest(world, rel, "security_signoff", "c1").status_code == 403     # safety, not support
    assert attest(world, rel, "clinical_safety_signoff", "s1").status_code == 403
    assert attest(world, rel, "clinical_partner", "s2").status_code == 403
    assert attest(world, rel, "security_signoff", "s1").status_code == 200
    assert attest(world, rel, "clinical_safety_signoff", "c1").status_code == 200


def test_nobody_signs_their_own_release_or_more_than_one_requirement(world):
    rel = release(world)
    assert attest(world, rel, "signed_build", "sam").status_code == 403           # the author
    assert attest(world, rel, "signed_build", "s1").status_code == 200
    assert attest(world, rel, "security_signoff", "s1").status_code == 403        # a second one
    assert attest(world, rel, "signed_build", "s1").status_code == 200         # same: harmless


@pytest.mark.parametrize("requirement,reference,expiry", [
    ("nonsense", "artefact-0001", 30), ("signed_build", "x", 30), ("signed_build", "has space", 30),
    ("signed_build", "artefact-0001", 0), ("signed_build", "artefact-0001", -5),
    ("signed_build", "artefact-0001", 400), ("signed_build", "artefact-0001", "soon"),
])
def test_a_signature_needs_a_real_artefact_and_a_near_future_expiry(world, requirement, reference,
                                                                      expiry):
    rel = release(world)
    when = (dt.date.today() + dt.timedelta(days=expiry)).isoformat() if isinstance(expiry, int) \
        else expiry
    r = call(world, "POST", f"platform/readiness/{rel['public_id']}/sign", {
        "requirement": requirement, "reference": reference, "expires_on": when}, "s1")
    assert r.status_code == 422
    assert show(world, rel)["blockers"].count("g3.signed_build:missing") == 1


def test_an_unknown_step_and_an_unknown_release_are_not_found(world):
    rel = release(world)
    assert call(world, "POST", f"platform/readiness/{rel['public_id']}/approve", {}, "s1"
                ).status_code == 404
    assert call(world, "GET", "platform/readiness/nonexistent-id-00000").status_code == 404


# ------------------------------------------------------------------ the decision
def test_signing_every_production_requirement_makes_it_ready_but_not_authorised(world):
    rel = release(world)
    sign_g3(world, rel)
    now = show(world, rel)
    assert now["decision"] == "g3_ready"
    assert set(now["blockers"]) == {"g4.legal_authority:missing", "g4.operating_entity:missing",
                                    "g4.clinical_partner:missing",
                                    "g4.clinical_safety_signoff:missing",
                                    "g4.country_pack:missing"}


def test_country_go_live_also_needs_the_country_requirements_and_a_signed_pack(world):
    rel = release(world)
    sign_g3(world, rel)
    sign_g4(world, rel)
    assert decision_of(world, rel) == "g3_ready"                       # still no signed pack
    assert show(world, rel)["blockers"] == ["g4.country_pack:missing"]
    signed(world, ref="gb-care-navigation", manifest=full())           # booking is switched on
    now = show(world, rel)
    assert now["decision"] == "g4_authorized" and now["blockers"] == []
    pack = next(q for q in now["requirements"] if q["requirement"] == "country_pack")
    assert pack["reference"] == "gb-care-navigation@1" and pack["signer"] == ""


def test_the_pack_must_switch_on_every_capability_of_the_release(world):
    rel = release(world, caps=("booking", "messaging"))
    sign_g3(world, rel)
    sign_g4(world, rel)
    signed(world, ref="gb-booking-only", manifest=full())              # booking, no messaging
    assert decision_of(world, rel) == "g3_ready"
    caps = {"provider_directory": True, "booking": True, "messaging": True}
    signed(world, ref="gb-with-messaging", manifest=full(capabilities=caps))
    assert decision_of(world, rel) == "g4_authorized"


def test_a_pack_for_another_country_or_one_that_is_not_signed_does_not_count(world):
    from tests.test_packs import make as make_pack
    from tests.test_packs import to_review
    rel = release(world, country="GB")
    sign_g3(world, rel)
    sign_g4(world, rel)
    signed(world, ref="de-care-navigation", country="DE", manifest=full())
    to_review(world, ref="gb-in-review", country="GB")
    make_pack(world, ref="gb-just-a-draft", country="GB")
    assert decision_of(world, rel) == "g3_ready"


def test_a_suspended_pack_takes_the_authorisation_away(world):
    from tests.test_packs import step
    rel = release(world)
    sign_g3(world, rel)
    sign_g4(world, rel)
    pack = signed(world, ref="gb-care-navigation", manifest=full())
    assert decision_of(world, rel) == "g4_authorized"
    step(world, pack, "suspend", "iyer", note="Regulator query")
    assert decision_of(world, rel) == "g3_ready"


def test_there_is_no_live_or_pilot_state_and_nothing_deploys(world):
    assert {d.value for d in Decision} == {"no_go", "g3_ready", "g4_authorized"}


# ------------------------------------------------------------------ vetoes
def test_a_veto_blocks_everything_whatever_else_is_signed_and_cannot_be_undone(world):
    rel = release(world)
    sign_g3(world, rel)
    sign_g4(world, rel)
    signed(world, ref="gb-care-navigation", manifest=full())
    assert decision_of(world, rel) == "g4_authorized"
    assert stop(world, rel, "clinical_safety_signoff", "tess").status_code == 200
    now = show(world, rel)
    assert now["decision"] == "no_go" and "g4.clinical_safety_signoff:vetoed" in now["blockers"]
    assert attest(world, rel, "clinical_safety_signoff", "iyer").status_code == 409  # new release
    assert stop(world, rel, "clinical_safety_signoff", "tess").status_code == 200       # harmless


def test_a_veto_needs_a_reason_a_known_requirement_and_staff_of_the_right_kind(world):
    rel = release(world)
    assert stop(world, rel, "signed_build", note="").status_code == 422
    assert stop(world, rel, "nonsense").status_code == 422
    from tests.test_clinician import Practice
    p = Practice()
    assert p.post(f"platform/readiness/{rel['public_id']}/veto",
                  {"requirement": "signed_build", "note": "No"}, subject="ops").status_code == 403
    assert stop(world, rel, "signed_build", "iyer").status_code == 200              # safety may


# ------------------------------------------------------------------ expiry and binding
def test_an_attestation_stops_counting_when_it_expires_and_the_decision_is_recorded(
        world, monkeypatch):
    from modules.readiness import services
    rel = release(world)
    sign_g3(world, rel)
    assert decision_of(world, rel) == "g3_ready"
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=120)
    monkeypatch.setattr(services, "utc_now", lambda clock=None: later)
    live = show(world, rel)
    assert live["decision"] == "no_go" and live["recorded_decision"] == "g3_ready"
    assert "g3.signed_build:expired" in live["blockers"]
    assert show(world, rel)["recorded_decision"] == "g3_ready"          # reading records nothing
    r = call(world, "POST", f"platform/readiness/{rel['public_id']}/evaluate", {}, "sam")
    assert r.status_code == 200 and json.loads(r.content)["release"]["recorded_decision"] == "no_go"
    assert show(world, rel)["history"][-1]["trigger"] == "re-evaluated"


def test_evidence_given_for_a_different_fingerprint_counts_for_nothing(world):
    rel = release(world)
    with tenant_transaction(world.ctx):
        row = Release.objects.get(public_id=rel["public_id"])
        Evidence.objects.create(
            release=row, requirement="signed_build", outcome="signed", signer_ref="x" * 22,
            signer_role="R11", reference="artefact-0001", tuple_digest="0" * 64,
            expires_on=dt.date.today() + dt.timedelta(days=30), cell_id=row.cell_id,
            tenant_id=row.tenant_id)
    assert "g3.signed_build:missing" in show(world, rel)["blockers"]


def test_one_releases_signatures_never_count_for_another(world):
    first = release(world, ref="rel-first")
    second = release(world, ref="rel-second")
    sign_g3(world, first)
    assert decision_of(world, first) == "g3_ready" and decision_of(world, second) == "no_go"


# ------------------------------------------------------------------ the record
def test_the_decision_history_gets_a_row_only_when_the_decision_or_its_blockers_change(world):
    rel = release(world)
    sign_g3(world, rel)
    history = show(world, rel)["history"]
    assert [h["decision"] for h in history] == ["no_go", "no_go", "no_go", "no_go", "no_go",
                                                "no_go", "g3_ready"]
    assert history[0]["trigger"] == "created"
    assert history[-1]["trigger"] == "signed:operations_staffed"
    again = call(world, "POST", f"platform/readiness/{rel['public_id']}/evaluate", {}, "sam")
    assert again.status_code == 200 and len(show(world, rel)["history"]) == len(history)


def test_evidence_and_decisions_cannot_be_changed_or_removed(world):
    rel = release(world)
    attest(world, rel, "signed_build", "s1")
    with tenant_transaction(world.ctx):
        for model in (Evidence, GateDecision):
            with pytest.raises(DatabaseError), transaction.atomic():
                model.objects.all().update(note="x") if model is Evidence else \
                    model.objects.all().update(trigger="x")
            with pytest.raises(DatabaseError), transaction.atomic():
                model.objects.all().delete()


def test_every_step_is_audited_without_free_text(world):
    rel = release(world)
    attest(world, rel, "signed_build", "s1", note="Private note about the build")
    stop(world, rel, "security_signoff", "c1", note="Private reason for stopping")
    with tenant_transaction(world.ctx):
        events = list(AuditEvent.objects.filter(action__startswith="readiness.")
                      .order_by("chain_seq"))
    steps = [e.action for e in events if not e.action.startswith("readiness.decision_")]
    assert steps == ["readiness.release_created", "readiness.signed", "readiness.vetoed"]
    assert any(e.action == "readiness.decision_no_go" for e in events)
    assert "Private" not in json.dumps([e.details for e in events])


# --------------------------------------------------------- the hazard register (GHE-08 AT42)
def test_an_empty_hazard_register_holds_a_release_back_even_with_everything_signed(bare):
    rel = release(bare)
    sign_g3(bare, rel)
    now = show(bare, rel)
    assert now["decision"] == "no_go" and "g3.hazard_register:missing" in now["blockers"]
    row = next(q for q in now["requirements"] if q["requirement"] == "hazard_register")
    assert row["reference"] == "no hazards recorded" and row["signer"] == ""


@pytest.mark.parametrize("status", ["OPEN", "VERIFIED", "REJECTED"])
def test_a_hazard_that_is_not_accepted_or_retired_holds_a_release_back(bare, status):
    rel = release(bare)
    sign_g3(bare, rel)
    settle_hazards(bare, leave=status)
    now = show(bare, rel)
    assert now["decision"] == "no_go" and "g3.hazard_register:missing" in now["blockers"]
    row = next(q for q in now["requirements"] if q["requirement"] == "hazard_register")
    assert "resolved" in row["reference"] and not row["reference"].startswith("0 of")


def test_accepted_and_retired_hazards_let_it_through_and_resolving_the_last_one_releases_it(bare):
    from modules.safety.models import Hazard
    rel = release(bare)
    sign_g3(bare, rel)
    settle_hazards(bare, leave="VERIFIED")
    assert decision_of(bare, rel) == "no_go"
    with tenant_transaction(bare.ctx):
        Hazard.objects.filter(status="VERIFIED").update(status="RETIRED")
    assert decision_of(bare, rel) == "g3_ready"


def test_the_hazard_requirement_is_checked_by_the_system_and_cannot_be_signed(world):
    rel = release(world)
    assert attest(world, rel, "hazard_register", "s1").status_code == 422
    assert stop(world, rel, "hazard_register").status_code == 422
