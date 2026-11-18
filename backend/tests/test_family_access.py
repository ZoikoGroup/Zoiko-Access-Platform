"""A patient's control over who can act for them: the age-of-majority review, narrowing or ending
a guardian's access, and the consent register (trust/age_review.py, access.py, modules/consent)."""
import datetime as dt
import json

import pytest
from django.db import DatabaseError, connection, transaction

from foundation import periodic
from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from foundation.timeutil import FixedClock, utc_now
from modules.consent import api as consent
from modules.consent.models import ConsentGrant
from modules.evidence.models import AuditEvent
from modules.trust import age_jobs, age_review
from modules.trust import api as trust
from modules.trust.models import ProxyGrant, Tenant
from modules.trust.people import suspend_proxy_grant
from tests.test_api import API
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

SCOPES = ["appointments", "insurance", "messages", "referrals"]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.AGE_OF_MAJORITY = ""
    settings.AGE_OF_MAJORITY_DEFAULT = 18


@pytest.fixture
def p():
    return Practice()


def years_ago(years, *, days=0):
    """A birth date `years` ago today, `days` days earlier (negative: later)."""
    today = utc_now().date()
    try:
        day = today.replace(year=today.year - years)
    except ValueError:                                           # 29 February
        day = today.replace(year=today.year - years, day=28)
    return (day - dt.timedelta(days=days)).isoformat()


def child(p, birth, *, relationship="parent", name="kid", scopes=None):
    """A patient (with their own login) whom `pat` acts for, with an approved grant."""
    actor, person = p.patient(name)
    saved = p.post(f"people/{person}/profile",
                   {"name": "Ravi Rao", "birth_date": birth, "phone": ""}, subject=name)
    assert saved.status_code == 200, saved.content
    with tenant_transaction(p.clinic.ctx):
        trust.link_person_to_site(person, p.clinic.org, "family")     # as a real request does
        trust.link_person_to_site(p.person, p.clinic.org, "family")
        grant = trust.propose_proxy_grant(
            delegate_public_id=p.patient_actor, person_public_id=person,
            relationship_type=relationship, by=p.patient_actor)
        trust.approve_proxy_grant(
            grant.public_id, p.desk, rights=scopes or SCOPES, proof_kind="birth_record",
            proof_ref="proof-0001")
    return actor, person, grant.public_id


def review(p, clock=None):
    with tenant_transaction(p.clinic.ctx):
        return age_review.review_due(clock=clock)


def grant_of(p, grant):
    with tenant_transaction(p.clinic.ctx):
        row = ProxyGrant.objects.get(public_id=grant)
        return row.status, row.review_reason, row.age_reviewed_at, list(row.rights or []), \
            row.valid_until


def audit(p, action):
    with tenant_transaction(p.clinic.ctx):
        return [(r.actor_ref, r.reason_code or (r.details or {}).get("reason", ""))
                for r in AuditEvent.objects.filter(action=action)]


def act(p, grant, verb, subject, **body):
    return p.post(f"access/delegates/{grant}/{verb}", body, subject=subject)


