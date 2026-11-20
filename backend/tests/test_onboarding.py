import json
import re
from typing import Any

import pytest
from django.core import mail

from foundation import mail as mailer
from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust import auth
from tests.test_api import API
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PICKED = "Chosen-Passw0rd"
ADMIN = "owner@greenvalley.example.org"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STAFF_APP_URL = "http://staff.test"


@pytest.fixture
def platform():
    p = Platform()
    p.support = p.staff("vikram", "R11")
    p.safety = p.staff("iyer", "R12")
    return p


def add(platform, subject="vikram", customer="Green Valley Clinics", site="Green Valley Main",
        email=ADMIN, tenant_id=None):
    return platform.client.post(
        API + "platform/customers",
        json.dumps({"customer": customer, "site": site, "admin_email": email}),
        content_type="application/json",
        **platform.header(tenant_id or platform.tenant_public_id, subject))


def tenant_names():
    return sorted(t["name"] for t in trust.list_tenants())


def invitation_token():
    mailer.send_due()
    return re.findall(r"token=([\w.\-]+)", str(mail.outbox[-1].body))[0]


# ------------------------------------------------------------------ the happy path
def test_support_adds_a_customer_and_its_administrator_can_then_sign_in(platform):
    before = tenant_names()
    r = add(platform)
    assert r.status_code == 201
    made = json.loads(r.content)["customer"]
    assert made["name"] == "Green Valley Clinics" and made["site"] == "Green Valley Main"
    assert tenant_names() == sorted(before + ["Green Valley Clinics"])

    # the administrator is only told by email; nothing about them comes back to Zoiko staff
    assert ADMIN not in r.content.decode()
    mailer.send_due()
    message = mail.outbox[-1]
    assert message.to == [ADMIN] and "administrator" in message.body
    assert "organization 'Green Valley Clinics'" in message.body
    assert "http://staff.test/?action=reset&token=" in message.body

    with pytest.raises(Exception):  # noqa: B017 - ZoikoError: nobody knows the placeholder password
        auth.login(tenant_name="Green Valley Clinics", login=ADMIN, password="Whatever-Passw0rd")
    token = re.findall(r"token=([\w.\-]+)", str(message.body))[0]
    from django.test import Client
    done = Client().post(API + "auth/reset-password", json.dumps(
        {"token": token, "password": PICKED}), content_type="application/json")
    assert done.status_code == 200
    got = auth.login(tenant_name="Green Valley Clinics", login=ADMIN, password=PICKED)
    assert [r[0] for r in got.roles] == ["R13"]


def test_the_new_customer_is_ready_but_closed_to_self_sign_up(platform):
    add(platform)
    row = next(t for t in trust.list_tenants() if t["name"] == "Green Valley Clinics")
    assert row["type"] == "provider_org" and row["status"] == "active"
    assert row["self_registration"] == "false"
    with tenant_transaction(trust.context_for(row["public_id"])):
        counts = trust.counts()
        assert counts["organizations"] == 1 and counts["staff"] == 1 and counts["patients"] == 0


def test_the_administrator_can_manage_their_own_team_straight_away(platform):
    add(platform)
    token = invitation_token()
    from django.test import Client
    Client().post(API + "auth/reset-password", json.dumps(
        {"token": token, "password": PICKED}), content_type="application/json")
    tenant = next(t for t in trust.list_tenants() if t["name"] == "Green Valley Clinics")
    login = Client().post(API + "auth/login", json.dumps({
        "organization": "Green Valley Clinics", "login": ADMIN, "password": PICKED}),
        content_type="application/json")
    assert login.status_code == 200 and json.loads(login.content)["home"] == "operations"
    bearer: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {json.loads(login.content)['token']}"}
    r = Client().get(API + "staff/directory", **bearer)
    assert r.status_code == 200 and json.loads(r.content)["can_manage"] is True
    assert tenant["public_id"]


# ------------------------------------------------------------------ who may do it
def test_only_support_can_add_a_customer(platform):
    before = tenant_names()
    assert add(platform, "iyer").status_code == 403                 # safety may only look
    assert add(platform, "nobody").status_code == 401              # no such login
    assert platform.client.post(API + "platform/customers", "{}",
                                content_type="application/json").status_code == 401
    assert tenant_names() == before


def test_a_customers_staff_cannot_add_customers(platform):
    clinic = World()
    with tenant_transaction(clinic.ctx):
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="boss")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=clinic.org,
                          role_code="R13", by="system")
    before = tenant_names()
    assert add(platform, "boss", tenant_id=clinic.tenant_public_id).status_code == 403
    assert tenant_names() == before


def test_a_refused_attempt_is_recorded_in_the_platform_decisions(platform):
    add(platform, "iyer")
    from modules.policy.models import AccessDecision
    with tenant_transaction(platform.ctx):
        rows = AccessDecision.objects.filter(operation="write:platform", allowed=False)
        assert rows.count() == 1


# ------------------------------------------------------------------ bad input leaves nothing behind
@pytest.mark.parametrize("kwargs", [
    {"customer": ""}, {"customer": "A"}, {"customer": "x" * 121}, {"customer": "Bad\nName"},
    {"site": ""}, {"site": " "}, {"email": "not-an-address"}, {"email": ""},
    {"email": "a@example.org\nBcc: b@example.org"},
])
def test_bad_input_is_refused_and_nothing_is_created(platform, kwargs):
    before, outbox = tenant_names(), len(mail.outbox)
    assert add(platform, **kwargs).status_code == 422
    assert tenant_names() == before
    mailer.send_due()
    assert len(mail.outbox) == outbox


def test_a_customer_name_can_only_be_used_once(platform):
    assert add(platform).status_code == 201
    mailer.send_due()
    sent = len(mail.outbox)
    again = add(platform, email="someone.else@example.org")
    assert again.status_code == 409
    assert tenant_names().count("Green Valley Clinics") == 1
    mailer.send_due()
    assert len(mail.outbox) == sent                          # no second invitation went out


# ------------------------------------------------------------------ the record
def test_the_platform_chain_records_it_without_any_personal_detail(platform):
    add(platform)
    with tenant_transaction(platform.ctx):
        events = list(AuditEvent.objects.filter(action="platform.onboard_customer"))
    assert len(events) == 1 and events[0].actor_ref == platform.support
    text = json.dumps([str(e.__dict__) for e in events])
    assert ADMIN not in text and "Green Valley" not in text


def test_the_new_customers_own_chain_starts_with_its_setup_and_stays_intact(platform):
    add(platform)
    tenant = next(t for t in trust.list_tenants() if t["name"] == "Green Valley Clinics")
    with tenant_transaction(trust.context_for(tenant["public_id"])):
        actions = [e.action for e in AuditEvent.objects.order_by("chain_seq")]
        from modules.evidence import api as evidence
        assert evidence.verify_chain().ok
    assert "tenant.create" in actions and "customer.onboard" in actions
    assert "staff.invite" not in actions                      # not an administrator's own invite
