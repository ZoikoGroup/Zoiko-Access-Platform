"""Partner teams, employer programme counts, payer plans and entitlement checks (DEV-159)."""
import json

import pytest
from django.core import mail

from foundation import mail as mailer
from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from modules.trust.models import Actor, RoleAssignment
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
FAC, PAY, EMP = (trust.ActorType.FACILITY_USER, trust.ActorType.PAYER_USER,
                 trust.ActorType.EMPLOYER_USER)


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.SPONSOR_MIN_GROUP = 10
    settings.PARTNER_APP_URL = "http://partners.test"


@pytest.fixture
def c():
    return Practice()


def body(r):
    return json.loads(r.content)


def refs(n, prefix="emp"):
    return [f"{prefix}-{i:04d}" for i in range(n)]


# ------------------------------------------------------------------ partner teams
def test_a_partner_user_adds_a_colleague_of_their_own_kind_who_is_invited_to_the_partner_app(c):
    c.login("lab1", FAC, "R05")
    r = c.post("portal/team", {"email": "second.lab@example.org"}, subject="lab1")
    assert r.status_code == 201 and body(r)["member"]["role"] == "R05"
    with tenant_transaction(c.clinic.ctx):
        actor = Actor.objects.get(subject="second.lab@example.org")
        assert list(RoleAssignment.objects.filter(actor=actor).values_list(
            "role__role_code", flat=True)) == ["R05"]
    mailer.send_due()
    assert "http://partners.test/?action=reset&token=" in str(mail.outbox[-1].body)
    team = body(c.get("portal/team", subject="lab1"))["team"]
    assert {m["login"] for m in team} >= {"second.lab@example.org"}


def test_a_payer_or_employer_user_adds_colleagues_with_the_same_kind_of_login(c):
    c.login("pay", PAY, "R07")
    c.login("hr", EMP, "R08")
    assert c.post("portal/team", {"email": "p2@example.org"}, subject="pay").status_code == 201
    assert c.post("portal/team", {"email": "h2@example.org"}, subject="hr").status_code == 201
    with tenant_transaction(c.clinic.ctx):
        assert Actor.objects.get(subject="p2@example.org").actor_type == "payer_user"
        assert Actor.objects.get(subject="h2@example.org").actor_type == "employer_user"


def test_a_duplicate_login_a_bad_address_and_other_kinds_of_account_are_refused(c):
    c.login("lab1", FAC, "R05")
    assert c.post("portal/team", {"email": "x@example.org"}, subject="lab1").status_code == 201
    assert c.post("portal/team", {"email": "x@example.org"}, subject="lab1").status_code == 409
    assert c.post("portal/team", {"email": "not-an-address"}, subject="lab1").status_code == 422
    for subject in ("pat", "ops", "doc"):
        assert c.post("portal/team", {"email": "y@example.org"}, subject=subject).status_code == 403
        assert c.get("portal/team", subject=subject).status_code == 403


def test_a_colleague_can_be_suspended_and_reinstated_but_never_yourself(c):
    mine = c.login("lab1", FAC, "R05")
    other = c.login("lab2", FAC, "R05")
    assert c.post(f"portal/team/{mine}/suspend", {}, subject="lab1").status_code == 403
    assert c.post(f"portal/team/{other}/suspend", {}, subject="lab1").status_code == 200
    with tenant_transaction(c.clinic.ctx):
        assert Actor.objects.get(public_id=other).status == "suspended"
    assert c.post(f"portal/team/{other}/reinstate", {}, subject="lab1").status_code == 200
    assert c.post(f"portal/team/{other}/dance", {}, subject="lab1").status_code == 404


def test_a_partner_colleague_reviews_a_listing_but_nobody_reviews_their_own_request(c):
    c.login("lab1", FAC, "R05")
    c.login("lab2", FAC, "R05")
    made = c.post("services", {"organization": c.clinic.org, "name": "Blood tests",
                               "category": "diagnostics", "duration_minutes": 15}, subject="lab1")
    service = body(made)["service"]["id"]
    url = f"services/{service}/listing"
    c.post(url, {"action": "request", "note": "licence-0001"}, subject="lab1")
    assert c.post(url, {"action": "review"}, subject="lab1").status_code == 403   # not their own
    assert c.post(url, {"action": "review"}, subject="lab2").status_code == 200   # a colleague
    assert c.post(url, {"action": "publish"}, subject="lab1").status_code == 403  # still not theirs
    assert c.post(url, {"action": "publish"}, subject="lab2").status_code == 200
    state = c.post(url, {"action": "suspend", "note": "paused"}, subject="lab2")
    assert body(state)["service"]["listing_state"] == "suspended"


