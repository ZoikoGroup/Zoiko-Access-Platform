import datetime as dt

import pytest
from django.db import DatabaseError, connection, transaction

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, FixedClock
from modules.ai import api as ai
from modules.ai.models import AiActionApproval, AiPermitEvent, AiRequest, AiResponseEvidence
from modules.evidence.models import AuditEvent
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

D = "a" * 64
NOW = dt.datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
LATER = NOW + dt.timedelta(days=90)
CLOCK = FixedClock(NOW)
PATH = ["QUALIFICATION", "DESIGN_CONTROL", "VALIDATION", "SANDBOX", "SHADOW", "RELEASE_REVIEW"]


@pytest.fixture(autouse=True)
def builtin():
    ai.register_builtin()


def propose(**over):
    args = {
        "feature_id": ai.REPLY_DRAFT,
        "purpose": "Draft a reply for staff to review",
        "lane": "A",
        "level": "A1",
        "model_digest": D,
        "prompt_digest": "b" * 64,
        "created_by_ref": "creator",
        **over,
    }
    return ai.create_permit(**args)


def walk(permit, *, upto="RELEASE_REVIEW"):
    for state in PATH:
        ai.advance(permit["id"], state, actor_ref="creator", clock=CLOCK)
        if state == upto:
            break


def authorize(permit, **over):
    args = {
        "actor_ref": "lead",
        "approver_refs": ["legal", "clinical"],
        "expiry": LATER,
        "clock": CLOCK,
        **over,
    }
    return ai.advance(permit["id"], "AUTHORIZED", **args)


def live(ctx, **over):
    """A permit taken all the way to AUTHORIZED."""
    with tenant_transaction(ctx):
        p = propose(**over)
        walk(p)
        authorize(p)
    return p


def code(excinfo):
    return excinfo.value.code


# ------------------------------------------------------------------ default deny
def test_nothing_runs_without_a_permit(ctx_a):
    with tenant_transaction(ctx_a), pytest.raises(ZoikoError) as e:
        ai.run(
            ai.REPLY_DRAFT, actor_ref="staff", purpose_code="reply", input_text="hello", clock=CLOCK
        )
    assert code(e) == ErrorCode.POLICY_DENIED


def test_a_permit_that_is_only_proposed_does_not_let_anything_run(ctx_a):
    with tenant_transaction(ctx_a):
        propose()
        with pytest.raises(ZoikoError) as e:
            ai.run(
                ai.REPLY_DRAFT,
                actor_ref="staff",
                purpose_code="reply",
                input_text="hi",
                clock=CLOCK,
            )
    assert code(e) == ErrorCode.POLICY_DENIED


def test_an_authorized_feature_runs_and_the_answer_goes_to_a_human(ctx_a):
    live(ctx_a)
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="My knee hurts",
            clock=CLOCK,
        )
    assert r["decision"] == "HUMAN_REVIEW" and r["mode"] == "live" and r["output"]
    assert "knee" not in r["output"]  # the template never repeats what it was given


def test_an_expired_permit_stops_working(ctx_a):
    live(ctx_a)
    with tenant_transaction(ctx_a), pytest.raises(ZoikoError) as e:
        ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            clock=FixedClock(LATER + dt.timedelta(seconds=1)),
        )
    assert code(e) == ErrorCode.POLICY_DENIED


def test_suspending_stops_it_at_once_and_it_cannot_just_restart(ctx_a):
    p = live(ctx_a)
    with tenant_transaction(ctx_a):
        ai.advance(p["id"], "SUSPENDED", actor_ref="sre", reason="wrong answers seen", clock=CLOCK)
        with pytest.raises(ZoikoError) as e:
            ai.run(
                ai.REPLY_DRAFT,
                actor_ref="staff",
                purpose_code="reply",
                input_text="hi",
                clock=CLOCK,
            )
        assert code(e) == ErrorCode.POLICY_DENIED
        with pytest.raises(ZoikoError) as e2:  # no jump from SUSPENDED to AUTHORIZED
            authorize(p)
        assert code(e2) == ErrorCode.CONFLICT_VERSION


def test_other_tenants_permits_do_not_count(ctx_a, ctx_b):
    live(ctx_a)
    with tenant_transaction(ctx_b), pytest.raises(ZoikoError) as e:
        ai.run(
            ai.REPLY_DRAFT, actor_ref="staff", purpose_code="reply", input_text="hi", clock=CLOCK
        )
    assert code(e) == ErrorCode.POLICY_DENIED


