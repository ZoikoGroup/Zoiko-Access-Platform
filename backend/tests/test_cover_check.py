"""Asking an insurer whether a policy covers something (modules/coverage/insurers.py): what is
answered, what is kept, and who may ask."""
import datetime as dt
import json

import pytest
from django.db import DatabaseError, transaction

from foundation.cell_context import tenant_transaction
from foundation.release_check import release_problems
from foundation.timeutil import utc_now
from modules.coverage import api as coverage
from modules.coverage import insurers
from modules.coverage.models import CoverCheck
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MEMBER = "RH-48213-OK"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.COVER_SIMULATOR = True
    yield
    coverage.reset_adapters()


@pytest.fixture
def p():
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):   # staff deal only with their own site's patients
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def policy(p, insurer="Pretend Insurer", member=MEMBER, **over):
    body = {"insurer": insurer, "plan": "Gold", "member_id": member, **over}
    r = p.post("coverage", body, subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["coverage"]["id"]


def ask(p, coverage_id, subject="pat", **body):
    return p.post(f"coverage/{coverage_id}/check", body, subject=subject)


def answered(p, coverage_id, **body):
    r = ask(p, coverage_id, **body)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["check"]


def listed(p, coverage_id, subject="pat"):
    return p.get(f"coverage/{coverage_id}/checks", subject=subject)


def share(p, on=True):
    assert p.post("coverage/sharing", {"share": on}, subject="pat").status_code == 200


class Scripted(insurers.InsurerAdapter):
    """A pretend real insurer: answers as told and counts how often it was asked."""

    name = "scripted"
    asked: list[tuple[str, str]] = []

    def __init__(self, outcome):
        self.outcome = outcome

    def check(self, *, member_id, category, on):
        Scripted.asked.append((member_id, category))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def connect(name, outcome):
    Scripted.asked.clear()
    coverage.register_adapter(name, lambda: Scripted(outcome))


# ------------------------------------------------------------------ what is answered
@pytest.mark.parametrize("ending, result, reason", [
    ("OK", "eligible", "in_force"), ("EX", "not_eligible", "expired"),
    ("NC", "not_eligible", "not_covered"), ("NF", "not_eligible", "member_not_found"),
    ("ZZ", "unknown", "unclear"), ("DOWN", "unknown", "unavailable"),
])
def test_the_pretend_insurer_answers_from_the_end_of_the_member_number(p, ending, result, reason):
    got = answered(p, policy(p, member=f"RH-48213-{ending}"))
    assert (got["result"], got["reason"]) == (result, reason)
    assert got["reason_text"] == insurers.REASONS[reason]


def test_an_answer_says_when_it_was_given_and_that_it_is_not_a_promise(p):
    got = answered(p, policy(p), category="general_physician")
    assert got["category"] == "general_physician" and got["checked_at"] and got["fresh"] is True
    assert "not a promise" in got["disclaimer"]


def test_an_insurer_we_cannot_reach_automatically_is_unknown_never_guessed(p):
    got = answered(p, policy(p, insurer="Some Real Insurer Ltd"))
    assert (got["result"], got["reason"]) == ("unknown", "no_connection")


def test_with_the_pretend_insurer_switched_off_it_is_just_another_unconnected_insurer(p, settings):
    settings.COVER_SIMULATOR = False
    got = answered(p, policy(p))
    assert (got["result"], got["reason"]) == ("unknown", "no_connection")


def test_a_real_insurers_adapter_is_used_for_that_insurers_name_however_it_is_typed(p):
    connect("Acme  Health", insurers.Answer("eligible", "in_force", "ACME-77"))
    got = answered(p, policy(p, insurer="acme health"), category="dermatology")
    assert got["result"] == "eligible"
    assert Scripted.asked == [(MEMBER, "dermatology")]          # it was told what it needs
    with tenant_transaction(p.clinic.ctx):
        row = CoverCheck.objects.get(public_id=got["id"])
    assert (row.adapter, row.source_ref) == ("scripted", "ACME-77")


@pytest.mark.parametrize("outcome, reason", [
    (insurers.InsurerUnavailable(), "unavailable"), (RuntimeError("boom"), "unavailable"),
    (insurers.Answer("approved", "in_force"), "unclear"),
    (insurers.Answer("eligible", "because we said so"), "unclear"),
])
def test_a_failing_or_strange_insurer_gives_unknown(p, outcome, reason):
    connect("Acme", outcome)
    got = answered(p, policy(p, insurer="Acme"))
    assert (got["result"], got["reason"]) == ("unknown", reason)
    assert "boom" not in json.dumps(got)


@pytest.mark.parametrize("dates", [
    {"valid_until": (utc_now().date() - dt.timedelta(days=1)).isoformat()},
    {"valid_from": (utc_now().date() + dt.timedelta(days=5)).isoformat()},
])
def test_a_policy_outside_its_dates_is_answered_without_asking_anyone(p, dates):
    connect("Acme", insurers.Answer("eligible", "in_force"))
    got = answered(p, policy(p, insurer="Acme", **dates))
    assert (got["result"], got["reason"]) == ("not_eligible", "expired")
    assert Scripted.asked == []


def test_an_ended_policy_cannot_be_checked(p):
    coverage_id = policy(p)
    assert p.post(f"coverage/{coverage_id}/end", {}, subject="pat").status_code == 200
    assert ask(p, coverage_id).status_code == 409


@pytest.mark.parametrize("bad", ["Dermatology", "a", "two words", "x" * 41, 5, ["a"]])
def test_a_category_must_be_a_plain_code(p, bad):
    assert ask(p, policy(p), category=bad).status_code == 422


def test_at_most_ten_checks_an_hour_for_one_policy(p):
    coverage_id = policy(p)
    for _ in range(10):
        answered(p, coverage_id)
    assert ask(p, coverage_id).status_code == 429


# ------------------------------------------------------------------ what is kept
def test_the_member_number_is_never_returned_or_kept_with_the_answer(p):
    coverage_id = policy(p)
    got = answered(p, coverage_id)
    assert MEMBER not in json.dumps(got) and "48213" not in json.dumps(got)
    with tenant_transaction(p.clinic.ctx):
        row = CoverCheck.objects.get(public_id=got["id"])
    kept = " ".join(str(getattr(row, f.name)) for f in CoverCheck._meta.concrete_fields)
    assert MEMBER not in kept and "48213" not in kept


def test_every_answer_is_listed_newest_first(p):
    coverage_id = policy(p)
    first = answered(p, coverage_id, category="dermatology")
    second = answered(p, coverage_id, category="orthopedics")
    r = listed(p, coverage_id)
    assert r.status_code == 200
    assert [c["id"] for c in json.loads(r.content)["checks"]] == [second["id"], first["id"]]


def test_an_answer_can_never_be_changed_or_removed(p):
    got = answered(p, policy(p))
    for change in ("update", "delete"):
        with pytest.raises(DatabaseError):
            with tenant_transaction(p.clinic.ctx), transaction.atomic():
                rows = CoverCheck.objects.filter(public_id=got["id"])
                rows.update(result="eligible") if change == "update" else rows.delete()


def test_asking_is_audited(p):
    got = answered(p, policy(p))
    with tenant_transaction(p.clinic.ctx):
        rows = list(AuditEvent.objects.filter(action="coverage.check"))
    assert [r.object_ref for r in rows] == [got["id"]]


# ------------------------------------------------------------------ who may ask
def test_staff_may_ask_and_read_only_while_the_patient_shares_the_insurance(p):
    coverage_id = policy(p)
    assert ask(p, coverage_id, subject="ops").status_code == 403
    assert listed(p, coverage_id, subject="ops").status_code == 403
    share(p)
    assert ask(p, coverage_id, subject="ops").status_code == 201
    assert listed(p, coverage_id, subject="ops").status_code == 200
    share(p, False)
    assert ask(p, coverage_id, subject="ops").status_code == 403
    assert listed(p, coverage_id, subject="ops").status_code == 403


def test_another_patient_can_neither_ask_nor_read_even_when_it_is_shared(p):
    coverage_id = policy(p)
    share(p)
    p.patient("other")
    assert ask(p, coverage_id, subject="other").status_code in (403, 404)
    assert listed(p, coverage_id, subject="other").status_code in (403, 404)


def test_it_needs_a_login_the_right_method_and_a_real_policy(p):
    coverage_id = policy(p)
    assert p.client.post(API + f"coverage/{coverage_id}/check").status_code == 401
    assert p.get(f"coverage/{coverage_id}/check", subject="pat").status_code == 405
    assert p.post(f"coverage/{coverage_id}/checks", {}, subject="pat").status_code == 405
    assert ask(p, "nosuchpolicy0000000000").status_code == 404


def test_a_real_server_may_not_use_the_pretend_insurer():
    assert release_problems(good()) == []
    assert any("pretend insurer" in x for x in release_problems(good(COVER_SIMULATOR=True)))
