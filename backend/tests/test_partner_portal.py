"""The partner, payer and employer portal: onboarding each kind of customer, and what a login of
that kind is told it can reach (DEV-158)."""
import json
import re

import pytest
from django.core import mail

from foundation import mail as mailer
from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from modules.trust import auth
from modules.trust.models import Actor, RoleAssignment, Tenant
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
PICKED = "Chosen-Passw0rd"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STAFF_APP_URL = "http://staff.test"
    settings.PARTNER_APP_URL = "http://partners.test"


@pytest.fixture
def platform():
    p = Platform()
    p.support = p.staff("vikram", "R11")
    return p


def add(platform, kind, name, email):
    body = {"customer": name, "site": f"{name} Main", "admin_email": email}
    if kind is not None:
        body["kind"] = kind
    return platform.client.post(
        API + "platform/customers", json.dumps(body), content_type="application/json",
        **platform.header(platform.tenant_public_id, "vikram"))


KINDS = [  # kind, customer type, role, kind of login
    ("lab", "partner", "R05", "facility_user"), ("pharmacy", "partner", "R06", "facility_user"),
    ("homecare", "partner", "R09", "facility_user"), ("payer", "payer", "R07", "payer_user"),
    ("employer", "employer", "R08", "employer_user"),
    ("developer", "partner", "R10", "facility_user"),
    ("provider", "provider_org", "R13", "facility_user"),
]


# ------------------------------------------------------------------ onboarding each kind
@pytest.mark.parametrize("kind,tenant_type,role,login_kind", KINDS)
def test_support_onboards_each_kind_of_customer_with_the_one_role_that_kind_holds(
        platform, kind, tenant_type, role, login_kind):
    email = f"first.{kind}@example.org"
    r = add(platform, kind, f"Acme {kind}", email)
    assert r.status_code == 201 and json.loads(r.content)["customer"]["kind"] == kind
    tenant = Tenant.objects.get(name=f"Acme {kind}")
    assert tenant.type == tenant_type
    from foundation.cell_context import build_context
    with tenant_transaction(build_context(tenant.id)):
        actor = Actor.objects.get(subject=email)
        held = list(RoleAssignment.objects.filter(actor=actor).values_list(
            "role__role_code", flat=True))
    assert actor.actor_type == login_kind and held == [role]


def test_no_kind_means_a_clinic_as_before_and_an_unknown_kind_is_refused(platform):
    assert json.loads(add(platform, None, "Plain Clinic", "a@example.org").content
                      )["customer"]["kind"] == "provider"
    assert add(platform, "casino", "Odd One", "b@example.org").status_code == 422
    assert not Tenant.objects.filter(name="Odd One").exists()


@pytest.mark.parametrize("kind,link", [
    ("lab", "http://partners.test/"), ("payer", "http://partners.test/"),
    ("employer", "http://partners.test/"), ("developer", "http://partners.test/"),
    ("provider", "http://staff.test/")])
def test_the_invitation_opens_the_right_app_and_the_person_can_then_sign_in(
        platform, kind, link):
    email = f"first.{kind}@example.org"
    add(platform, kind, f"Acme {kind}", email)
    mailer.send_due()
    body = str(mail.outbox[-1].body)
    assert f"{link}?action=reset&token=" in body
    token = re.findall(r"token=([\w.\-]+)", body)[0]
    from django.test import Client
    done = Client().post(API + "auth/reset-password", json.dumps(
        {"token": token, "password": PICKED}), content_type="application/json")
    assert done.status_code == 200
    result = auth.login(tenant_name=f"Acme {kind}", login=email, password=PICKED)
    from api.support import home_for
    expected = {"lab": "partner", "payer": "payer", "employer": "employer",
                "developer": "developer", "provider": "operations"}[kind]
    assert home_for(result.actor_type, [r[0] for r in result.roles],
                    result.tenant_type) == expected


# ------------------------------------------------------------------ what the portal says
@pytest.fixture
def clinic():
    return Practice()


@pytest.mark.parametrize("role,login_kind,area", [
    ("R05", trust.ActorType.FACILITY_USER, "partner"),
    ("R06", trust.ActorType.FACILITY_USER, "partner"),
    ("R09", trust.ActorType.FACILITY_USER, "partner"),
    ("R07", trust.ActorType.PAYER_USER, "payer"),
    ("R08", trust.ActorType.EMPLOYER_USER, "employer")])