def mine(p, subject="kid"):
    r = p.get("access", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


# ------------------------------------------------------------------ the age of majority
def test_the_age_of_majority_defaults_to_eighteen_and_a_country_can_differ(settings):
    assert age_review.majority_age("") == 18 and age_review.majority_age("GB") == 18
    settings.AGE_OF_MAJORITY = "SG=21, jp=20 ,XX=2,YY=abc,=19"
    assert age_review.majority_age("SG") == 21 and age_review.majority_age("jp") == 20
    assert age_review.majority_age("GB") == 18                   # not listed: the default
    assert age_review.majority_table() == {"SG": 21, "JP": 20}   # nonsense entries are ignored
    settings.AGE_OF_MAJORITY_DEFAULT = 19
    assert age_review.majority_age("GB") == 19


def test_ages_are_counted_from_the_birthday_not_the_year():
    birth = dt.date(2008, 6, 15)
    assert age_review.age_on(birth, dt.date(2026, 6, 14)) == 17
    assert age_review.age_on(birth, dt.date(2026, 6, 15)) == 18
    assert age_review.age_on(birth, dt.date(2026, 12, 31)) == 18


def test_a_child_who_has_come_of_age_has_the_guardians_access_suspended(p):
    _, person, grant = child(p, years_ago(18))
    assert review(p) == 1
    status, reason, reviewed, rights, _ = grant_of(p, grant)
    assert (status, reason, reviewed) == ("suspended", "age_of_majority", None)
    assert rights == SCOPES                                      # kept, so they can be restored
    assert ("system", "age_of_majority") in audit(p, "proxy.age_review")
    with tenant_transaction(p.clinic.ctx):
        kinds = {e.event_type for e in OutboxEvent.objects.all()}
        events = OutboxEvent.objects.filter(event_type="delegation.age_review.v1")
        payload = events[0].payload
    assert {"delegation.age_review.v1", "delegation.revoked.v1"} <= kinds
    assert set(payload) == {"grant_ref", "person_ref"} and payload["person_ref"] == person


def test_a_child_who_is_still_a_minor_keeps_their_guardians(p):
    _, _, grant = child(p, years_ago(18, days=-1))               # eighteen tomorrow
    assert review(p) == 0
    assert grant_of(p, grant)[0] == "active"


def test_the_birthday_itself_counts_and_the_day_before_does_not(p):
    _, _, today = child(p, years_ago(18), name="kid1")
    _, _, tomorrow = child(p, years_ago(18, days=-1), name="kid2")
    assert review(p) == 1
    assert grant_of(p, today)[0] == "suspended" and grant_of(p, tomorrow)[0] == "active"


def test_the_clinics_country_sets_the_age(p, settings):
    settings.AGE_OF_MAJORITY = "SG=21"
    _, _, grant = child(p, years_ago(19))
    with tenant_transaction(p.clinic.ctx):
        Tenant.objects.filter(id=p.clinic.ctx.tenant_id).update(country="SG")
    assert review(p) == 0                                        # nineteen: a minor in SG
    with tenant_transaction(p.clinic.ctx):
        Tenant.objects.filter(id=p.clinic.ctx.tenant_id).update(country="GB")
    assert review(p) == 1 and grant_of(p, grant)[0] == "suspended"


def test_only_parent_and_guardian_access_is_tied_to_age(p):
    _, _, caregiver = child(p, years_ago(40), relationship="caregiver")
    assert review(p) == 0 and grant_of(p, caregiver)[0] == "active"
    _, _, guardian = child(p, years_ago(30), relationship="guardian", name="kid2")
    assert review(p) == 1 and grant_of(p, guardian)[0] == "suspended"


def test_the_review_only_touches_active_grants_and_is_safe_to_repeat(p):
    _, _, grant = child(p, years_ago(20))
    assert review(p) == 1 and review(p) == 0                     # the second run finds nothing
    assert len(audit(p, "proxy.age_review")) == 1


def test_a_grant_the_person_has_already_confirmed_is_never_suspended_again(p):
    _, _, grant = child(p, years_ago(20))
    review(p)
    assert act(p, grant, "keep", "kid").status_code == 200
    assert review(p) == 0 and grant_of(p, grant)[0] == "active"


def test_a_person_with_no_birth_date_on_record_is_left_alone(p):
    actor, person = p.patient("kid")
    with tenant_transaction(p.clinic.ctx):
        grant = trust.propose_proxy_grant(
            delegate_public_id=p.patient_actor, person_public_id=person,
            relationship_type="parent", by=p.patient_actor)
        trust.approve_proxy_grant(grant.public_id, p.desk, rights=SCOPES,
                                  proof_kind="birth_record", proof_ref="proof-0001")
    assert review(p) == 0 and grant_of(p, grant.public_id)[0] == "active"


def test_a_grant_the_clinic_already_suspended_is_not_touched(p):
    _, _, grant = child(p, years_ago(20))
    with tenant_transaction(p.clinic.ctx):
        suspend_proxy_grant(grant, by=p.desk, reason="safeguarding concern")
    assert review(p) == 0
    assert grant_of(p, grant)[1] == ""                            # no age review was started


def test_the_daily_job_covers_every_active_customer_and_is_registered(p):
    _, _, grant = child(p, years_ago(20))
    assert age_jobs.review_all_tenants() >= 1
    assert grant_of(p, grant)[0] == "suspended"
    assert "trust.age_review" in periodic.registered()


def test_a_suspended_guardian_can_no_longer_act_for_the_person(p):
    _, person, grant = child(p, years_ago(20))
    with tenant_transaction(p.clinic.ctx):
        assert person in trust.reachable_persons(p.patient_actor)
    review(p)
    with tenant_transaction(p.clinic.ctx):
        assert person not in trust.reachable_persons(p.patient_actor)


# ------------------------------------------------------------------ the person decides
def test_the_person_keeps_the_guardians_access_and_it_is_recorded(p):
    _, person, grant = child(p, years_ago(20))
    review(p)
    done = act(p, grant, "keep", "kid")
    assert done.status_code == 200 and json.loads(done.content)["delegate"]["status"] == "active"
    status, reason, reviewed, rights, _ = grant_of(p, grant)
    assert (status, reason) == ("active", "") and reviewed is not None and rights == SCOPES
    assert audit(p, "proxy.age_review_kept")
    with tenant_transaction(p.clinic.ctx):
        assert person in trust.reachable_persons(p.patient_actor)


def test_the_person_ends_the_guardians_access_and_repeating_it_is_harmless(p):
    _, _, grant = child(p, years_ago(20))
    review(p)
    assert act(p, grant, "end", "kid").status_code == 200
    assert act(p, grant, "end", "kid").status_code == 200
    status, reason, _, _, until = grant_of(p, grant)
    assert (status, reason) == ("revoked", "") and until is not None
    assert len(audit(p, "proxy.revoke")) == 1


def test_a_person_can_end_a_guardians_access_at_any_time_not_only_after_a_review(p):
    _, _, grant = child(p, years_ago(10))
    assert act(p, grant, "end", "kid").status_code == 200
    assert grant_of(p, grant)[0] == "revoked"


def test_keeping_access_is_only_possible_while_it_is_waiting_for_the_person(p):
    _, _, grant = child(p, years_ago(10))                         # still active: nothing to decide
    assert act(p, grant, "keep", "kid").status_code == 409
    review(p)                                                     # a minor: still nothing
    assert grant_of(p, grant)[0] == "active"


def test_only_the_person_decides_never_the_guardian_or_anyone_else(p):
    _, _, grant = child(p, years_ago(20))
    review(p)
    for who in ("pat", "ops", "doc"):
        assert act(p, grant, "keep", who).status_code in (403, 404)
        assert act(p, grant, "end", who).status_code in (403, 404)
        assert act(p, grant, "narrow", who, scopes=["messages"]).status_code in (403, 404)
    assert grant_of(p, grant)[0] == "suspended"


def test_another_patients_guardian_cannot_be_reached_by_id(p):
    _, _, grant = child(p, years_ago(20), name="kid")
    review(p)
    p.patient("stranger")
    assert act(p, grant, "end", "stranger").status_code == 404
    assert act(p, "doesnotexist00000000", "end", "kid").status_code == 404
    assert grant_of(p, grant)[0] == "suspended"


def test_unknown_actions_and_missing_logins_are_refused(p):
    _, _, grant = child(p, years_ago(20))
    assert act(p, grant, "delete", "kid").status_code == 404
    assert p.client.post(API + f"access/delegates/{grant}/end").status_code == 401
    assert p.get(f"access/delegates/{grant}/end", subject="kid").status_code == 405


# ------------------------------------------------------------------ giving less access
def test_a_person_can_give_a_guardian_fewer_scopes(p):
    _, _, grant = child(p, years_ago(20))
    done = act(p, grant, "narrow", "kid", scopes=["appointments", "messages"])
    assert done.status_code == 200
    assert grant_of(p, grant)[3] == ["appointments", "messages"]
    assert [reason for _, reason in audit(p, "proxy.narrowed")] == ["scopes"]


def test_access_can_never_be_widened_from_here(p):
    _, _, grant = child(p, years_ago(20), scopes=["appointments"])
    for scopes in (["appointments", "referrals"], ["referrals"], ["root"], ["appointments", 5]):
        assert act(p, grant, "narrow", "kid", scopes=scopes).status_code == 422
    assert grant_of(p, grant)[3] == ["appointments"]


def test_taking_away_every_scope_ends_the_access(p):
    _, _, grant = child(p, years_ago(20))
    assert act(p, grant, "narrow", "kid", scopes=[]).status_code == 200
    assert grant_of(p, grant)[0] == "revoked"


def test_a_person_can_bring_the_end_date_forward_but_never_push_it_back(p):
    _, _, grant = child(p, years_ago(20))
    soon = (utc_now().date() + dt.timedelta(days=30)).isoformat()
    sooner = (utc_now().date() + dt.timedelta(days=10)).isoformat()
    later = (utc_now().date() + dt.timedelta(days=60)).isoformat()
    assert act(p, grant, "narrow", "kid", valid_until=soon).status_code == 200      # from none
    assert act(p, grant, "narrow", "kid", valid_until=sooner).status_code == 200
    assert act(p, grant, "narrow", "kid", valid_until=later).status_code == 422     # not later
    until = grant_of(p, grant)[4]
    assert until is not None and until.date().isoformat() == sooner


@pytest.mark.parametrize("value", ["2000-01-01", "not a date", "2999-13-45", 20260101])
def test_a_bad_end_date_is_refused(p, value):
    _, _, grant = child(p, years_ago(20))
    assert act(p, grant, "narrow", "kid", valid_until=value).status_code == 422


def test_narrowing_needs_something_to_change_and_repeating_it_is_harmless(p):
    _, _, grant = child(p, years_ago(20))
    assert act(p, grant, "narrow", "kid").status_code == 422
    assert act(p, grant, "narrow", "kid", scopes=SCOPES).status_code == 200     # no change
    assert audit(p, "proxy.narrowed") == []


def test_an_ended_grant_cannot_be_narrowed(p):
    _, _, grant = child(p, years_ago(20))
    act(p, grant, "end", "kid")
    assert act(p, grant, "narrow", "kid", scopes=["messages"]).status_code == 409


# ------------------------------------------------------------------ the patient's own overview
def test_the_overview_lists_who_may_act_for_me_and_what_they_may_do(p):
    _, person, grant = child(p, years_ago(20))
    review(p)
    view = mine(p)
    assert view["person"] == person and view["age_of_majority"] == 18
    (delegate,) = view["delegates"]
    assert delegate["id"] == grant and delegate["relationship"] == "parent"
    assert delegate["status"] == "suspended" and delegate["waiting_for_you"] is True
    assert delegate["scopes"] == SCOPES and delegate["proof_kind"] == "birth_record"
    assert "proof_ref" not in delegate and "proof-0001" not in json.dumps(view)   # kind only


def test_the_overview_shows_only_my_own_guardians(p):
    child(p, years_ago(20), name="kid")
    p.patient("other")
    assert mine(p, "other")["delegates"] == []
    assert mine(p, "pat")["delegates"] == []                      # pat IS the guardian, not guarded


def test_ended_grants_drop_out_of_the_overview(p):
    _, _, grant = child(p, years_ago(20))
    act(p, grant, "end", "kid")
    assert mine(p)["delegates"] == []


def test_a_login_with_no_record_of_its_own_sees_an_empty_overview_and_cannot_act(p):
    body = json.loads(p.get("access", subject="ops").content)
    assert body["person"] is None and body["delegates"] == []
    _, _, grant = child(p, years_ago(20))
    assert act(p, grant, "end", "ops").status_code == 403
    assert p.post("access/consent", {"purpose": "insurance_details", "grant": True},
                  subject="ops").status_code == 403


def test_the_overview_needs_a_login_and_the_right_method(p):
    assert p.client.get(API + "access").status_code == 401
    assert p.post("access", {}, subject="kid").status_code == 405


# ------------------------------------------------------------------ the consent register
def test_nothing_is_shared_until_the_patient_says_so(p):
    _, person = p.patient("kid")
    (item,) = mine(p)["consents"]
    assert item["purpose"] == "insurance_details" and item["shared"] is False
    assert item["state"] == "withdrawn" and item["expires_at"] is None
    with tenant_transaction(p.clinic.ctx):
        assert consent.is_granted(person, "insurance_details") is False


def say(p, grant, subject="kid", **over):
    return p.post("access/consent", {"purpose": "insurance_details", "grant": grant, **over},
                  subject=subject)


def test_a_yes_can_last_for_a_number_of_days_and_then_stops_counting(p):
    _, person = p.patient("kid")
    done = say(p, True, days=30)
    assert done.status_code == 200
    item = json.loads(done.content)["consent"]
    assert item["shared"] is True and item["state"] == "granted" and item["expires_at"]
    later = FixedClock(utc_now() + dt.timedelta(days=31))
    with tenant_transaction(p.clinic.ctx):
        assert consent.is_granted(person, "insurance_details") is True
        assert consent.is_granted(person, "insurance_details", clock=later) is False
        assert consent.list_for_person(person, clock=later)[0]["state"] == "expired"


def test_an_open_ended_yes_lasts_until_it_is_withdrawn(p):
    _, person = p.patient("kid")
    say(p, True)
    far = FixedClock(utc_now() + dt.timedelta(days=3000))
    with tenant_transaction(p.clinic.ctx):
        assert consent.is_granted(person, "insurance_details", clock=far) is True
    assert say(p, False).status_code == 200
    with tenant_transaction(p.clinic.ctx):
        assert consent.is_granted(person, "insurance_details") is False


def test_every_change_is_one_chained_event_and_a_repeat_adds_nothing(p):
    _, person = p.patient("kid")
    say(p, True)
    say(p, True)                                                  # the same again: nothing new
    say(p, True, days=10)                                         # a new end date: a new event
    say(p, False)
    say(p, False)
    with tenant_transaction(p.clinic.ctx):
        history = consent.history(person, "insurance_details")
    assert history["chain_ok"] is True
    assert [e["kind"] for e in history["events"]] == ["granted", "granted", "withdrawn"]
    assert history["events"][1]["expires_at"] and history["events"][0]["expires_at"] is None


def test_a_history_that_was_tampered_with_no_longer_checks_out(p):
    _, person = p.patient("kid")
    say(p, True)
    say(p, False)
    with tenant_transaction(p.clinic.ctx):
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE consent_consentevent DISABLE TRIGGER USER")
            cursor.execute("UPDATE consent_consentevent SET actor_ref = 'forged' WHERE seq = 1")
            cursor.execute("ALTER TABLE consent_consentevent ENABLE TRIGGER USER")
        assert consent.history(person, "insurance_details")["chain_ok"] is False


def test_the_database_itself_refuses_to_change_or_remove_consent_history(p):
    p.patient("kid")
    say(p, True)
    for statement in ("UPDATE consent_consentevent SET kind = 'withdrawn'",
                      "DELETE FROM consent_consentevent"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement)


def test_both_new_tables_are_protected_by_row_level_security():
    with connection.cursor() as cursor:
        cursor.execute("SELECT relname, relrowsecurity FROM pg_class "
                       "WHERE relname IN ('consent_consentgrant', 'consent_consentevent')")
        assert sorted(cursor.fetchall()) == [("consent_consentevent", True),
                                             ("consent_consentgrant", True)]


@pytest.mark.parametrize("body", [
    {"purpose": "nothing"}, {"purpose": 5}, {"purpose": None}, {"grant": "yes"}, {"grant": None},
    {"days": 0}, {"days": -3}, {"days": True}, {"days": "30"}, {"days": 4000}, {"days": 1.5}])
def test_bad_consent_requests_are_refused_and_change_nothing(p, body):
    p.patient("kid")
    good = {"purpose": "insurance_details", "grant": True}
    assert p.post("access/consent", {**good, **body}, subject="kid").status_code == 422
    assert mine(p)["consents"][0]["shared"] is False


def test_consent_needs_a_login_and_the_right_method(p):
    assert p.client.post(API + "access/consent").status_code == 401
    assert p.get("access/consent", subject="kid").status_code == 405


def test_one_patients_choice_never_affects_another(p):
    _, a = p.patient("kid")
    _, b = p.patient("other")
    say(p, True, "kid")
    with tenant_transaction(p.clinic.ctx):
        assert consent.states([a, b], "insurance_details") == {a: True}      # b made no choice
        assert consent.is_granted(b, "insurance_details") is False
    assert mine(p, "other")["consents"][0]["shared"] is False


def test_a_consent_change_is_audited_and_announced_without_personal_details(p):
    p.patient("kid")
    say(p, True, days=5)
    say(p, False)
    assert [r for _, r in audit(p, "consent.granted")] == ["insurance_details"]
    assert [r for _, r in audit(p, "consent.withdrawn")] == ["insurance_details"]
    with tenant_transaction(p.clinic.ctx):
        events = list(OutboxEvent.objects.filter(event_type="consent.changed.v1"))
    assert len(events) == 2
    for event in events:
        assert set(event.payload) == {"consent_ref", "purpose", "state"}


def test_unknown_purposes_and_audiences_are_refused_by_the_register_itself(p):
    _, person = p.patient("kid")
    from foundation.errors import ZoikoError
    with tenant_transaction(p.clinic.ctx):
        for over in ({"purpose": "nothing"}, {"audience": "everyone"}, {"grant": "yes"}):
            args = {"person_ref": person, "purpose": "insurance_details", "grant": True,
                    "actor_ref": "x", **over}
            with pytest.raises(ZoikoError):
                consent.set_consent(**args)
    with tenant_transaction(p.clinic.ctx):
        assert ConsentGrant.objects.count() == 0


# ------------------------------------------------------------------ insurance sharing rides on it
def staff_sees(p, person):
    r = p.get(f"people/{person}/coverage", subject="ops")
    assert r.status_code == 200
    return json.loads(r.content)


def add_insurance(p, subject="pat"):
    r = p.post("coverage", {"insurer": "Star Health", "plan": "Family",
                            "member_id": "STAR-12345-6789"}, subject=subject)
    assert r.status_code == 201, r.content


@pytest.fixture
def insured(p):
    from modules.trust import api as trust_api
    with tenant_transaction(p.clinic.ctx):
        trust_api.link_person_to_site(p.person, p.clinic.org, "booking")
    add_insurance(p)
    return p


def test_staff_see_insurance_only_while_the_consent_lasts(insured):
    p = insured
    assert staff_sees(p, p.person) == {"coverage": [], "shared": False}
    assert p.post("access/consent", {"purpose": "insurance_details", "grant": True, "days": 30},
                  subject="pat").status_code == 200
    assert staff_sees(p, p.person)["shared"] is True
    with tenant_transaction(p.clinic.ctx):                        # the end date passes
        ConsentGrant.objects.update(expires_at=utc_now() - dt.timedelta(days=1))
    assert staff_sees(p, p.person) == {"coverage": [], "shared": False}


def test_the_older_sharing_switch_and_the_new_consent_are_the_same_choice(insured):
    p = insured
    assert p.post("coverage/sharing", {"share": True}, subject="pat").status_code == 200
    assert mine(p, "pat")["consents"][0]["shared"] is True
    assert staff_sees(p, p.person)["shared"] is True
    assert p.post("access/consent", {"purpose": "insurance_details", "grant": False},
                  subject="pat").status_code == 200
    sharing = json.loads(p.get("coverage/sharing", subject="pat").content)["sharing"]
    assert staff_sees(p, p.person)["shared"] is False
    assert sharing[0]["person"] == p.person


def test_the_older_switch_can_now_carry_an_end_date(insured):
    p = insured
    done = p.post("coverage/sharing", {"share": True, "days": 7}, subject="pat")
    assert done.status_code == 200 and json.loads(done.content)["sharing"]["until"]
    assert p.post("coverage/sharing", {"share": True, "days": 0},
                  subject="pat").status_code == 422


def test_a_choice_recorded_before_the_register_existed_still_counts(insured):
    from modules.coverage.models import InsuranceSharing
    p = insured
    with tenant_transaction(p.clinic.ctx):
        InsuranceSharing.objects.create(
            person_ref=p.person, share_with_staff=True, changed_by_ref=p.patient_actor,
            cell_id=p.clinic.ctx.cell_id, tenant_id=p.clinic.ctx.tenant_id)
        assert ConsentGrant.objects.count() == 0
    assert staff_sees(p, p.person)["shared"] is True
    assert p.post("access/consent", {"purpose": "insurance_details", "grant": False},
                  subject="pat").status_code == 200
    assert staff_sees(p, p.person)["shared"] is False             # the new choice wins


def test_the_billing_overview_follows_the_same_consent(insured):
    p = insured
    boss = p.login("boss", "facility_user", "R13")
    assert boss
    before = json.loads(p.get("staff/billing", subject="boss").content)
    assert before["counts"]["not_shared"] >= 1
    p.post("access/consent", {"purpose": "insurance_details", "grant": True}, subject="pat")
    after = json.loads(p.get("staff/billing", subject="boss").content)
    assert after["counts"]["not_shared"] == before["counts"]["not_shared"] - 1


def test_one_customers_choices_are_invisible_to_another(insured):
    p = insured
    other = Practice()
    p.post("access/consent", {"purpose": "insurance_details", "grant": True}, subject="pat")
    with tenant_transaction(other.clinic.ctx):
        assert consent.states([p.person], "insurance_details") == {}


# ---------------------------------------------- the clinic cannot override the person
def test_the_clinic_cannot_resume_a_guardian_who_was_paused_for_age(p):
    _, _, grant = child(p, years_ago(20))
    review(p)
    resumed = p.post(f"staff/family-requests/{grant}/resume", {}, subject="ops")
    assert resumed.status_code == 409
    assert grant_of(p, grant)[0] == "suspended"                   # still the person's decision


def test_a_guardian_the_clinic_paused_for_another_reason_can_still_be_resumed(p):
    _, _, grant = child(p, years_ago(10))
    with tenant_transaction(p.clinic.ctx):
        suspend_proxy_grant(grant, by=p.desk, reason="safeguarding concern")
    assert p.post(f"staff/family-requests/{grant}/resume", {}, subject="ops").status_code == 200
    assert grant_of(p, grant)[0] == "active"


def test_the_staff_list_shows_why_a_guardian_is_paused(p):
    _, _, grant = child(p, years_ago(20))
    review(p)
    listed = json.loads(p.get("staff/family-requests", subject="ops").content)
    rows = [r for key in listed for r in (listed[key] if isinstance(listed[key], list) else [])
            if isinstance(r, dict) and r.get("id") == grant]
    assert rows and rows[0]["review_reason"] == "age_of_majority"