# ------------------------------------------------------------------ employer counts
def enrol(c, items, subject="hr"):
    return c.post("portal/program/enrol", {"refs": items}, subject=subject)


def summary(c, subject="hr"):
    return body(c.get("portal/program", subject=subject))


def test_an_empty_programme_shows_zero_not_hidden(c):
    c.login("hr", EMP, "R08")
    s = summary(c)
    assert s["min_group"] == 10 and s["enrolled"] == {"value": 0, "hidden": False}


def test_a_big_enough_group_is_counted_and_a_small_one_is_hidden(c):
    c.login("hr", EMP, "R08")
    enrol(c, refs(3))
    assert summary(c)["enrolled"] == {"value": None, "hidden": True}   
    enrol(c, refs(9))
    assert summary(c)["enrolled"] == {"value": None, "hidden": True}      # 9 still hidden
    assert body(enrol(c, refs(10)))["added"] == 1                         # the tenth joins
    assert summary(c)["enrolled"] == {"value": 10, "hidden": False}


def test_withdrawing_changes_the_counts_and_a_small_withdrawn_group_is_hidden(c):
    c.login("hr", EMP, "R08")
    enrol(c, refs(12))
    r = c.post("portal/program/withdraw", {"refs": refs(2)}, subject="hr")
    assert body(r)["withdrawn"] == 2
    s = summary(c)
    assert s["enrolled"] == {"value": 10, "hidden": False}
    assert s["withdrawn"] == {"value": None, "hidden": True}
    assert body(enrol(c, refs(2)))["added"] == 2                          # coming back counts


def test_repeating_an_enrolment_adds_nobody(c):
    c.login("hr", EMP, "R08")
    assert body(enrol(c, refs(12)))["added"] == 12
    again = body(enrol(c, refs(12)))
    assert again == {"added": 0, "already_enrolled": 12}


@pytest.mark.parametrize("bad", [None, "emp-0001", [], [1], ["ab"], ["has space ok"],
                                 ["x" * 41], [f"emp-{i:05d}" for i in range(201)]])
def test_only_short_plain_references_in_a_sensible_batch_are_accepted(c, bad):
    c.login("hr", EMP, "R08")
    assert enrol(c, bad).status_code == 422
    assert summary(c)["enrolled"]["value"] == 0


def test_the_programme_never_hands_back_who_is_in_it(c):
    c.login("hr", EMP, "R08")
    enrol(c, refs(15, "secret-employee"))
    for path in ("portal/program", "portal/program/enrol"):
        text = c.get(path, subject="hr").content.decode() if path.endswith("program") else ""
        assert "secret-employee" not in text
    assert c.get("portal/program/members", subject="hr").status_code in (404, 405)


def test_only_an_employer_login_uses_the_programme(c):
    for subject, kind, role in (("pay", PAY, "R07"), ("lab", FAC, "R05")):
        c.login(subject, kind, role)
        assert c.get("portal/program", subject=subject).status_code == 403
        assert enrol(c, refs(3), subject=subject).status_code == 403
    assert enrol(c, refs(3), subject="pat").status_code == 403


# ------------------------------------------------------------------ payer plans and checks
def plan(c, code="GOLD", cats=("general_physician", "diagnostics"), share=20, subject="pay"):
    return c.post("portal/plans", {"code": code, "name": "Gold plan",
                                   "covered_categories": list(cats), "copay_percent": share},
                  subject=subject)


def member(c, plan_code="GOLD", member_id="MEM-0001", subject="pay", **extra):
    return c.post("portal/members", {"plan": plan_code, "member_id": member_id, **extra},
                  subject=subject)


def check(c, member_id="MEM-0001", category="general_physician", subject="pay", **extra):
    return body(c.post("portal/entitlement", {"member_id": member_id, "category": category,
                                              **extra}, subject=subject))


@pytest.fixture
def payer(c):
    c.login("pay", PAY, "R07")
    return c


def test_a_plan_is_made_listed_and_changed(payer):
    made = plan(payer)
    assert made.status_code == 201 and body(made)["plan"]["copay_percent"] == 20
    plan_id = body(made)["plan"]["id"]
    assert [p["code"] for p in body(payer.get("portal/plans", subject="pay"))["plans"]] == ["GOLD"]
    upd = payer.post(f"portal/plans/{plan_id}", {"copay_percent": 10, "active": False},
                     subject="pay")
    assert body(upd)["plan"]["copay_percent"] == 10 and body(upd)["plan"]["active"] is False


@pytest.mark.parametrize("over", [{"code": "gold"}, {"code": ""}, {"code": "A"}, {"name": "x"},
                                  {"covered_categories": []}, {"covered_categories": ["Bad Name"]},
                                  {"copay_percent": 101}, {"copay_percent": -1},
                                  {"copay_percent": True}, {"copay_percent": "5"}])