def test_the_overview_names_the_role_and_lists_what_it_may_reach_from_the_real_rules(
        clinic, role, login_kind, area):
    from modules.policy.rules import RULES
    clinic.login("someone", login_kind, role)
    r = clinic.get("portal/overview", subject="someone")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["area"] == area and len(body["roles"]) == 1
    mine = body["roles"][0]
    assert mine["code"] == role and mine["name"] and mine["prohibitions"]
    assert {c["resource"] for c in mine["can"]} == set(RULES[role])
    assert body["waiting"]                                    # it always says what is not connected


def test_the_employer_overview_promises_counts_only_and_says_what_is_connected(clinic):
    clinic.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    body = json.loads(clinic.get("portal/overview", subject="hr").content)
    assert any("Programme counts" in c for c in body["connected"])
    assert "never an individual" in " ".join(body["waiting"])
    can = {c["resource"]: c for c in body["roles"][0]["can"]}
    assert "person" not in can and "booking" not in can and can["billing"]["verbs"] == ["read"]


def test_other_kinds_of_login_are_refused_here(clinic):
    for subject in ("pat", "ops", "doc"):
        assert clinic.get("portal/overview", subject=subject).status_code == 403


# ------------------------------------------------------------------ a partner's own services
@pytest.mark.parametrize("role", ["R05", "R06", "R09"])
def test_a_lab_pharmacy_or_home_care_partner_adds_a_service_and_asks_for_a_listing(clinic, role):
    clinic.login("partner", trust.ActorType.FACILITY_USER, role)
    made = clinic.post("services", {
        "organization": clinic.clinic.org, "name": "Blood tests", "category": "diagnostics",
        "duration_minutes": 15}, subject="partner")
    assert made.status_code == 201
    service = json.loads(made.content)["service"]
    assert service["listing_state"] == "draft"
    asked = clinic.post(f"services/{service['id']}/listing",
                        {"action": "request", "note": "licence-0001"}, subject="partner")
    assert asked.status_code == 200
    assert json.loads(asked.content)["service"]["listing_state"] == "evidence_pending"
    # it cannot approve its own listing, nor can anyone without the administrator role
    for action in ("review", "publish"):
        again = clinic.post(f"services/{service['id']}/listing", {"action": action},
                            subject="partner")
        assert again.status_code == 403


def test_a_payer_and_an_employer_cannot_add_services(clinic):
    for subject, kind, role in (("pay", trust.ActorType.PAYER_USER, "R07"),
                                ("hr", trust.ActorType.EMPLOYER_USER, "R08")):
        clinic.login(subject, kind, role)
        r = clinic.post("services", {
            "organization": clinic.clinic.org, "name": "X", "category": "x",
            "duration_minutes": 15}, subject=subject)
        assert r.status_code == 403


