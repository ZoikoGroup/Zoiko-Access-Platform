import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import Suspension, Tenant
from tests.test_api import API
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

LOGIN = "clinic.admin@example.org"
PASSWORD = "Correct-Horse-9"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def platform():
    world = Platform()
    world.sam = world.staff("sam", "R11")
    world.tess = world.staff("tess", "R11")                         # the second person
    return world


@pytest.fixture
def customer():
    world = World()                                                 # a clinic with one admin login
    with tenant_transaction(world.ctx):
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="local", subject=LOGIN)
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=world.org,
                          role_code="R13", by="system")
        trust.set_password(actor.public_id, login=LOGIN, password=PASSWORD, by="system",
                           email=LOGIN)
    return world


def post(world, path, body=None, subject="sam"):
    return world.client.post(API + path, json.dumps(body or {}), content_type="application/json",
                             **world.header(world.tenant_public_id, subject))


def get(world, subject="sam"):
    return world.client.get(API + "platform/suspensions",
                            **world.header(world.tenant_public_id, subject))


def ask(world, customer, action="suspend", reason="Unpaid and unreachable", subject="sam"):
    return post(world, f"platform/customers/{customer.tenant_public_id}/suspension",
                {"action": action, "reason": reason}, subject)


def asked(world, customer, action="suspend", subject="sam"):
    r = ask(world, customer, action, subject=subject)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["id"]


def approve(world, request_id, subject="tess"):
    return post(world, f"platform/suspensions/{request_id}/approve", {}, subject)


def reject(world, request_id, subject="tess", note="Not yet"):
    return post(world, f"platform/suspensions/{request_id}/reject", {"note": note}, subject)


def cancel(world, request_id, subject="sam"):
    return post(world, f"platform/suspensions/{request_id}/cancel", {}, subject)


