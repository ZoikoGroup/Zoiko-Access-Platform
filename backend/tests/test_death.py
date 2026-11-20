"""Recording that a patient has died (DEV-333): an administrator records it, the future bookings
are cancelled, a second person withdraws a mistake, and the notes clock becomes 8 years after."""

import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
FAC = trust.ActorType.FACILITY_USER


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    from tests.test_platform import Platform

    Platform()
    practice = Practice()
    practice.login("boss", FAC, "R13")
    practice.login("boss2", FAC, "R13")
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def body(r):
    return json.loads(r.content)


def record(p, subject="boss", died_on=None, source="death_certificate"):
    day = died_on or (dt.date.today() - dt.timedelta(days=2)).isoformat()
    return p.post(f"people/{p.person}/death", {"died_on": day, "source": source},
                  subject=subject)


def test_an_administrator_records_a_death_and_future_bookings_are_cancelled(p):
    from tests.test_api import book

    booked = book(p, p.slots[0]["public_id"])
    assert booked.status_code == 201, booked.content
    r = record(p)
    assert r.status_code == 201, r.content
    got = body(r)
    assert got["death"]["source"] == "death_certificate" and got["death"]["recorded_by_me"]
    assert got["bookings_cancelled"] == 1
    again = record(p)
    assert again.status_code == 201 and body(again)["bookings_cancelled"] == 0  # same: no change
    assert record(p, source="family_notice").status_code == 409  # a different one is refused
    with tenant_transaction(p.clinic.ctx):
        assert AuditEvent.objects.filter(action="person.death_recorded").count() == 1


def test_only_an_administrator_records_and_bad_input_is_refused(p):
    assert record(p, subject="ops").status_code == 403  # a receptionist
    assert record(p, subject="doc").status_code == 403
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    for bad in ({"died_on": tomorrow, "source": "death_certificate"},
                {"died_on": "1899-12-31", "source": "death_certificate"},
                {"died_on": "yesterday", "source": "death_certificate"},
                {"died_on": "2026-01-01", "source": "rumour"}):
        assert p.post(f"people/{p.person}/death", bad, subject="boss").status_code == 422, bad


def test_only_a_second_administrator_withdraws_a_mistake(p):
    record(p)
    path = f"people/{p.person}/death/withdraw"
    assert p.post(path, {"reason": "recorded_in_error"}, subject="boss").status_code == 403
    assert p.post(path, {"reason": "because"}, subject="boss2").status_code == 422
    r = p.post(path, {"reason": "recorded_in_error"}, subject="boss2")
    assert r.status_code == 200 and body(r)["death"]["died_on"] is None
    assert p.post(path, {"reason": "recorded_in_error"}, subject="boss2").status_code == 409
    seen = body(p.get(f"people/{p.person}/death", subject="ops"))["death"]
    assert seen["died_on"] is None and seen["can_record"] is False


def test_notes_are_kept_8_years_after_death():
    from modules.clinicalnote.services import keep_until

    last = dt.date(2020, 3, 1)
    assert keep_until(last, "2012-05-05", "2021-06-01") == dt.date(2029, 6, 1)  # a child too
    assert keep_until(last, "1950-05-05", "2021-06-01") == dt.date(2029, 6, 1)
    assert keep_until(last, None, "2021-06-01") == dt.date(2029, 6, 1)  # birth date not needed


def test_the_family_is_not_sent_a_cancellation_notice(p):
    from modules.comms.models import Notification
    from tests.test_api import book
    from tests.test_notifications import deliver

    assert book(p, p.slots[0]["public_id"]).status_code == 201
    deliver()  # the booking notice goes out as usual
    with tenant_transaction(p.clinic.ctx):
        before = Notification.objects.filter(person_ref=p.person).count()
    record(p)
    deliver()
    with tenant_transaction(p.clinic.ctx):
        after = Notification.objects.filter(person_ref=p.person)
        assert after.count() == before
        assert not after.filter(kind="booking_cancelled").exists()



# ------------------------------------------------ their own login stops (DEV-335)
def test_the_patients_own_login_stops_and_comes_back_if_withdrawn(p):
    assert p.get("bookings", subject="pat").status_code == 200
    record(p)
    assert p.get("bookings", subject="pat").status_code == 401  # signed out, no hint why
    assert p.get("staff/matches", subject="ops").status_code != 401  # staff unaffected
    p.post(f"people/{p.person}/death/withdraw", {"reason": "recorded_in_error"}, subject="boss2")
    assert p.get("bookings", subject="pat").status_code == 200



# ------------------------------------------------ waiting lists and open lab requests (DEV-343)
def test_waiting_lists_close_and_open_lab_requests_stop_but_delivered_results_stay():
    from modules.exchange.models import ExchangeRequest
    from modules.waitlist.models import WaitEntry
    from tests.test_exchange import Partner, accepted, delivered_with, give_profile, made
    from tests.test_platform import Platform

    Platform()
    p = Practice()
    p.login("boss", FAC, "R13")
    with tenant_transaction(p.clinic.ctx):
        trust.ensure_care_relationship(actor_public_id=p.doc, person_public_id=p.person,
                                       basis=trust.CareBasis.CARE_TEAM,
                                       organization_ref=p.clinic.org, by="system")
        trust.link_person_to_site(p.person, p.clinic.org, "booking")
    lab = Partner("R05", "lab1")
    give_profile(p)
    open_one = made(p, lab, token="ORD-0701")
    working = accepted(p, lab, token="ORD-0702")
    done = delivered_with(p, lab, token="ORD-0703")
    with tenant_transaction(p.clinic.ctx):  # on the waiting list for a full service
        ctx = p.clinic.ctx
        WaitEntry.objects.create(person_ref=p.person, service_ref=p.clinic.service,
                                 organization_ref=p.clinic.org, joined_by_ref=p.patient_actor,
                                 cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    r = record(p)
    got = body(r)
    assert got["requests_cancelled"] == 2 and got["waitlist_closed"] == 1
    from modules.trust import api as trust_api

    plat = next(t for t in trust_api.list_tenants() if t["type"] == "platform")
    with tenant_transaction(trust_api.context_for(plat["public_id"])):
        states = {r.public_id: (r.state, r.shared_identity_enc)
                  for r in ExchangeRequest.objects.all()}
    assert states[open_one] == ("cancelled", "") and states[working] == ("cancelled", "")
    assert states[done][0] == "result_available"  # a delivered result stays
    with tenant_transaction(p.clinic.ctx):
        assert WaitEntry.objects.get(person_ref=p.person).closed_reason == "died"