# ------------------------------------------------------------------ the path to authorization
def test_steps_cannot_be_skipped(ctx_a):
    with tenant_transaction(ctx_a):
        p = propose()
        for target in ("SHADOW", "AUTHORIZED", "SUSPENDED"):
            with pytest.raises(ZoikoError) as e:
                ai.advance(p["id"], target, actor_ref="creator", reason="x", clock=CLOCK)
            assert code(e) == ErrorCode.CONFLICT_VERSION


def test_authorizing_needs_two_other_people_and_a_sensible_expiry(ctx_a):
    with tenant_transaction(ctx_a):
        p = propose()
        walk(p)
        for bad in (
            {"approver_refs": ["legal"]},  # only one signer
            {"approver_refs": ["legal", "legal"]},  # the same person twice
            {"approver_refs": ["legal", "creator"]},  # the creator signing off their own work
            {"expiry": None},
            {"expiry": NOW},  # no expiry / already over
            {"expiry": NOW + dt.timedelta(days=366)},
        ):  # too long
            with pytest.raises(ZoikoError) as e:
                authorize(p, **bad)
            assert code(e) in (ErrorCode.POLICY_DENIED, ErrorCode.VALIDATION_FAILED), bad
        assert authorize(p)["state"] == "AUTHORIZED"
        assert AiPermitEvent.objects.filter(permit_ref=p["id"], to_state="AUTHORIZED").count() == 1


def test_a_clinical_content_feature_needs_a_named_clinical_owner(ctx_a):
    with tenant_transaction(ctx_a):
        p = propose(lane="B", level="A2")
        walk(p)
        with pytest.raises(ZoikoError) as e:
            authorize(p)
        assert code(e) == ErrorCode.VALIDATION_FAILED
        q = propose(
            feature_id="notes.summary_assist", lane="B", level="A2", clinical_owner_ref="doc_1"
        )
        walk(q)
        assert authorize(q)["state"] == "AUTHORIZED"


@pytest.mark.parametrize("over", [{"level": "A3"}, {"lane": "C"}, {"lane": "D"}])
def test_regulated_device_style_features_cannot_be_authorized_here(ctx_a, over):
    with tenant_transaction(ctx_a):
        p = propose(**over)
        walk(p)
        with pytest.raises(ZoikoError) as e:
            authorize(p)
    assert code(e) == ErrorCode.CLINICAL_ACCEPTANCE_ABSENT


def test_suspending_or_retiring_needs_a_reason(ctx_a):
    p = live(ctx_a)
    with tenant_transaction(ctx_a), pytest.raises(ZoikoError) as e:
        ai.advance(p["id"], "SUSPENDED", actor_ref="sre", clock=CLOCK)
    assert code(e) == ErrorCode.VALIDATION_FAILED


def test_only_one_permit_per_feature_can_be_authorized_at_a_time(ctx_a):
    old = live(ctx_a)
    with tenant_transaction(ctx_a):
        new = propose()
        walk(new)
        with pytest.raises(ZoikoError) as e:
            authorize(new)
        assert code(e) == ErrorCode.CONFLICT_VERSION
        ai.advance(old["id"], "RETIRED", actor_ref="lead", reason="replaced", clock=CLOCK)
        assert authorize(new)["state"] == "AUTHORIZED"
        other = propose(feature_id="other.feature")  # a different feature is unaffected
        walk(other)
        assert authorize(other)["state"] == "AUTHORIZED"


def test_a_retired_permit_is_final(ctx_a):
    with tenant_transaction(ctx_a):
        p = propose()
        ai.advance(p["id"], "RETIRED", actor_ref="lead", reason="not needed", clock=CLOCK)
        with pytest.raises(ZoikoError) as e:
            ai.advance(p["id"], "QUALIFICATION", actor_ref="lead", clock=CLOCK)
    assert code(e) == ErrorCode.CONFLICT_VERSION


