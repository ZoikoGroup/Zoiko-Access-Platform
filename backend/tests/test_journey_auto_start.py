"""Referrals arriving and people joining a waiting list start the clinic's journey by themselves
(DEV-395, decided 27 Nov 2026), but only where the clinic published that journey, always with a
named accountable person, and never twice for the same thing."""
import pytest

from foundation.cell_context import tenant_transaction
from modules.orchestration.models import Transition, WorkflowInstance
from modules.orchestration.subscribers import register_subscribers
from modules.referral import api as referral_api
from tests.test_referral import Clinic2, issued
from tests.test_standard_journeys import in_use
from tests.test_waitlist import Waiting, deliver

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.WORKFLOW_CLINICAL_ENABLED = False
    register_subscribers()


def journeys(practice):
    with tenant_transaction(practice.clinic.ctx):
        return list(WorkflowInstance.objects.all().values(
            "public_id", "journey", "subject_ref", "owner_ref", "state", "organization_ref"))


# ------------------------------------------------------------------ referrals
def test_nothing_starts_where_the_clinic_has_not_published_the_journey():
    c = Clinic2()
    issued(c)
    deliver()
    assert journeys(c) == []


def test_a_referral_arriving_starts_its_journey_for_the_doctor_who_answers_referrals():
    c = Clinic2()
    in_use(c, "referral")
    made = issued(c)
    deliver()
    [j] = journeys(c)
    with tenant_transaction(c.clinic.ctx):
        inbox = referral_api.inbox_dict(c.clinic.org)
        assert inbox is not None
        answerer = inbox["owner_ref"]
        [step] = Transition.objects.filter(instance__public_id=j["public_id"])
    assert j["journey"] == "referral" and j["state"] == "requested"
    assert j["subject_ref"] == made["id"] and j["owner_ref"] == answerer
    assert j["organization_ref"] == c.clinic.org
    assert step.actor_ref == "system" and step.evidence == [
        {"kind": "request_origin", "ref": made["id"]}]


def test_without_a_named_answerer_the_administrator_who_published_it_is_accountable():
    c = Clinic2(name_inbox=False)
    in_use(c, "referral")
    issued(c)
    deliver()
    [j] = journeys(c)
    assert j["owner_ref"] == c.boss


def test_the_same_referral_never_gets_a_second_journey():
    c = Clinic2()
    in_use(c, "referral")
    made = issued(c)
    deliver()
    from modules.orchestration.engine import start_automatically
    with tenant_transaction(c.clinic.ctx):
        again = start_automatically(definition_key="referral-journey", subject_ref=made["id"],
                                    organization_ref=c.clinic.org, owner_ref="")
    assert again is not None and len(journeys(c)) == 1


# ------------------------------------------------------------------ the waiting list
def test_joining_the_waiting_list_starts_its_journey_for_the_administrator():
    w = Waiting()
    boss = w.login("boss", "facility_user", "R13")
    in_use(w, "waitlist")
    assert w.join().status_code == 201
    deliver()
    [j] = journeys(w)
    assert j["journey"] == "waitlist" and j["owner_ref"] == boss
    assert j["subject_ref"] == w.entries()[0]["id"]


def test_no_waiting_list_journey_without_the_clinics_choice():
    w = Waiting()
    w.login("boss", "facility_user", "R13")
    assert w.join().status_code == 201
    deliver()
    assert journeys(w) == []
