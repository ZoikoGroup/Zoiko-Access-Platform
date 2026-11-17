"""Who is on call for an incident, its timeline, escalation when nobody answers, and the restoration
notice before a serious incident closes (GHE-18 G-01, G-10; DEV-202)."""
import datetime as dt
import json

import pytest
from django.db import DatabaseError, transaction

from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from foundation.models import QueuedEmail
from foundation.timeutil import FixedClock, utc_now
from modules.trust import api as trust
from modules.trust import oncall
from modules.trust.models import IncidentEvent
from tests.test_incidents import made, post, resolve
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    w = Platform()
    w.sam = w.staff("sam", "R11")
    w.tess = w.staff("tess", "R11")
    w.iyer = w.staff("iyer", "R12")
    return w


def assign(world, ref, owner, deputy):
    with tenant_transaction(world.ctx):
        return trust.assign_incident(incident_ref=ref, owner_ref=owner, deputy_ref=deputy,
                                     actor_ref=world.sam)


def kinds(world, ref):
    with tenant_transaction(world.ctx):
        return [e["kind"] for e in trust.incident_timeline(ref)]


def test_every_step_of_an_incident_is_on_its_timeline(world):
    ref = made(world, severity="sev2")
    assign(world, ref, world.tess, world.iyer)
    with tenant_transaction(world.ctx):
        trust.acknowledge_incident(incident_ref=ref, actor_ref=world.tess)
    assert resolve(world, ref).status_code == 200
    assert kinds(world, ref) == ["recorded", "assigned", "acknowledged", "resolved"]


def test_the_owner_and_deputy_are_two_different_on_call_staff(world):
    ref = made(world, severity="sev1")
    stranger = "x" * 22
    for owner, deputy in ((world.tess, world.tess), (stranger, world.iyer),
                          (world.tess, stranger)):
        with pytest.raises(ZoikoError):
            assign(world, ref, owner, deputy)
    assign(world, ref, world.tess, world.iyer)


def test_only_the_owner_or_deputy_acknowledges_and_only_once(world):
    ref = made(world, severity="sev1")
    assign(world, ref, world.tess, world.iyer)
    with tenant_transaction(world.ctx):
        with pytest.raises(ZoikoError), transaction.atomic():
            trust.acknowledge_incident(incident_ref=ref, actor_ref=world.sam)
        trust.acknowledge_incident(incident_ref=ref, actor_ref=world.iyer)
        with pytest.raises(ZoikoError), transaction.atomic():
            trust.acknowledge_incident(incident_ref=ref, actor_ref=world.tess)


def test_a_serious_incident_nobody_acknowledges_is_escalated_to_the_deputy_once(world):
    ref = made(world, severity="sev0")
    assign(world, ref, world.tess, world.iyer)
    soon = FixedClock(utc_now() + dt.timedelta(minutes=5))
    later = FixedClock(utc_now() + oncall.ACK_WITHIN + dt.timedelta(minutes=1))
    with tenant_transaction(world.ctx):
        assert oncall.escalate_due(soon) == 0                    # not yet
        assert oncall.escalate_due(later) == 1
        assert oncall.escalate_due(later) == 0                   # once
    assert kinds(world, ref)[-1] == "escalated"
    assert QueuedEmail.objects.filter(kind="incident_escalation").count() == 0   # unverified


def test_an_acknowledged_or_minor_incident_is_not_escalated(world):
    quiet = made(world, severity="sev3")
    handled = made(world, severity="sev1")
    assign(world, quiet, world.tess, world.iyer)
    assign(world, handled, world.tess, world.iyer)
    with tenant_transaction(world.ctx):
        trust.acknowledge_incident(incident_ref=handled, actor_ref=world.tess)
        later = FixedClock(utc_now() + dt.timedelta(hours=2))
        assert oncall.escalate_due(later) == 0


def test_a_sev0_or_sev1_incident_closes_only_with_a_restoration_notice(world):
    ref = made(world, severity="sev1")
    refused = resolve(world, ref)
    assert refused.status_code == 422
    assert "restoration_notice" in json.loads(refused.content)["fields"]
    done = resolve(world, ref, restoration_notice="Customers and partners told at 10:40.")
    assert done.status_code == 200
    assert json.loads(done.content)["incident"]["restoration_notice"].startswith("Customers")


def test_the_timeline_cannot_be_changed_even_by_the_owner_of_the_table(world):
    ref = made(world, severity="sev2")
    with tenant_transaction(world.ctx):
        row = IncidentEvent.objects.filter(incident__public_id=ref).first()
        with pytest.raises(DatabaseError), transaction.atomic():
            IncidentEvent.objects.filter(pk=row.pk).update(note="rewritten")


def test_reporting_an_incident_through_the_api_still_works(world):
    r = post(world, "platform/incidents", {"title": "Mail delayed", "severity": "sev2",
                                           "happened_on": utc_now().date().isoformat()})
    assert r.status_code == 201, r.content