def listed(world, subject="sam"):
    r = get(world, subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["suspensions"]


def status_of(customer):
    return next(t["status"] for t in trust.list_tenants()
                if t["public_id"] == customer.tenant_public_id)


def sign_in(customer):
    return Client().post("/api/v1/auth/login", json.dumps({
        "organization": customer.tenant_name, "login": LOGIN, "password": PASSWORD}),
        content_type="application/json")


def audits(world, action):
    with tenant_transaction(world.ctx):
        return AuditEvent.objects.filter(action=action).count()


# ------------------------------------------------------------------ asking
def test_asking_changes_nothing_and_the_customer_keeps_working(platform, customer):
    token = sign_in(customer).json()["token"]
    request_id = asked(platform, customer)
    assert status_of(customer) == "active"
    assert sign_in(customer).status_code == 200
    assert Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200
    [row] = listed(platform)
    assert row["id"] == request_id and row["status"] == "pending" and row["kind"] == "suspend"
    assert row["customer_name"] == customer.tenant_name
    assert row["reason"] == "Unpaid and unreachable"
    assert row["requested_by"] and row["mine"] is True and row["decided_by"] == ""


def test_the_other_person_sees_it_as_not_theirs(platform, customer):
    asked(platform, customer)
    assert listed(platform, "tess")[0]["mine"] is False


# ------------------------------------------------------------------ the second person approves
def test_a_different_person_approves_and_the_customer_is_locked_out(platform, customer):
    token = sign_in(customer).json()["token"]
    request_id = asked(platform, customer)
    r = approve(platform, request_id)
    assert r.status_code == 200 and json.loads(r.content)["status"] == "applied"
    assert status_of(customer) == "suspended"
    assert sign_in(customer).status_code == 401                          # cannot sign in
    assert Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401
    dev: dict[str, Any] = {
        "HTTP_AUTHORIZATION": f"Bearer dev:{customer.tenant_public_id}:local:{LOGIN}"}
    assert Client().get("/api/v1/me", **dev).status_code == 401
    [row] = listed(platform)
    assert row["status"] == "applied" and row["decided_by"] and row["decided_at"]


def test_the_overview_shows_the_customer_as_suspended(platform, customer):
    approve(platform, asked(platform, customer))
    rows = {t["name"]: t for t in json.loads(platform.overview("sam").content)["tenants"]}
    assert rows[customer.tenant_name]["status"] == "suspended"


def test_reinstating_needs_a_second_person_too_and_brings_everything_back(platform, customer):
    approve(platform, asked(platform, customer))
    request_id = asked(platform, customer, "reinstate", subject="tess")
    assert status_of(customer) == "suspended" and sign_in(customer).status_code == 401
    assert approve(platform, request_id, subject="sam").status_code == 200
    assert status_of(customer) == "active"
    assert sign_in(customer).status_code == 200                          # the same account works


def test_one_person_cannot_do_both_halves_of_a_reinstatement(platform, customer):
    approve(platform, asked(platform, customer))
    request_id = asked(platform, customer, "reinstate", subject="tess")
    assert approve(platform, request_id, subject="tess").status_code == 403
    assert status_of(customer) == "suspended"


# ------------------------------------------------------------------ the one who asked
def test_the_person_who_asked_can_neither_approve_nor_reject(platform, customer):
    request_id = asked(platform, customer)
    assert approve(platform, request_id, subject="sam").status_code == 403
    assert reject(platform, request_id, subject="sam").status_code == 403
    assert status_of(customer) == "active" and listed(platform)[0]["status"] == "pending"


def test_the_person_who_asked_can_take_it_back_and_then_ask_again(platform, customer):
    request_id = asked(platform, customer)
    assert cancel(platform, request_id).status_code == 200
    assert listed(platform)[0]["status"] == "cancelled"
    assert approve(platform, request_id).status_code == 409              # no longer waiting
    assert status_of(customer) == "active"
    assert ask(platform, customer).status_code == 201


def test_nobody_else_can_cancel_someone_elses_request(platform, customer):
    request_id = asked(platform, customer)
    assert cancel(platform, request_id, subject="tess").status_code == 403
    assert listed(platform)[0]["status"] == "pending"


# ------------------------------------------------------------------ rejecting
def test_the_other_person_can_reject_with_a_note_and_nothing_changes(platform, customer):
    request_id = asked(platform, customer)
    assert reject(platform, request_id, note="Wrong customer").status_code == 200
    [row] = listed(platform)
    assert row["status"] == "rejected" and row["decision_note"] == "Wrong customer"
    assert status_of(customer) == "active" and sign_in(customer).status_code == 200
    assert ask(platform, customer).status_code == 201                    # may ask again


def test_a_finished_request_cannot_be_decided_again(platform, customer):
    request_id = asked(platform, customer)
    approve(platform, request_id)
    assert approve(platform, request_id).status_code == 409
    assert reject(platform, request_id).status_code == 409
    assert cancel(platform, request_id).status_code == 409
    assert status_of(customer) == "suspended"


# ------------------------------------------------------------------ one at a time, only what fits
def test_only_one_request_can_wait_for_a_customer(platform, customer):
    asked(platform, customer)
    assert ask(platform, customer).status_code == 409
    assert ask(platform, customer, subject="tess").status_code == 409
    assert len(listed(platform)) == 1


def test_a_different_customer_can_have_its_own_request(platform, customer):
    asked(platform, customer)
    other = World()
    assert ask(platform, other).status_code == 201


def test_suspending_a_suspended_or_reinstating_an_active_customer_is_refused(platform, customer):
    assert ask(platform, customer, "reinstate").status_code == 409
    approve(platform, asked(platform, customer))
    assert ask(platform, customer, "suspend").status_code == 409


def test_a_request_that_no_longer_fits_cannot_be_approved_and_stays_waiting(platform, customer):
    request_id = asked(platform, customer)
    Tenant.objects.filter(public_id=customer.tenant_public_id).update(status="suspended")
    assert approve(platform, request_id).status_code == 409
    assert listed(platform)[0]["status"] == "pending"
    assert reject(platform, request_id).status_code == 200               # can still be closed


def test_an_ended_customer_cannot_be_suspended(platform, customer):
    Tenant.objects.filter(public_id=customer.tenant_public_id).update(status="ended")
    assert ask(platform, customer).status_code == 409


def test_the_platforms_own_tenant_can_never_be_suspended(platform):
    assert post(platform, f"platform/customers/{platform.tenant_public_id}/suspension",
                {"action": "suspend", "reason": "x"}).status_code == 422
    assert status_of(platform) == "active"


def test_unknown_customers_and_requests_are_not_found(platform):
    assert post(platform, "platform/customers/tenant_missing/suspension",
                {"action": "suspend", "reason": "x"}).status_code == 404
    assert approve(platform, "suspension_missing").status_code == 404
    assert reject(platform, "suspension_missing").status_code == 404
    assert cancel(platform, "suspension_missing").status_code == 404


# ------------------------------------------------------------------ bad input
@pytest.mark.parametrize("fields", [
    {"action": "delete"}, {"action": ""}, {"action": None}, {"action": 1}, {"action": ["suspend"]},
    {"action": "Suspend"},
    {"reason": ""}, {"reason": "   "}, {"reason": "r" * 501}, {"reason": "bell\x07"},
    {"reason": "two\nlines"}, {"reason": 5}, {"reason": None},
])
def test_bad_requests_are_refused_and_nothing_is_saved(platform, customer, fields):
    body = {"action": "suspend", "reason": "A real reason", **fields}
    r = post(platform, f"platform/customers/{customer.tenant_public_id}/suspension", body)
    assert r.status_code == 422
    assert listed(platform) == [] and status_of(customer) == "active"


def test_a_missing_field_is_refused(platform, customer):
    path = f"platform/customers/{customer.tenant_public_id}/suspension"
    assert post(platform, path, {"reason": "why"}).status_code == 422
    assert post(platform, path, {"action": "suspend"}).status_code == 422


def test_the_longest_reason_is_accepted(platform, customer):
    assert ask(platform, customer, reason="r" * 500).status_code == 201


@pytest.mark.parametrize("note", ["n" * 301, "bell\x07", 5, ["x"]])
def test_a_bad_note_on_a_rejection_is_refused_and_the_request_waits(platform, customer, note):
    request_id = asked(platform, customer)
    r = post(platform, f"platform/suspensions/{request_id}/reject", {"note": note}, "tess")
    assert r.status_code == 422
    assert listed(platform)[0]["status"] == "pending"


def test_the_note_is_optional(platform, customer):
    request_id = asked(platform, customer)
    assert post(platform, f"platform/suspensions/{request_id}/reject", {}, "tess"
                ).status_code == 200


# ------------------------------------------------------------------ who may do what
def test_the_safety_officer_can_read_but_not_ask_approve_reject_or_cancel(platform, customer):
    platform.staff("iyer", "R12")
    request_id = asked(platform, customer)
    assert len(listed(platform, "iyer")) == 1
    assert ask(platform, customer, subject="iyer").status_code == 403
    assert approve(platform, request_id, subject="iyer").status_code == 403
    assert reject(platform, request_id, subject="iyer").status_code == 403
    assert cancel(platform, request_id, subject="iyer").status_code == 403
    assert status_of(customer) == "active" and listed(platform)[0]["status"] == "pending"


@pytest.mark.parametrize("role,kind", [("R10", trust.ActorType.WORKLOAD),
                                       ("R04", trust.ActorType.FACILITY_USER)])
def test_other_platform_roles_can_do_nothing_here(platform, customer, role, kind):
    request_id = asked(platform, customer)
    platform.staff("other", role, kind=kind)
    assert get(platform, "other").status_code == 403
    assert ask(platform, customer, subject="other").status_code == 403
    assert approve(platform, request_id, subject="other").status_code == 403
    assert status_of(customer) == "active"


def test_a_customers_own_staff_cannot_touch_it_even_with_a_support_role(platform, customer):
    with tenant_transaction(customer.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="idp", subject="x")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=customer.org,
                          role_code="R11", by="system")
    header: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer dev:{customer.tenant_public_id}:idp:x"}
    body = json.dumps({"action": "suspend", "reason": "x"})
    client = Client()
    assert client.get(API + "platform/suspensions", **header).status_code == 403
    assert client.post(API + f"platform/customers/{customer.tenant_public_id}/suspension", body,
                       content_type="application/json", **header).status_code == 403
    assert status_of(customer) == "active"