# ------------------------------------------------------------------ tools: whitelist only
@pytest.mark.parametrize("tool", ["order_medication", "release_critical_result"])
def test_the_forbidden_tools_can_never_be_on_a_list(ctx_a, tool):
    for field in ("allowed_tools", "write_tools", "two_person_tools"):
        with tenant_transaction(ctx_a), pytest.raises(ZoikoError) as e:
            propose(**{"allowed_tools": [tool, "x_tool"], field: [tool]})
        assert code(e) == ErrorCode.VALIDATION_FAILED, field


def test_tool_lists_must_nest_and_have_no_repeats(ctx_a):
    for over in (
        {"write_tools": ["send_reply"]},  # write not allowed
        {"allowed_tools": ["a_tool"], "two_person_tools": ["a_tool"]},  # two-person not a write
        {"allowed_tools": ["a_tool", "a_tool"]},
        {"allowed_tools": ["Bad Name"]},
    ):
        with tenant_transaction(ctx_a), pytest.raises(ZoikoError) as e:
            propose(**over)
        assert code(e) == ErrorCode.VALIDATION_FAILED, over


def test_a_tool_not_on_the_list_is_rejected_and_nothing_is_produced(ctx_a):
    live(ctx_a, allowed_tools=["lookup_hours"])
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "delete_everything"}],
            clock=CLOCK,
        )
        assert (
            r["decision"] == "REJECTED" and r["output"] == "" and "tool_not_on_list" in r["flags"]
        )
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "lookup_hours"}],
            clock=CLOCK,
        )
        assert r["decision"] == "HUMAN_REVIEW" and r["approvals"] == []  # a read tool: no approval


def test_more_steps_than_the_permit_allows_is_rejected(ctx_a):
    live(ctx_a, allowed_tools=["lookup_hours"], max_steps=1)
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "lookup_hours"}, {"tool": "lookup_hours"}],
            clock=CLOCK,
        )
    assert r["decision"] == "REJECTED" and "too_many_steps" in r["flags"]


# ------------------------------------------------------------------ writes need a human
def test_a_write_waits_for_a_human_who_is_not_the_proposer(ctx_a):
    live(ctx_a, allowed_tools=["send_reply"], write_tools=["send_reply"])
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "send_reply", "payload": {"to": "x"}}],
            clock=CLOCK,
        )
        [approval] = r["approvals"]
        assert AiActionApproval.objects.get(public_id=approval).state == "pending"
        with pytest.raises(ZoikoError) as e:  # not the one who proposed it
            ai.decide(approval, approver_ref="staff", approve=True)
        assert code(e) == ErrorCode.POLICY_DENIED
        assert ai.decide(approval, approver_ref="boss", approve=True)["state"] == "approved"
        with pytest.raises(ZoikoError) as e2:  # decided once, final
            ai.decide(approval, approver_ref="other", approve=False)
        assert code(e2) == ErrorCode.CONFLICT_VERSION


def test_a_two_person_write_needs_two_different_approvers(ctx_a):
    live(
        ctx_a,
        allowed_tools=["send_reply"],
        write_tools=["send_reply"],
        two_person_tools=["send_reply"],
    )
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "send_reply"}],
            clock=CLOCK,
        )
        [approval] = r["approvals"]
        assert ai.decide(approval, approver_ref="boss", approve=True)["state"] == "pending"
        with pytest.raises(ZoikoError) as e:  # the same person twice
            ai.decide(approval, approver_ref="boss", approve=True)
        assert code(e) == ErrorCode.POLICY_DENIED
        assert ai.decide(approval, approver_ref="second", approve=True)["state"] == "approved"


def test_a_suspended_or_expired_feature_cannot_have_old_writes_approved(ctx_a):
    p = live(ctx_a, allowed_tools=["send_reply"], write_tools=["send_reply"])
    with tenant_transaction(ctx_a):
        first = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "send_reply"}],
            clock=CLOCK,
        )["approvals"][0]
        second = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "send_reply"}],
            clock=CLOCK,
        )["approvals"][0]
        with pytest.raises(ZoikoError) as e:  # past the expiry
            ai.decide(
                first,
                approver_ref="boss",
                approve=True,
                clock=FixedClock(LATER + dt.timedelta(seconds=1)),
            )
        assert code(e) == ErrorCode.POLICY_DENIED
        ai.advance(p["id"], "SUSPENDED", actor_ref="sre", reason="stop", clock=CLOCK)
        with pytest.raises(ZoikoError) as e2:  # suspended
            ai.decide(second, approver_ref="boss", approve=True, clock=CLOCK)
        assert code(e2) == ErrorCode.POLICY_DENIED
        assert (
            ai.decide(second, approver_ref="boss", approve=False, clock=CLOCK)["state"]
            == "rejected"
        )


