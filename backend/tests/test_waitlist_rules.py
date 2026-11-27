"""Waiting-list rules: staff-set priority, and each clinic's own offer window (table row 6)."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock, utc_now
from modules.waitlist import services as waitlist_services
from tests.test_waitlist import Waiting, deliver, offers_in_inbox

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    from modules.comms import api as comms
    from modules.waitlist.subscribers import register_subscribers

    comms.register_subscribers()
    register_subscribers()


@pytest.fixture
def w():
    made = Waiting()
    made.login("boss", "facility_user", "R13")
    return made


def entry_id(w, subject):
    return w.entries(subject)[0]["id"]


def set_priority(w, subject, level, reason="", who="desk"):
    return w.post(f"staff/waitlist/{entry_id(w, subject)}/priority",
                  {"priority": level, "reason": reason}, subject=who)


def staff_rows(w):
    r = w.get("staff/waitlist", subject="desk")
    assert r.status_code == 200, r.content
    return json.loads(r.content)["entries"]


# ------------------------------------------------------------------ priority
def test_an_urgent_person_is_offered_the_next_time_before_someone_who_joined_earlier(w):
    w.join("pat")
    w.join("pat2")                                              # pat joined first
    r = set_priority(w, "pat2", 2, "needs a heart check within the week")
    assert r.status_code == 200 and json.loads(r.content)["entry"]["priority"] == 2
    w.add_times(1)
    deliver()
    assert w.entries("pat2")[0]["state"] == "offered"
    assert w.entries("pat")[0]["state"] == "waiting"            # one time, one offer
    assert [n["title"] for n in offers_in_inbox(w, "pat2")] == ["A time opened up"]
    assert offers_in_inbox(w, "pat") == []


def test_people_at_the_same_level_keep_the_order_they_joined(w):
    w.join("pat")
    w.join("pat2")
    set_priority(w, "pat", 1, "referred by a doctor")
    set_priority(w, "pat2", 1, "referred by a doctor")
    w.add_times(1)
    deliver()
    assert w.entries("pat")[0]["state"] == "offered"
    assert w.entries("pat2")[0]["state"] == "waiting"


def test_the_staff_list_shows_the_queue_in_offer_order_with_each_place(w):
    w.join("pat")
    w.join("pat2")
    set_priority(w, "pat2", 1, "long journey, only free on weekdays")
    rows = staff_rows(w)
    assert [(r["priority"], r["place"]) for r in rows] == [(1, 1), (0, 2)]
    assert rows[0]["priority_reason"] == "long journey, only free on weekdays"
    assert w.entries("pat2")[0].get("priority") is None        # a patient never sees the ranking


def test_going_back_to_routine_clears_the_reason(w):
    w.join("pat")
    set_priority(w, "pat", 2, "urgent review")
    done = json.loads(set_priority(w, "pat", 0).content)["entry"]
    assert done["priority"] == 0 and done["priority_reason"] == ""


def test_a_level_above_routine_needs_a_reason_and_only_known_levels_are_allowed(w):
    w.join("pat")
    assert set_priority(w, "pat", 1, "").status_code == 422
    assert set_priority(w, "pat", 1, "   ").status_code == 422
    assert set_priority(w, "pat", 3, "x").status_code == 422
    assert set_priority(w, "pat", -1, "x").status_code == 422
    assert set_priority(w, "pat", True, "x").status_code == 422
    assert set_priority(w, "pat", "2", "x").status_code == 422
    assert set_priority(w, "pat", 1, "x" * 201).status_code == 422
    assert staff_rows(w)[0]["priority"] == 0                    # nothing changed


def test_a_patient_cannot_rank_themselves_or_anyone_else(w):
    w.join("pat")
    w.join("pat2")
    for who in ("pat", "pat2"):
        denied = set_priority(w, "pat", 2, "I am in a hurry", who=who)
        assert denied.status_code == 403
    assert staff_rows(w)[0]["priority"] == 0


def test_staff_of_another_clinic_cannot_change_the_queue(w):
    w.join("pat")
    other = Waiting()                                          # a second, separate clinic
    assert other.post(f"staff/waitlist/{entry_id(w, 'pat')}/priority",
                      {"priority": 2, "reason": "x"}, subject="desk").status_code in (403, 404)
    assert staff_rows(w)[0]["priority"] == 0


def test_a_closed_entry_cannot_be_ranked_and_an_unknown_one_is_not_found(w):
    w.join("pat")
    entry = entry_id(w, "pat")
    w.post(f"waitlist/{entry}/leave", subject="pat")
    assert w.post(f"staff/waitlist/{entry}/priority", {"priority": 1, "reason": "x"},
                  subject="desk").status_code == 409
    assert w.post("staff/waitlist/nosuchentry000000000/priority", {"priority": 1, "reason": "x"},
                  subject="desk").status_code == 404


def test_setting_the_same_priority_twice_is_harmless(w):
    w.join("pat")
    assert set_priority(w, "pat", 1, "same reason").status_code == 200
    assert set_priority(w, "pat", 1, "same reason").status_code == 200
    assert staff_rows(w)[0]["priority"] == 1


def test_a_priority_change_is_audited_and_announced_without_any_personal_detail(w):
    w.join("pat")
    set_priority(w, "pat", 2, "urgent review")
    from foundation.models import OutboxEvent

    with tenant_transaction(w.clinic.ctx):
        events = list(OutboxEvent.objects.filter(event_type="waitlist.prioritized.v1"))
    assert len(events) == 1
    assert set(events[0].payload) == {"entry_ref", "state", "actor_ref"}
    assert "urgent review" not in json.dumps(events[0].payload)


# ------------------------------------------------------------------ the clinic's own offer window
def settings_of(w, subject="boss"):
    r = w.get("staff/settings", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def test_the_clinic_administrator_sets_its_own_offer_window(w, settings):
    settings.WAITLIST_OFFER_HOURS = 24
    assert settings_of(w)["waitlist_offer_hours"] == 24
    assert w.post("staff/settings", {"waitlist_offer_hours": 6}, subject="boss").status_code == 200
    now = settings_of(w)
    assert now["waitlist_offer_hours"] == 6 and now["waitlist_offer_default"] == 24
    w.post("staff/settings", {"waitlist_offer_hours": None}, subject="boss")
    assert settings_of(w)["waitlist_offer_hours"] == 24         # back to the service default


def test_only_the_administrator_may_set_it_and_only_sensible_values_are_accepted(w):
    assert w.post("staff/settings", {"waitlist_offer_hours": 6}, subject="desk").status_code == 403
    for bad in (0, 169, -1, "6", 1.5, True):
        r = w.post("staff/settings", {"waitlist_offer_hours": bad}, subject="boss")
        assert r.status_code == 422, bad
    assert settings_of(w)["waitlist_offer_hours"] == 24


def test_setting_one_rule_leaves_the_other_alone(w):
    w.post("staff/settings", {"hours": 30}, subject="boss")
    w.post("staff/settings", {"waitlist_offer_hours": 5}, subject="boss")
    now = settings_of(w)
    assert (now["response_target_hours"], now["waitlist_offer_hours"]) == (30, 5)
    w.post("staff/settings", {"hours": 40}, subject="boss")
    assert settings_of(w)["waitlist_offer_hours"] == 5


def offered_entry(w):
    w.join("pat")
    w.add_times(1)
    deliver()
    assert w.entries("pat")[0]["state"] == "offered"


def test_an_unused_offer_returns_to_the_queue_after_the_clinics_own_window(w):
    w.post("staff/settings", {"waitlist_offer_hours": 2}, subject="boss")
    offered_entry(w)
    soon = FixedClock(utc_now() + dt.timedelta(hours=1))
    later = FixedClock(utc_now() + dt.timedelta(hours=3))
    with tenant_transaction(w.clinic.ctx):
        assert waitlist_services.expire_old_offers(clock=soon) == 0
        assert waitlist_services.expire_old_offers(clock=later) == 1
    assert w.entries("pat")[0]["state"] == "waiting"


# ------------------------------------------------------------------ a service's own window
def rules(w, who="boss"):
    r = w.get("staff/waitlist-rules", subject=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def cardio_rule(w, who="boss"):
    return next(s for s in rules(w, who)["services"] if s["id"] == w.cardio)


def set_rule(w, hours, who="boss", service=None):
    return w.post("staff/waitlist-rules", {"service": service or w.cardio, "hours": hours},
                  subject=who)


def test_a_service_follows_the_clinic_until_it_has_its_own_rule(w, settings):
    settings.WAITLIST_OFFER_HOURS = 24
    w.post("staff/settings", {"waitlist_offer_hours": 6}, subject="boss")
    assert (cardio_rule(w)["hours"], cardio_rule(w)["effective_hours"]) == (None, 6)
    assert set_rule(w, 2).status_code == 200
    assert (cardio_rule(w)["hours"], cardio_rule(w)["effective_hours"]) == (2, 2)
    others = [s for s in rules(w)["services"] if s["id"] != w.cardio]
    assert others and all(s["effective_hours"] == 6 for s in others)
    assert rules(w)["clinic_hours"] == 6                        # the clinic's rule is untouched
    set_rule(w, None)
    assert cardio_rule(w)["effective_hours"] == 6


def test_an_offer_for_a_service_with_its_own_window_returns_after_that_window(w):
    w.post("staff/settings", {"waitlist_offer_hours": 10}, subject="boss")
    set_rule(w, 2)
    offered_entry(w)
    with tenant_transaction(w.clinic.ctx):
        assert waitlist_services.expire_old_offers(
            clock=FixedClock(utc_now() + dt.timedelta(hours=1))) == 0
        assert waitlist_services.expire_old_offers(
            clock=FixedClock(utc_now() + dt.timedelta(hours=3))) == 1


def test_only_the_administrator_changes_a_services_window_and_patients_see_nothing(w):
    assert cardio_rule(w, "desk")["hours"] is None and rules(w, "desk")["can_set"] is False
    assert set_rule(w, 2, who="desk").status_code == 403
    assert set_rule(w, 2, who="pat").status_code == 403
    assert w.get("staff/waitlist-rules", subject="pat").status_code == 403
    assert cardio_rule(w)["hours"] is None


@pytest.mark.parametrize("bad", [0, 169, -1, "6", 1.5, True])
def test_a_services_window_must_be_a_sensible_number_of_hours(w, bad):
    assert set_rule(w, bad).status_code == 422
    assert cardio_rule(w)["hours"] is None


def test_another_clinics_service_is_not_found(w):
    other = Waiting()
    assert set_rule(w, 2, service=other.cardio).status_code == 404
    assert set_rule(w, 2, service="nosuchservice00000000").status_code == 404


def test_a_clinic_without_its_own_window_uses_the_service_default(w, settings):
    settings.WAITLIST_OFFER_HOURS = 24
    offered_entry(w)
    with tenant_transaction(w.clinic.ctx):
        assert waitlist_services.expire_old_offers(
            clock=FixedClock(utc_now() + dt.timedelta(hours=23))) == 0
        assert waitlist_services.expire_old_offers(
            clock=FixedClock(utc_now() + dt.timedelta(hours=25))) == 1
