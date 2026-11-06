import json

import pytest

from tests.test_api import Web

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PATH = ["QUALIFICATION", "DESIGN_CONTROL", "VALIDATION", "SANDBOX", "SHADOW", "RELEASE_REVIEW"]
NEW = {
    "feature_id": "message.reply_draft",
    "purpose": "Draft a reply for staff to review",
    "lane": "A",
    "level": "A1",
    "model": "template v1",
    "prompt": "Write a polite holding reply",
}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def web():
    w = Web()
    w.patient("pat")
    w.staff("desk")
    for name in ("boss", "boss2", "boss3"):
        w.login(name, "facility_user", "R13")
    return w


def permits(web, subject="boss"):
    r = web.get("staff/ai/permits", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)["permits"]


def propose(web, subject="boss", **over):
    return web.post("staff/ai/permits", {**NEW, **over}, subject=subject)


def step(web, permit, to, subject="boss", **body):
    return web.post(f"staff/ai/permits/{permit}/advance", {"to": to, **body}, subject=subject)


def to_review(web, permit):
    for state in PATH:
        assert step(web, permit, state).status_code == 200


def test_only_an_organization_administrator_may_use_ai_permits(web):
    for who in ("desk", "pat"):
        assert web.get("staff/ai/permits", subject=who).status_code == 403
        assert propose(web, subject=who).status_code == 403
    made = json.loads(propose(web).content)["permit"]
    for who in ("desk", "pat"):
        assert web.post(f"staff/ai/permits/{made['id']}/sign", subject=who).status_code == 403
        assert step(web, made["id"], "RETIRED", subject=who, reason="x").status_code == 403
    assert permits(web)[0]["state"] == "PROPOSED"


def test_proposing_stores_fingerprints_and_never_the_text(web):
    r = propose(web, model="secret model name", prompt="secret prompt text")
    assert r.status_code == 201
    row = permits(web)[0]
    assert row["feature_id"] == "message.reply_draft" and row["state"] == "PROPOSED"
    assert "secret" not in json.dumps(row)
    assert row["created_by"] and row["steps"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"feature_id": "Bad Name"},
        {"purpose": "no"},
        {"lane": "Z"},
        {"level": "A9"},
        {"model": ""},
        {"tools": "order_medication"},
        {"tools": "x_tool x_tool"},
    ],
)
def test_a_bad_proposal_is_refused(web, bad):
    assert propose(web, **bad).status_code == 422
    assert permits(web) == []


def test_authorizing_needs_two_other_signers_and_a_valid_length(web):
    made = json.loads(propose(web).content)["permit"]["id"]
    to_review(web, made)
    sign = lambda who: web.post(f"staff/ai/permits/{made}/sign", subject=who)  # noqa: E731
    assert step(web, made, "AUTHORIZED", days=30).status_code == 403  # nobody signed
    assert sign("boss").status_code == 403  # not the creator
    assert sign("boss2").status_code == 200
    assert sign("boss2").status_code == 403  # once each
    assert step(web, made, "AUTHORIZED", subject="boss3", days=30).status_code == 403  # one signer
    assert sign("boss3").status_code == 200
    for days in (0, 366, "x", None):
        assert step(web, made, "AUTHORIZED", days=days).status_code == 422, days
    r = step(web, made, "AUTHORIZED", days=30)
    assert r.status_code == 200 and json.loads(r.content)["permit"]["state"] == "AUTHORIZED"
    row = permits(web)[0]
    assert row["state"] == "AUTHORIZED" and len(row["signers"]) == 2 and row["expiry"]
    assert [s["to"] for s in row["steps"]][-1] == "AUTHORIZED"


def test_signing_is_only_possible_while_in_release_review(web):
    made = json.loads(propose(web).content)["permit"]["id"]
    assert web.post(f"staff/ai/permits/{made}/sign", subject="boss2").status_code == 409


def test_steps_cannot_be_skipped_and_stopping_needs_a_reason(web):
    made = json.loads(propose(web).content)["permit"]["id"]
    assert step(web, made, "SHADOW").status_code == 409
    assert step(web, made, "RETIRED").status_code == 422  # no reason
    assert step(web, made, "RETIRED", reason="not needed").status_code == 200
    assert step(web, made, "QUALIFICATION").status_code == 409  # final


def test_regulated_style_features_cannot_be_authorized_from_the_screen(web):
    made = json.loads(propose(web, level="A3").content)["permit"]["id"]
    to_review(web, made)
    for who in ("boss2", "boss3"):
        assert web.post(f"staff/ai/permits/{made}/sign", subject=who).status_code == 200
    assert step(web, made, "AUTHORIZED", days=30).status_code in (403, 409)
    assert permits(web)[0]["state"] == "RELEASE_REVIEW"


def test_another_organizations_administrator_sees_none_of_it(web):
    propose(web)
    other = Web()
    other.login("outsider", "facility_user", "R13")
    assert permits(other, subject="outsider") == []
