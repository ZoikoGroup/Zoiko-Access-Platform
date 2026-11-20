"""Retention rules with no periods until Legal sets them, approved by two administrators, and a
report that only counts (DEV-213). Nothing in the product deletes anything."""
import datetime as dt
import json

import pytest

from foundation import retention as registry
from foundation.cell_context import tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.evidence.models import AuditEvent
from modules.retention import api as retention
from modules.rights.models import RightsCase
from modules.scheduling.models import Booking
from modules.trust import api as trust
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
FAR = FixedClock(dt.datetime(2040, 1, 1, tzinfo=UTC))      # every test slot is long past by then


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    practice.login("boss2", trust.ActorType.FACILITY_USER, "R13")
    return practice


def propose(p, who="boss", **over):
    body = {"data_class": "bookings", "period_days": 365, "basis": "Clinic records policy 4.2"}
    body.update(over)
    return p.post("staff/retention", body, subject=who)


def report(p, who="boss"):
    r = p.get("staff/retention", subject=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def row(body, key="bookings"):
    return next(c for c in body["classes"] if c["data_class"] == key)


def step(p, rule, name, who="boss2"):
    return p.post(f"staff/retention/{rule['public_id']}/{name}", {}, subject=who)


def test_the_product_ships_with_no_periods_and_nothing_is_due(p):
    body = report(p)
    assert {c["data_class"] for c in body["classes"]} >= {"bookings", "message_threads"}
    assert all(c["rule"] is None and c["past_period"] is None for c in body["classes"])
    assert body["deletes_anything"] is False


def test_a_rule_counts_only_once_a_second_administrator_approves_it(p):
    r = propose(p)
    assert r.status_code == 201, r.content
    pending = row(json.loads(r.content))
    assert pending["rule"] is None and pending["proposal"]["period_days"] == 365
    assert step(p, pending["proposal"], "approve", who="boss").status_code == 403   # not alone
    done = step(p, pending["proposal"], "approve")
    assert done.status_code == 200, done.content
    assert row(report(p))["rule"]["period_days"] == 365


def test_only_administrators_may_propose_or_approve(p):
    assert propose(p, who="ops").status_code == 403
    rule = row(json.loads(propose(p).content))["proposal"]
    assert step(p, rule, "approve", who="ops").status_code == 403
    assert report(p, who="ops")["can_set"] is False


@pytest.mark.parametrize("over,field", [
    ({"data_class": "medical_notes"}, "data_class"), ({"period_days": 0}, "period_days"),
    ({"period_days": 36_501}, "period_days"), ({"period_days": "365"}, "period_days"),
    ({"period_days": True}, "period_days"), ({"basis": "x"}, "basis"), ({"basis": ""}, "basis"),
])
def test_bad_proposals_are_refused(p, over, field):
    r = propose(p, **over)
    assert r.status_code == 422 and field in json.loads(r.content)["fields"]


def test_one_open_proposal_per_kind_and_a_withdrawn_one_never_counts(p):
    rule = row(json.loads(propose(p).content))["proposal"]
    assert propose(p, period_days=10).status_code == 409
    assert step(p, rule, "withdraw", who="boss").status_code == 200
    assert step(p, rule, "approve").status_code == 409
    assert row(report(p))["rule"] is None


def test_a_new_rule_supersedes_the_old_and_no_limit_is_allowed(p):
    first = row(json.loads(propose(p).content))["proposal"]
    step(p, first, "approve")
    second = row(json.loads(propose(p, who="boss2", period_days=None,
                                    basis="Keep until Legal decides").content))["proposal"]
    assert step(p, second, "approve", who="boss").status_code == 200
    now = row(report(p))
    assert now["rule"]["period_days"] is None and now["past_period"] is None


def _approved(p, days=365, key="bookings"):
    rule = row(json.loads(propose(p, data_class=key, period_days=days).content), key)["proposal"]
    assert step(p, rule, "approve").status_code == 200


def test_the_report_counts_records_past_the_period_and_deletes_nothing(p):
    for slot in p.slots[:2]:
        assert book(p, slot["public_id"]).status_code in (200, 201)
    _approved(p)
    org = _org(p)
    with tenant_transaction(p.clinic.ctx):
        before = Booking.objects.count()
        found = row(retention.report(org, clock=FAR))
        assert found["past_period"] == before == 2
        assert row(retention.report(org))["past_period"] == 0     # not yet, today
        assert Booking.objects.count() == before


def _org(p):
    with tenant_transaction(p.clinic.ctx):
        from modules.retention.models import RetentionRule
        return RetentionRule.objects.values_list("organization_ref", flat=True).first()


def test_a_lawful_hold_keeps_a_patients_records_out_of_the_count(p):
    assert book(p, p.slots[0]["public_id"]).status_code in (200, 201)
    _approved(p)
    org = _org(p)
    with tenant_transaction(p.clinic.ctx):
        assert row(retention.report(org, clock=FAR))["past_period"] == 1
        RightsCase.objects.create(
            ref_no="DSR-TEST0001", person_ref=p.person, organization_ref=org,
            requested_by_ref=p.patient_actor, request_type="erasure", lawful_hold=True,
            hold_reason="Litigation", cell_id=p.clinic.ctx.cell_id,
            tenant_id=p.clinic.ctx.tenant_id)
        held = retention.report(org, clock=FAR)
        assert held["patients_on_hold"] == 1
        assert row(held)["past_period"] == 0


def test_every_step_is_audited(p):
    rule = row(json.loads(propose(p).content))["proposal"]
    step(p, rule, "approve")
    with tenant_transaction(p.clinic.ctx):
        actions = set(AuditEvent.objects.filter(action__startswith="retention.")
                      .values_list("action", flat=True))
    assert {"retention.proposed", "retention.approved"} <= actions


def test_a_data_class_cannot_be_registered_twice_with_another_counter():
    with pytest.raises(ValueError):
        registry.register("bookings", "x", "y", lambda cutoff, held: 0)