def test_another_platform_tenant_neither_sees_nor_decides_these(platform, customer):
    request_id = asked(platform, customer)
    other = Platform()
    other.staff("zed", "R11")
    assert listed(other, "zed") == []
    assert approve(other, request_id, subject="zed").status_code == 404
    assert status_of(customer) == "active"


def test_a_missing_login_is_refused(platform, customer):
    assert platform.client.get(API + "platform/suspensions").status_code == 401
    r = platform.client.post(API + f"platform/customers/{customer.tenant_public_id}/suspension",
                             json.dumps({"action": "suspend", "reason": "x"}),
                             content_type="application/json")
    assert r.status_code == 401


# ------------------------------------------------------------------ the record
def test_every_step_is_audited_once_and_a_refusal_is_not(platform, customer):
    request_id = asked(platform, customer)
    approve(platform, request_id, subject="sam")                        # refused: same person
    ask(platform, customer)                                             # refused: already waiting
    approve(platform, request_id)
    approve(platform, request_id)                                       # refused: finished
    assert audits(platform, "platform.suspension_request") == 1
    assert audits(platform, "platform.suspension_approve") == 1
    assert audits(platform, "platform.suspension_reject") == 0
    assert audits(platform, "platform.suspension_cancel") == 0
    assert audits(customer, "tenant.suspended") == 1


