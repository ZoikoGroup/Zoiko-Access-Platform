import json

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.policy.models import AccessDecision
from modules.trust import api as trust
from tests.test_api import API
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def platform():
    world = Platform()
    world.sam = world.staff("sam", "R11")                           # support
    return world


@pytest.fixture
def customer():
    return World()                                                  # a clinic, sign-up closed


def post(world, customer_id, body, subject="sam"):
    return world.client.post(API + f"platform/customers/{customer_id}/sign-up",
                             json.dumps(body), content_type="application/json",
                             **world.header(world.tenant_public_id, subject))


def set_to(world, customer, enabled, subject="sam"):
    return post(world, customer.tenant_public_id, {"enabled": enabled}, subject)


def is_open(customer):
    row = next(t for t in trust.list_tenants() if t["public_id"] == customer.tenant_public_id)
    return row["self_registration"] == "true"


def sign_up(customer, login="new.patient@example.org"):
    return Client().post(
        "/api/v1/auth/register",
        json.dumps({"organization": customer.tenant_name, "login": login, "password": PASSWORD}),
        content_type="application/json")


def audits(world, action):
    with tenant_transaction(world.ctx):
        return AuditEvent.objects.filter(action=action).count()


# ------------------------------------------------------------------ the setting works
def test_opening_lets_patients_sign_up_and_closing_stops_them_again(platform, customer):
    assert not is_open(customer) and sign_up(customer).status_code == 403
    r = set_to(platform, customer, True)
    assert r.status_code == 200
    assert json.loads(r.content)["customer"] == {
        "id": customer.tenant_public_id, "name": customer.tenant_name, "self_registration": True}
    assert is_open(customer) and sign_up(customer).status_code == 201
    assert set_to(platform, customer, False).status_code == 200
    assert not is_open(customer) and sign_up(customer, "other@example.org").status_code == 403


def test_closing_does_not_remove_accounts_already_created(platform, customer):
    set_to(platform, customer, True)
    assert sign_up(customer).status_code == 201
    set_to(platform, customer, False)
    login = Client().post("/api/v1/auth/login", json.dumps({
        "organization": customer.tenant_name, "login": "new.patient@example.org",
        "password": PASSWORD}), content_type="application/json")
    assert login.status_code == 200


def test_the_overview_shows_the_new_state(platform, customer):
    set_to(platform, customer, True)
    rows = {t["name"]: t for t in json.loads(platform.overview("sam").content)["tenants"]}
    assert rows[customer.tenant_name]["self_registration"] is True
    set_to(platform, customer, False)
    rows = {t["name"]: t for t in json.loads(platform.overview("sam").content)["tenants"]}
    assert rows[customer.tenant_name]["self_registration"] is False


def test_changing_one_customer_leaves_another_alone(platform, customer):
    other = World()
    set_to(platform, customer, True)
    assert is_open(customer) and not is_open(other)


# ------------------------------------------------------------------ the record
def test_a_real_change_is_audited_in_both_chains_with_the_person_who_made_it(platform, customer):
    set_to(platform, customer, True)
    assert audits(platform, "platform.customer_sign_up") == 1
    assert audits(customer, "tenant.self_registration") == 1
    with tenant_transaction(customer.ctx):
        event = AuditEvent.objects.get(action="tenant.self_registration")
    assert event.actor_ref == platform.sam and event.details == {"enabled": 1}
    with tenant_transaction(platform.ctx):
        seen = AuditEvent.objects.get(action="platform.customer_sign_up")
    assert seen.actor_ref == platform.sam and seen.object_ref == customer.tenant_public_id
    assert seen.details == {"enabled": 1}
    set_to(platform, customer, False)
    with tenant_transaction(platform.ctx):
        later = AuditEvent.objects.filter(action="platform.customer_sign_up").order_by("chain_seq")
        assert [e.details for e in later] == [{"enabled": 1}, {"enabled": 0}]


def test_setting_the_value_it_already_has_changes_and_records_nothing(platform, customer):
    assert set_to(platform, customer, False).status_code == 200      # already closed
    assert audits(platform, "platform.customer_sign_up") == 0
    assert audits(customer, "tenant.self_registration") == 0
    set_to(platform, customer, True)
    set_to(platform, customer, True)                                # already open
    assert audits(platform, "platform.customer_sign_up") == 1
    assert audits(customer, "tenant.self_registration") == 1


def test_a_refusal_is_not_audited(platform, customer):
    set_to(platform, customer, "yes")
    set_to(platform, customer, True, subject="nobody")
    assert audits(platform, "platform.customer_sign_up") == 0
    assert audits(customer, "tenant.self_registration") == 0


def test_the_change_leaves_a_write_decision_on_record(platform, customer):
    set_to(platform, customer, True)
    with tenant_transaction(platform.ctx):
        assert AccessDecision.objects.filter(
            operation="write:platform", actor_ref=platform.sam, allowed=True).exists()


# ------------------------------------------------------------------ bad requests
@pytest.mark.parametrize("value", ["true", "yes", 1, 0, None, [], {}, "", "false"])
def test_only_a_real_true_or_false_is_accepted(platform, customer, value):
    assert post(platform, customer.tenant_public_id, {"enabled": value}).status_code == 422
    assert not is_open(customer)


def test_a_missing_value_is_refused(platform, customer):
    assert post(platform, customer.tenant_public_id, {}).status_code == 422
    assert not is_open(customer)


def test_a_closing_request_with_a_bad_value_leaves_an_open_customer_open(platform, customer):
    set_to(platform, customer, True)
    assert post(platform, customer.tenant_public_id, {"enabled": "false"}).status_code == 422
    assert is_open(customer)


def test_an_unknown_customer_is_not_found(platform):
    assert post(platform, "tenant_missing", {"enabled": True}).status_code == 404


def test_the_platforms_own_tenant_has_no_patient_sign_up(platform):
    assert post(platform, platform.tenant_public_id, {"enabled": True}).status_code == 422
    row = next(t for t in trust.list_tenants() if t["public_id"] == platform.tenant_public_id)
    assert row["self_registration"] == "false"


# ------------------------------------------------------------------ who may do it
def test_the_safety_officer_can_not_change_it(platform, customer):
    platform.staff("iyer", "R12")
    assert set_to(platform, customer, True, subject="iyer").status_code == 403
    assert not is_open(customer)


@pytest.mark.parametrize("role,kind", [("R10", trust.ActorType.WORKLOAD),
                                       ("R04", trust.ActorType.FACILITY_USER)])
def test_other_platform_roles_can_not_change_it(platform, customer, role, kind):
    platform.staff("other", role, kind=kind)
    assert set_to(platform, customer, True, subject="other").status_code == 403
    assert not is_open(customer)


def test_a_customers_own_staff_can_not_change_it_even_with_a_support_role(customer):
    with tenant_transaction(customer.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="idp", subject="x")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=customer.org,
                          role_code="R11", by="system")
    r = Client().post(API + f"platform/customers/{customer.tenant_public_id}/sign-up",
                      json.dumps({"enabled": True}), content_type="application/json",
                      HTTP_AUTHORIZATION=f"Bearer dev:{customer.tenant_public_id}:idp:x")
    assert r.status_code == 403
    assert not is_open(customer)


def test_a_missing_login_is_refused(platform, customer):
    r = platform.client.post(API + f"platform/customers/{customer.tenant_public_id}/sign-up",
                             json.dumps({"enabled": True}), content_type="application/json")
    assert r.status_code == 401
    assert not is_open(customer)