def test_a_badly_formed_plan_is_refused(payer, over):
    fields = {"code": "GOLD", "name": "Gold plan", "covered_categories": ["diagnostics"],
              "copay_percent": 0, **over}
    assert payer.post("portal/plans", fields, subject="pay").status_code == 422


def test_two_plans_cannot_share_a_code(payer):
    assert plan(payer).status_code == 201
    assert plan(payer).status_code == 409


def test_a_member_is_added_once_and_shown_masked(payer):
    plan(payer)
    r = member(payer, valid_from="2026-01-01", valid_until="2026-12-31")
    assert r.status_code == 201 and body(r)["member"]["member"] == "…0001"
    assert member(payer).status_code == 409                     # the same number twice
    assert member(payer, plan_code="NOPE", member_id="MEM-0002").status_code == 422
    assert member(payer, member_id="MEM-0003", valid_from="2026-02-01",
                  valid_until="2026-01-01").status_code == 422
    assert member(payer, member_id="MEM-0004", valid_from="not-a-date").status_code == 422
    listed = body(payer.get("portal/members", subject="pay"))["members"]
    assert "MEM-0001" not in json.dumps(listed) and listed[0]["member"] == "…0001"


def test_the_check_says_covered_with_the_patients_share(payer):
    plan(payer)
    member(payer)
    assert check(payer) == {"covered": True, "reason": "covered", "plan": "GOLD",
                            "copay_percent": 20}


def test_the_check_always_says_exactly_why_not(payer):
    plan_id = body(plan(payer))["plan"]["id"]
    member(payer, valid_from="2026-01-01", valid_until="2026-12-31")
    member(payer, member_id="MEM-0002")
    assert check(payer, member_id="MEM-9999")["reason"] == "no_member"
    assert check(payer, category="dentistry")["reason"] == "not_covered"
    assert check(payer, on="2025-06-01")["reason"] == "outside_dates"
    assert check(payer, on="2027-06-01")["reason"] == "outside_dates"
    member_id = body(payer.get("portal/members", subject="pay"))["members"][-1]["id"]
    payer.post(f"portal/members/{member_id}/end", {}, subject="pay")
    assert check(payer, member_id="MEM-0001")["reason"] == "member_ended"
    payer.post(f"portal/plans/{plan_id}", {"active": False}, subject="pay")
    assert check(payer, member_id="MEM-0002")["reason"] == "plan_inactive"
    for answer in (check(payer, member_id="MEM-9999"), check(payer, category="dentistry")):
        assert answer["covered"] is False and answer["copay_percent"] is None


@pytest.mark.parametrize(
    "over", [{"member_id": "x"}, {"category": "Bad Cat"}, {"on": "2026-13-45"}])
def test_a_check_with_nonsense_is_refused(payer, over):
    fields = {"member_id": "MEM-0001", "category": "diagnostics", **over}
    assert payer.post("portal/entitlement", fields, subject="pay").status_code == 422


def test_ending_a_member_twice_is_harmless_and_unknown_ids_are_not_found(payer):
    plan(payer)
    mid = body(member(payer))["member"]["id"]
    assert payer.post(f"portal/members/{mid}/end", {}, subject="pay").status_code == 200
    assert payer.post(f"portal/members/{mid}/end", {}, subject="pay").status_code == 200
    assert payer.post("portal/members/nonexistent-id-00000/end", {}, subject="pay"
                      ).status_code == 404


def test_each_check_is_audited_without_the_member_number(c, payer):
    from modules.evidence.models import AuditEvent
    plan(payer)
    member(payer)
    check(payer)
    with tenant_transaction(c.clinic.ctx):
        events = list(AuditEvent.objects.filter(action="plan.check"))
        assert len(events) == 1 and "MEM-0001" not in json.dumps(events[0].details or {})


def test_only_a_payer_login_uses_plans_and_checks(c):
    for subject, kind, role in (("hr", EMP, "R08"), ("lab", FAC, "R05")):
        c.login(subject, kind, role)
        assert c.get("portal/plans", subject=subject).status_code == 403
        assert plan(c, subject=subject).status_code == 403
        assert c.post("portal/entitlement", {"member_id": "MEM-0001", "category": "x1"},
                      subject=subject).status_code == 403
    assert c.get("portal/plans", subject="pat").status_code == 403


def test_one_payers_plans_are_invisible_to_another_customer():
    one, two = Practice(), Practice()
    one.login("pay", PAY, "R07")
    two.login("pay", PAY, "R07")
    assert plan(one).status_code == 201
    assert body(two.get("portal/plans", subject="pay"))["plans"] == []
    assert plan(two).status_code == 201                         # the same code in another customer