def test_the_customers_own_record_names_the_approver_and_the_reinstatement(platform, customer):
    approve(platform, asked(platform, customer))
    with tenant_transaction(customer.ctx):
        assert AuditEvent.objects.get(action="tenant.suspended").actor_ref == platform.tess
    approve(platform, asked(platform, customer, "reinstate", subject="tess"), subject="sam")
    with tenant_transaction(customer.ctx):
        assert AuditEvent.objects.get(action="tenant.reinstated").actor_ref == platform.sam


def test_rejecting_and_cancelling_are_audited_but_do_not_touch_the_customer(platform, customer):
    reject(platform, asked(platform, customer))
    cancel(platform, asked(platform, customer))
    assert audits(platform, "platform.suspension_reject") == 1
    assert audits(platform, "platform.suspension_cancel") == 1
    assert audits(customer, "tenant.suspended") == 0


def test_waiting_requests_are_listed_first_even_when_older_then_the_newest(platform, customer):
    waiting = asked(platform, customer)                             # the OLDEST, still waiting
    other = World()
    older = asked(platform, other)
    cancel(platform, older)
    third = World()
    newest = asked(platform, third)
    reject(platform, newest)
    assert [r["id"] for r in listed(platform)] == [waiting, newest, older]


def test_a_read_leaves_an_access_decision_on_record(platform):
    from modules.policy.models import AccessDecision
    listed(platform)
    with tenant_transaction(platform.ctx):
        assert AccessDecision.objects.filter(
            operation="read:platform", actor_ref=platform.sam, allowed=True).exists()


def test_a_request_is_never_deleted_and_keeps_who_did_what(platform, customer):
    request_id = asked(platform, customer)
    approve(platform, request_id)
    with tenant_transaction(platform.ctx):
        row = Suspension.objects.get(public_id=request_id)
    assert (row.requested_by_ref, row.decided_by_ref) == (platform.sam, platform.tess)
    assert row.status == "applied" and row.decided_at is not None
