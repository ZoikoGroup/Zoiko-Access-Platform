import json

import pytest
from django.db import DatabaseError, connection, transaction

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.safety.models import Hazard
from tests.test_api import API, Web
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def platform():
    world = Platform()
    world.staff("sam", "R11")  # support: may read
    world.staff("iyer", "R12")  # safety officer
    world.staff("rao", "R12")  # a second, independent safety officer
    return world


def get(world, path, subject="iyer"):
    return world.client.get(API + path, **world.header(world.tenant_public_id, subject))


def post(world, path, body, subject="iyer"):
    return world.client.post(
        API + path,
        json.dumps(body),
        content_type="application/json",
        **world.header(world.tenant_public_id, subject),
    )


def register(world, subject="iyer"):
    r = get(world, "platform/hazards", subject)
    assert r.status_code == 200
    return json.loads(r.content)


def hazard(world, ref="H01"):
    return next(h for h in register(world)["hazards"] if h["ref_no"] == ref)


ASSESSMENT = {
    "severity": 4,
    "probability": 2,
    "residual_risk": "medium",
    "basis_method": "Reviewed with the clinical lead against the booking flow",
}


def assess(world, ref="H01", subject="iyer", **over):
    return post(
        world,
        f"platform/hazards/{hazard(world, ref)['id']}/assess",
        {**ASSESSMENT, **over},
        subject,
    )


def step(world, name, ref="H01", subject="rao", **body):
    return post(world, f"platform/hazards/{hazard(world, ref)['id']}/{name}", body, subject)


# ------------------------------------------------------------------ the seeded register
def test_the_register_starts_with_the_hazards_the_docs_name_and_adds_them_once(platform):
    rows = register(platform)["hazards"]
    refs = [h["ref_no"] for h in rows]
    expected = {f"H{n:02d}" for n in range(1, 21)} | {f"HAI-{n:02d}" for n in range(1, 17)}
    assert set(refs) == expected
    assert len(rows) == 36 and len(set(refs)) == 36
    assert {h["library"] for h in rows} == {"product", "ai"}
    assert all(h["status"] == "OPEN" and h["assessed"] is False for h in rows)
    assert hazard(platform, "H17")["title"] == "AI drives clinical action"
    assert hazard(platform, "HAI-12")["title"] == "Agent overreach"
    assert len(register(platform)["hazards"]) == 36  # asking again adds nothing


def test_support_may_read_but_not_decide_and_customers_see_nothing(platform):
    assert register(platform, "sam")["can_decide"] is False
    assert register(platform)["can_decide"] is True
    hid = hazard(platform)["id"]
    for name, body in (
        ("assess", ASSESSMENT),
        ("review", {"verdict": "verified"}),
        ("accept", {}),
        ("retire", {"reason": "not needed"}),
    ):
        assert post(platform, f"platform/hazards/{hid}/{name}", body, "sam").status_code == 403
    assert hazard(platform)["status"] == "OPEN" and hazard(platform)["assessed"] is False
    outsider = Web()
    outsider.staff("desk")
    assert outsider.get("platform/hazards", subject="desk").status_code == 403


# ------------------------------------------------------------------ assessing
@pytest.mark.parametrize(
    "bad",
    [
        {"severity": 0},
        {"severity": 6},
        {"severity": "high"},
        {"severity": True},
        {"probability": 0},
        {"probability": 9},
        {"residual_risk": "tiny"},
        {"residual_risk": ""},
        {"basis_method": ""},
        {"basis_method": "no"},
    ],
)
def test_a_bad_assessment_is_refused_and_changes_nothing(platform, bad):
    assert assess(platform, **bad).status_code == 422
    assert hazard(platform)["assessed"] is False and hazard(platform)["severity"] == 0


def test_an_assessment_is_recorded_with_who_made_it(platform):
    r = assess(platform, hazardous_situation="A result is attached to the wrong patient")
    assert r.status_code == 200
    h = hazard(platform)
    assert (h["severity"], h["probability"], h["residual_risk"]) == (4, 2, "medium")
    assert h["assessed"] is True and h["status"] == "OPEN" and h["assessed_by"] and h["owner"]
    assert h["hazardous_situation"].startswith("A result")


