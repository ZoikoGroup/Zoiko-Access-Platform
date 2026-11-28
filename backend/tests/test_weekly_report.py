"""The weekly summary email to a clinic's administrators (DEV-392, from the maruthi branch): off
until the administrator turns it on, once a week, counts only, small numbers hidden like the
Reports page, unapproved measures not shown, and its wording held until approved. Mostly what must
NOT happen."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import MailState, QueuedEmail
from modules.comms import report_email
from modules.trust import api as trust
from tests.test_clinician import Practice
from tests.test_digest import give_email

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STAFF_APP_URL = "http://staff.test"
    settings.ZOIKO_TEMPLATE_GATING = "report"
    settings.ZOIKO_MEASURE_GATING = "report"


@pytest.fixture
def p():
    practice = Practice()
    practice.boss_id = practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    give_email(practice, practice.boss_id, "boss@clinic.example")
    return practice


def settings_of(p, subject="boss"):
    return json.loads(p.get("staff/settings", subject=subject).content)


def turn(p, on, subject="boss"):
    return p.post("staff/settings", {"weekly_report": on}, subject=subject)


def run(p):
    with tenant_transaction(p.clinic.ctx):
        return report_email.send_weekly_report()


def sent():
    return list(QueuedEmail.objects.filter(kind=report_email.KIND))


# ------------------------------------------------------------------ the clinic's choice
def test_it_is_off_until_the_administrator_turns_it_on(p):
    assert settings_of(p)["weekly_report"] is False
    assert run(p) == 0 and sent() == []
    assert turn(p, True).status_code == 200
    data = settings_of(p)
    assert data["weekly_report"] is True and data["weekly_report_recipients"] == 1


def test_only_the_administrator_may_turn_it_on_and_only_with_true_or_false(p):
    assert turn(p, True, subject="ops").status_code == 403
    assert turn(p, "yes").status_code == 422
    assert settings_of(p)["weekly_report"] is False


def test_turning_it_on_does_not_change_the_response_target(p):
    before = settings_of(p)["response_target_hours"]
    turn(p, True)
    assert settings_of(p)["response_target_hours"] == before


# ------------------------------------------------------------------ the email
def test_once_on_the_administrators_get_one_summary_a_week(p):
    turn(p, True)
    assert run(p) == 1
    assert run(p) == 0                                     # already sent this week
    [mail] = sent()
    assert mail.to_address == "boss@clinic.example" and mail.template_key == "weekly_report"
    assert mail.subject.startswith("Weekly summary for ") and "-W" in mail.subject


def test_counts_only_with_small_numbers_hidden(p):
    turn(p, True)
    run(p)
    body = sent()[0].body
    assert "Bookings made:" in body and "Waiting list:" in body
    assert "Counts under 5 are not shown" in body and "http://staff.test" in body
    for private in (p.person, "pat", "doc"):
        assert private not in body


def test_an_unverified_administrator_gets_nothing():
    practice = Practice()
    boss = practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    give_email(practice, boss, "boss@clinic.example", verified=False)
    turn(practice, True)
    assert run(practice) == 0


def test_unapproved_measures_say_not_shown(p, settings):
    settings.ZOIKO_MEASURE_GATING = "enforce"
    turn(p, True)
    run(p)
    assert sent()[0].body.count("not shown (measure not approved)") == 3


def test_unapproved_wording_is_held_not_sent(p, settings):
    settings.ZOIKO_TEMPLATE_GATING = "enforce"
    turn(p, True)
    run(p)
    assert sent()[0].state == MailState.HELD


def test_the_summary_lines_hide_small_numbers_and_a_rate_from_few_cases():
    report = {"bookings": {"total": 12, "status": {"requested": 0, "confirmed": 9,
                                                   "cancelled": None},
                           "attendance": {"attended": 6, "no_show": None, "not_recorded": 2}},
              "waiting_list": {"joined": None, "booked": 0, "left": 0, "waiting_now": 7}}
    lines = report_email.summary_lines(report)
    assert "- Bookings made: 12 (9 confirmed, fewer than 5 cancelled)" in lines
    assert "- Visits: 6 attended, fewer than 5 missed" in lines
    assert "Missed-visit rate" not in lines                 # a hidden count: no rate
    assert "- Waiting list: fewer than 5 joined, 0 booked, 7 waiting now" in lines
