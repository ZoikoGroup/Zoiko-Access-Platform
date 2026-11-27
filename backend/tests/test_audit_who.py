"""The audit record also says who (kind, session), why (purpose), and under which rules and build
(GHE-10 audit_event), and all of it is covered by the tamper-evident hash. DEV-114."""
import hashlib

import pytest

from foundation.cell_context import tenant_transaction
from foundation.request_context import reset_audit_extra, set_audit_extra
from modules.evidence.audit import compute_hash
from modules.evidence.models import AuditEvent
from modules.policy.models import AccessDecision
from modules.policy.rules import POLICY_DIGEST
from tests.test_api import book
from tests.test_audit import check, log, tamper
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.add_times(practice.doc)
    return practice


def events_by(p, actor, action):
    with tenant_transaction(p.clinic.ctx):
        return list(AuditEvent.objects.filter(actor_ref=actor, action=action).order_by("chain_seq"))


# ------------------------------------------------------------------ what a request adds
def test_an_event_caused_by_a_signed_in_person_says_who_why_and_which_build(p):
    book(p, p.slot_of("doc"), subject="pat")
    (event,) = events_by(p, p.patient_actor, "booking.request")
    assert event.actor_kind == "patient" and event.purpose_code == "SELF_SERVICE"
    assert event.source_service == "backend" and event.build_digest
    assert event.hash_version == 2 and len(event.session_ref) == 16


def test_the_session_is_a_fingerprint_and_the_token_is_never_stored(p):
    book(p, p.slot_of("doc"), subject="pat")
    (event,) = events_by(p, p.patient_actor, "booking.request")
    token = p.header("pat")["HTTP_AUTHORIZATION"]
    assert event.session_ref == hashlib.sha256(token.encode()).hexdigest()[:16]
    with tenant_transaction(p.clinic.ctx):
        dump = " ".join(
            f"{e.session_ref}{e.details}{e.build_digest}" for e in AuditEvent.objects.all())
    assert token not in dump and token.split(" ")[1] not in dump


def test_a_stated_purpose_is_what_the_record_keeps(p):
    made = book(p, p.slot_of("doc"), subject="pat").json()["booking"]["id"]
    p.post(f"bookings/{made}/confirm", subject="ops")
    p.get("clinician/patients", subject="doc", HTTP_X_ZOIKO_PURPOSE="CONTINUITY_OF_CARE")
    with tenant_transaction(p.clinic.ctx):
        purposes = set(AccessDecision.objects.filter(actor_ref=p.doc).values_list(
            "purpose_code", flat=True))
        audited = set(AuditEvent.objects.filter(
            actor_ref=p.doc, action="access.decide").values_list("purpose_code", flat=True))
    assert "CONTINUITY_OF_CARE" in purposes and "CONTINUITY_OF_CARE" in audited


def test_an_access_decision_names_the_rules_that_made_it(p):
    p.get("clinician/patients", subject="doc")
    with tenant_transaction(p.clinic.ctx):
        decisions = list(AuditEvent.objects.filter(action="access.decide"))
    assert decisions and {d.policy_digest for d in decisions} == {POLICY_DIGEST}
    assert len(POLICY_DIGEST) == 64


def test_an_event_with_no_signed_in_person_gets_no_person_labels(ctx_a):
    (event,) = log(ctx_a, actor_ref="system")
    assert (event.actor_kind, event.purpose_code, event.session_ref) == ("", "", "")
    assert event.source_service == "backend" and event.hash_version == 2


def test_someone_elses_event_is_never_labelled_with_the_request_persons_details(ctx_a):
    who = {"actor_ref": "act_1", "actor_kind": "clinician", "purpose_code": "DIRECT_CARE",
           "session_ref": "abc123"}
    token = set_audit_extra(who)
    try:
        (mine,) = log(ctx_a, actor_ref="act_1")
        (other,) = log(ctx_a, actor_ref="act_2")
    finally:
        reset_audit_extra(token)
    assert (mine.actor_kind, mine.session_ref) == ("clinician", "abc123")
    assert mine.purpose_code == "DIRECT_CARE"
    assert (other.actor_kind, other.purpose_code, other.session_ref) == ("", "", "")


# ------------------------------------------------------------------ covered by the hash
def test_the_new_fields_are_covered_by_the_tamper_evident_hash(ctx_a):
    log(ctx_a, 3, purpose_code="DIRECT_CARE", policy_digest="a" * 64)
    assert check(ctx_a).ok
    tamper("UPDATE evidence_auditevent SET purpose_code = 'OPERATIONS' WHERE chain_seq = 2")
    report = check(ctx_a)
    assert (report.ok, report.first_bad_seq, report.problem) == (
        False, 2, "row content was changed")


def test_changing_the_policy_digest_or_build_is_detected_too(ctx_a):
    log(ctx_a, 2, policy_digest="b" * 64)
    tamper("UPDATE evidence_auditevent SET policy_digest = %s WHERE chain_seq = 1", ["c" * 64])
    assert check(ctx_a).first_bad_seq == 1
    tamper("UPDATE evidence_auditevent SET policy_digest = %s WHERE chain_seq = 1", ["b" * 64])
    tamper("UPDATE evidence_auditevent SET build_digest = 'forged' WHERE chain_seq = 2")
    assert check(ctx_a).first_bad_seq == 2


def test_a_row_sealed_before_these_fields_existed_still_checks(ctx_a):
    log(ctx_a, 1)                                              # a version 2 row first
    with tenant_transaction(ctx_a):
        from foundation.timeutil import utc_now
        from modules.evidence.models import AuditOutcome
        last = AuditEvent.objects.get(chain_seq=1)
        old = AuditEvent(
            chain_seq=2, occurred_at=utc_now(), actor_ref="act_1", action="booking.confirm",
            object_type="booking", object_ref="bk_1", outcome=AuditOutcome.ALLOWED,
            request_id="req-old-layout", details={}, prev_hash=last.event_hash, hash_version=1)
        old.cell_id, old.tenant_id = ctx_a.cell_id, ctx_a.tenant_id
        old.event_hash = compute_hash(old)
        old.save()
    report = check(ctx_a)
    assert report.ok and report.checked == 2


def test_a_version_one_row_is_hashed_without_the_new_fields(ctx_a):
    with tenant_transaction(ctx_a):
        from foundation.timeutil import utc_now
        from modules.evidence.models import AuditOutcome
        base = dict(chain_seq=1, occurred_at=utc_now(), actor_ref="a", action="x.y",
                    object_type="o", object_ref="r", outcome=AuditOutcome.ALLOWED,
                    request_id="req-1", details={}, prev_hash="0" * 64)
        v1 = AuditEvent(hash_version=1, build_digest="one", **base)
        v1b = AuditEvent(hash_version=1, build_digest="two", **base)
        v2 = AuditEvent(hash_version=2, build_digest="one", **base)
        v2b = AuditEvent(hash_version=2, build_digest="two", **base)
        for e in (v1, v1b, v2, v2b):
            e.cell_id, e.tenant_id = ctx_a.cell_id, ctx_a.tenant_id
        assert compute_hash(v1) == compute_hash(v1b)          # the old layout ignores the field
        assert compute_hash(v2) != compute_hash(v2b)          # the new layout covers it