def test_a_human_can_refuse_a_proposed_write(ctx_a):
    live(ctx_a, allowed_tools=["send_reply"], write_tools=["send_reply"])
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "send_reply"}],
            clock=CLOCK,
        )
        assert (
            ai.decide(r["approvals"][0], approver_ref="boss", approve=False)["state"] == "rejected"
        )


# ------------------------------------------------------------------ shadow mode acts on nothing
def test_shadow_mode_runs_but_never_acts(ctx_a):
    with tenant_transaction(ctx_a):
        p = propose(allowed_tools=["send_reply"], write_tools=["send_reply"])
        walk(p, upto="SHADOW")
        r = ai.run(
            ai.REPLY_DRAFT, actor_ref="staff", purpose_code="reply", input_text="hi", clock=CLOCK
        )
        assert r["mode"] == "shadow" and r["decision"] == "HUMAN_REVIEW"
        r = ai.run(
            ai.REPLY_DRAFT,
            actor_ref="staff",
            purpose_code="reply",
            input_text="hi",
            tools=[{"tool": "send_reply"}],
            clock=CLOCK,
        )
        assert r["decision"] == "REJECTED" and r["approvals"] == []
        assert AiActionApproval.objects.count() == 0


# ------------------------------------------------------------------ evidence
def test_no_text_is_stored_only_fingerprints(ctx_a):
    live(ctx_a)
    secret = "Patient Asha Rao has chest pain"
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT, actor_ref="staff", purpose_code="reply", input_text=secret, clock=CLOCK
        )
        req = AiRequest.objects.get(public_id=r["request_id"])
        ev = AiResponseEvidence.objects.get(request_ref=r["request_id"])
        assert req.input_digest == ai_digest(secret) and len(ev.output_digest) == 64
        dump = " ".join(str(v) for row in (req, ev) for v in vars(row).values())
        assert "Asha" not in dump and "chest" not in dump
        assert AuditEvent.objects.filter(action="ai.run", object_ref=r["request_id"]).exists()


def ai_digest(text):
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def test_an_empty_answer_is_quarantined(ctx_a):
    live(ctx_a)
    ai.register_provider(ai.REPLY_DRAFT, lambda _t: "   ")
    with tenant_transaction(ctx_a):
        r = ai.run(
            ai.REPLY_DRAFT, actor_ref="staff", purpose_code="reply", input_text="hi", clock=CLOCK
        )
    assert r["decision"] == "QUARANTINED" and r["output"] == ""


def test_input_must_be_present_and_bounded(ctx_a):
    live(ctx_a)
    with tenant_transaction(ctx_a):
        for text in ("", "x" * 8001):
            with pytest.raises(ZoikoError) as e:
                ai.run(
                    ai.REPLY_DRAFT,
                    actor_ref="staff",
                    purpose_code="reply",
                    input_text=text,
                    clock=CLOCK,
                )
            assert code(e) == ErrorCode.VALIDATION_FAILED


def test_ai_history_cannot_be_changed_or_removed_even_by_the_owner(ctx_a):
    live(ctx_a)
    with tenant_transaction(ctx_a):
        ai.run(
            ai.REPLY_DRAFT, actor_ref="staff", purpose_code="reply", input_text="hi", clock=CLOCK
        )
    for table in ("ai_aipermitevent", "ai_airequest", "ai_airesponseevidence"):
        for sql in (
            f"UPDATE {table} SET created_at = now()",  # noqa: S608
            f"DELETE FROM {table}",  # noqa: S608
        ):
            with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
                c.execute(sql)


def test_the_app_role_sees_only_its_own_tenants_ai_rows(ctx_a, ctx_b):
    live(ctx_a)
    with tenant_transaction(ctx_b), as_app_role():
        assert AiRequest.objects.count() == 0
        from modules.ai.models import AiUsePermit

        assert AiUsePermit.objects.count() == 0


def test_the_forbidden_list_is_exactly_what_the_docs_say():
    assert ai.FORBIDDEN_TOOLS == {"order_medication", "release_critical_result"}