# ------------------------------------------------------------------ independent review
def test_nobody_reviews_their_own_assessment_and_there_must_be_one_to_review(platform):
    assert (
        step(platform, "review", subject="rao", verdict="verified").status_code == 422
    )  # not assessed yet
    assess(platform)
    assert (
        step(platform, "review", subject="iyer", verdict="verified").status_code == 403
    )  # the assessor
    assert hazard(platform)["status"] == "OPEN"
    assert step(platform, "review", subject="rao", verdict="verified").status_code == 200
    assert hazard(platform)["status"] == "VERIFIED" and hazard(platform)["reviewer"]


def test_a_review_must_be_verified_or_rejected_and_a_rejection_needs_a_reason(platform):
    assess(platform)
    assert step(platform, "review", verdict="fine").status_code == 422
    assert step(platform, "review", verdict="rejected").status_code == 422
    assert (
        step(
            platform, "review", verdict="rejected", note="The probability looks too low"
        ).status_code
        == 200
    )
    assert hazard(platform)["status"] == "REJECTED"
    assert step(platform, "review", verdict="verified").status_code == 409  # already decided
    assert assess(platform, probability=3).status_code == 200  # fix it and assess again
    assert hazard(platform)["status"] == "OPEN" and hazard(platform)["reviewer"] == ""


# ------------------------------------------------------------------ accepting what risk remains
def test_remaining_risk_is_accepted_only_after_a_verified_review_by_someone_other_than_the_assessor(
    platform,
):
    assess(platform)
    assert step(platform, "accept", subject="rao").status_code == 409  # not verified yet
    step(platform, "review", subject="rao", verdict="verified")
    assert step(platform, "accept", subject="iyer").status_code == 403  # the assessor cannot
    assert (
        step(platform, "accept", subject="rao", note="Mitigated by the booking check").status_code
        == 200
    )
    assert hazard(platform)["status"] == "ACCEPTED"
    assert step(platform, "accept", subject="rao").status_code == 409  # once


def test_a_high_remaining_risk_can_never_be_accepted(platform):
    assess(platform, residual_risk="high")
    step(platform, "review", subject="rao", verdict="verified")
    assert step(platform, "accept", subject="rao").status_code == 422
    assert hazard(platform)["status"] == "VERIFIED"


def test_assessing_again_cancels_an_earlier_sign_off(platform):
    assess(platform)
    step(platform, "review", subject="rao", verdict="verified")
    assess(platform, severity=5)  # a new judgment
    h = hazard(platform)
    assert h["status"] == "OPEN" and h["reviewer"] == "" and h["severity"] == 5
    assert step(platform, "accept", subject="rao").status_code == 409  # the old review is gone


# ------------------------------------------------------------------ retiring, history and audit
def test_retiring_needs_a_reason_and_is_final(platform):
    assert step(platform, "retire", subject="iyer", reason="").status_code == 422
    assert step(platform, "retire", subject="iyer", reason="No longer applies").status_code == 200
    assert hazard(platform)["status"] == "RETIRED"
    assert step(platform, "retire", subject="iyer", reason="again").status_code == 409
    assert assess(platform).status_code == 409


def test_every_step_is_in_the_history_and_the_audit_log_and_cannot_be_changed(platform):
    assess(platform)
    step(platform, "review", subject="rao", verdict="verified")
    step(platform, "accept", subject="rao", note="ok")
    steps = json.loads(get(platform, f"platform/hazards/{hazard(platform)['id']}/history").content)[
        "steps"
    ]
    assert [s["action"] for s in steps] == ["assess", "review_verified", "accept"]
    assert [s["to"] for s in steps] == ["OPEN", "VERIFIED", "ACCEPTED"]
    with tenant_transaction(platform.ctx):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        assert Hazard.objects.count() == 36
    assert {"safety.hazard_seed", "safety.hazard.assess", "safety.hazard.accept"} <= actions
    for sql in ("UPDATE safety_hazardevent SET note = 'x'", "DELETE FROM safety_hazardevent"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
            c.execute(sql)