# ------------------------------------------------------------------ the home screen numbers
def numbers(c, subject):
    r = c.get("portal/dashboard", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def test_a_partner_dashboard_counts_services_by_where_they_are_and_what_waits_for_you(clinic):
    clinic.login("lab1", trust.ActorType.FACILITY_USER, "R05")
    clinic.login("lab2", trust.ActorType.FACILITY_USER, "R05")
    base = numbers(clinic, "lab1")["services"]          # the test clinic already has one listed
    assert base["waiting_review"] == 0 and base["not_listed"] == 0
    ids = []
    for name in ("One", "Two", "Three"):
        made = clinic.post("services", {"organization": clinic.clinic.org, "name": name,
                                        "category": "diagnostics", "duration_minutes": 15},
                           subject="lab1")
        ids.append(json.loads(made.content)["service"]["id"])
    clinic.post(f"services/{ids[0]}/listing", {"action": "request", "note": "licence-0001"},
                subject="lab1")
    clinic.post(f"services/{ids[1]}/listing", {"action": "request", "note": "licence-0002"},
                subject="lab2")
    mine, other = numbers(clinic, "lab1"), numbers(clinic, "lab2")
    assert mine["services"] == {"total": base["total"] + 3, "listed": base["listed"],
                                "waiting_review": 2, "not_listed": 1}
    assert mine["to_review"] == 1 and other["to_review"] == 1       # each has the other's to review
    assert mine["team"] >= 2          # the test clinic has other staff too


def test_a_payer_dashboard_counts_plans_and_members(clinic):
    clinic.login("pay", trust.ActorType.PAYER_USER, "R07")
    clinic.post("portal/plans", {"code": "GOLD", "name": "Gold", "covered_categories": ["x1"],
                                 "copay_percent": 0}, subject="pay")
    clinic.post("portal/plans", {"code": "BASE", "name": "Base", "covered_categories": ["x1"],
                                 "copay_percent": 0}, subject="pay")
    clinic.post("portal/members", {"plan": "GOLD", "member_id": "MEM-0001"}, subject="pay")
    plan_id = json.loads(clinic.get("portal/plans", subject="pay").content)["plans"][0]["id"]
    clinic.post(f"portal/plans/{plan_id}", {"active": False}, subject="pay")
    assert numbers(clinic, "pay")["plans"] == {
        "plans": 2, "plans_on": 1, "members": 1, "members_ended": 0}


def test_an_employer_dashboard_shows_counts_with_small_groups_hidden(clinic):
    clinic.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    clinic.post("portal/program/enrol", {"refs": ["emp-0001", "emp-0002"]}, subject="hr")
    got = numbers(clinic, "hr")["programme"]
    assert got["enrolled"] == {"value": None, "hidden": True} and got["min_group"] == 10
    assert "emp-0001" not in json.dumps(got)


def test_a_developer_dashboard_counts_keys_webhooks_and_deliveries(clinic):
    clinic.login("dev", trust.ActorType.FACILITY_USER, "R10")
    r = numbers(clinic, "dev")
    assert r["keys"] == {"active": 0, "total": 0} and r["webhooks"] == {"active": 0, "total": 0}
    assert r["deliveries"]["gave_up"] == 0 and r["area"] == "developer"
    made = clinic.post("staff/integrations/partners",
                       {"label": "ERP", "allowed_types": ["partner.booking."]}, subject="dev")
    assert made.status_code == 201
    assert numbers(clinic, "dev")["keys"] == {"active": 1, "total": 1}


def test_other_kinds_of_login_have_no_portal_dashboard(clinic):
    for subject in ("pat", "ops", "doc"):
        assert clinic.get("portal/dashboard", subject=subject).status_code == 403


# ------------------------------------------------------------------ R10: the developer console
def test_a_partner_developer_belongs_in_the_developer_area_not_the_clinic_console(clinic):
    from api.support import home_for
    assert home_for("facility_user", ["R10"]) == "developer"
    assert home_for("facility_user", ["R10", "R13"]) == "operations"   # an admin keeps the console
    clinic.login("dev", trust.ActorType.FACILITY_USER, "R10")
    assert clinic.get("staff/dashboard", subject="dev").status_code == 403


def test_a_partner_developer_manages_keys_and_webhooks_and_a_colleague(clinic):
    clinic.login("dev", trust.ActorType.FACILITY_USER, "R10")
    assert clinic.post("staff/integrations/partners",
                       {"label": "ERP", "allowed_types": ["partner.booking."]},
                       subject="dev").status_code == 201
    assert clinic.get("staff/integrations/webhooks", subject="dev").status_code == 200
    assert clinic.get("staff/integrations/deliveries", subject="dev").status_code == 200
    assert clinic.post("portal/team", {"email": "dev2@example.org"},
                       subject="dev").status_code == 201
    with tenant_transaction(clinic.clinic.ctx):
        assert list(RoleAssignment.objects.filter(
            actor__subject="dev2@example.org").values_list("role__role_code", flat=True)) == ["R10"]


def test_the_documents_and_integration_screens_stay_closed_to_other_partner_kinds(clinic):
    clinic.login("lab", trust.ActorType.FACILITY_USER, "R05")
    clinic.login("pay", trust.ActorType.PAYER_USER, "R07")
    for subject in ("lab", "pay", "pat"):
        assert clinic.get("staff/integrations/partners", subject=subject).status_code == 403
