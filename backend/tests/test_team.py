import datetime as dt
import json
import re

import pytest
from django.core import mail
from django.test import Client

from foundation import mail as mailer
from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import FixedClock, utc_now
from modules.evidence.models import AuditEvent
from modules.policy.api import authorize
from modules.trust import api as trust
from modules.trust import auth
from tests.test_api import Web, book
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PICKED = "Invited-Passw0rd"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STAFF_APP_URL = "http://staff.test"
    settings.CLINICIAN_APP_URL = "http://doctor.test"


@pytest.fixture
def web():
    w = Web()
    w.admin = w.login("boss", trust.ActorType.FACILITY_USER, "R13")
    w.desk = w.login("desk", trust.ActorType.FACILITY_USER, "R04")
    return w


def invite(web, email="new.doctor@example.org", role="R03", subject="boss"):
    return web.post("staff/members", {"email": email, "role": role}, subject=subject)


def directory(web, subject="boss"):
    r = web.get("staff/directory", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)


def link_in_mail():
    mailer.send_due()
    return re.search(r"token=([\w.\-]+)", mail.outbox[-1].body).group(1)


# ------------------------------------------------------------------ who may manage
def test_only_the_administrator_is_offered_and_allowed_team_management(web):
    assert directory(web, "boss")["can_manage"] is True
    assert directory(web, "desk")["can_manage"] is False
    web.patient("pat")
    web.login("hr", trust.ActorType.EMPLOYER_USER, "R08")
    assert invite(web, subject="desk").status_code == 403         # a receptionist cannot
    assert invite(web, subject="pat").status_code == 403
    assert invite(web, subject="hr").status_code == 403
    assert invite(web).status_code == 201                           # the administrator can


def test_the_administrator_can_do_everything_clinic_operations_can(web):
    web.patient("pat")
    booking = book(web, web.slots[0]["public_id"], subject="pat").json()["booking"]["id"]
    assert web.post(f"bookings/{booking}/confirm", subject="boss").status_code == 200
    made = web.post("services", {"organization": web.clinic.org, "name": "Admin-made",
                                 "category": "dental", "duration_minutes": 20}, subject="boss")
    assert made.status_code == 201
    me = json.loads(web.get("me", subject="boss").content)
    assert me["home"] == "operations" and me["purpose"] == "OPERATIONS"


# ------------------------------------------------------------------ inviting
def test_inviting_creates_the_person_with_the_role_and_emails_a_link(web):
    r = invite(web, "New.Doctor@Example.org", "R03")
    assert r.status_code == 201
    member = json.loads(r.content)["member"]
    assert member["login"] == "new.doctor@example.org" and member["role"] == "R03"
    team = {m["login"]: m for m in directory(web)["staff"]}
    new = team["new.doctor@example.org"]
    assert new["type"] == "clinician" and new["pending_invite"] is True
    assert new["roles"][0]["role"] == "R03" and new["roles"][0]["organization_name"] == "North"
    assert mail.outbox == []                                        # sent by the worker
    token = link_in_mail()
    message = mail.outbox[0]
    assert message.to == ["new.doctor@example.org"]
    assert "invited to North" in message.subject and "a doctor" in message.body
    assert "http://doctor.test/?action=reset&token=" in message.body   # a doctor: the doctor app
    assert token
    # the sign-in instructions name the ORGANIZATION (what the sign-in form asks for), not the site
    assert f"organization '{web.clinic.tenant_name}'" in message.body
    assert "login new.doctor@example.org" in message.body


def test_the_person_chooses_their_own_password_from_the_link_and_can_then_sign_in(web):
    invite(web, "new.desk@example.org", "R04")
    token = link_in_mail()
    assert "http://staff.test/?action=reset&token=" in mail.outbox[-1].body   # staff console
    with pytest.raises(Exception):  # noqa: B017 - ZoikoError: the placeholder password is unknown
        auth.login(tenant_name=web.clinic.tenant_name, login="new.desk@example.org",
                   password="Whatever-Passw0rd")
    r = Client().post("/api/v1/auth/reset-password", json.dumps(
        {"token": token, "password": PICKED}), content_type="application/json")
    assert r.status_code == 200
    got = auth.login(tenant_name=web.clinic.tenant_name, login="new.desk@example.org",
                     password=PICKED)
    assert got.roles[0][0] == "R04"
    team = {m["login"]: m for m in directory(web)["staff"]}
    assert team["new.desk@example.org"]["pending_invite"] is False  # accepting proves the address


def test_only_doctor_receptionist_and_administrator_roles_can_be_handed_out(web):
    for role in ("R11", "R12", "R10", "R01", "R02", "R07", "R08", "R99", ""):
        r = invite(web, f"x.{role or 'blank'}@example.org", role) if role else web.post(
            "staff/members", {"email": "blank@example.org"}, subject="boss")
        assert r.status_code == 422, role
    for bad in ("not-an-email", "a b@c.org", "x@y"):
        assert invite(web, bad).status_code == 422, bad
    assert [m["login"] for m in directory(web)["staff"]
            if m["login"].startswith("x.")] == []


def test_inviting_never_takes_over_an_existing_login(web):
    assert invite(web, "boss").status_code == 422                   # not even an address
    with tenant_transaction(web.clinic.ctx):                        # a self-registered style login
        existing = trust.register_actor(
            actor_type=trust.ActorType.PATIENT, issuer="local", subject="someone@example.org")
        trust.set_password(existing.public_id, login="someone@example.org",
                           password="Existing-Passw0rd", by="system")
    first = invite(web, "twice@example.org")
    assert first.status_code == 201
    assert invite(web, "twice@example.org").status_code == 409
    assert invite(web, "Twice@Example.org").status_code == 409
    assert invite(web, "someone@example.org").status_code == 409    # an existing patient login


# ------------------------------------------------------------------ suspend, restore, resend
def test_suspending_stops_the_person_at_once_and_restoring_brings_them_back(web):
    web.login("temp", trust.ActorType.FACILITY_USER, "R04")
    ids = {m["login"]: m["id"] for m in directory(web)["staff"]}
    assert web.get("staff/directory", subject="temp").status_code == 200
    off = web.post(f"staff/members/{ids['temp']}/status", {"status": "suspended"}, subject="boss")
    assert off.status_code == 200
    assert web.get("staff/directory", subject="temp").status_code == 401     # signed out now
    on = web.post(f"staff/members/{ids['temp']}/status", {"status": "active"}, subject="boss")
    assert on.status_code == 200
    assert web.get("staff/directory", subject="temp").status_code == 200


def test_nobody_can_suspend_themselves_and_only_own_team_members_can_be_reached(web):
    web.patient("pat")
    other = World()                                             # a different customer
    other_staff = other.person("R04")
    ids = {m["login"]: m["id"] for m in directory(web)["staff"]}
    me = web.post(f"staff/members/{ids['boss']}/status", {"status": "suspended"}, subject="boss")
    assert me.status_code == 403
    for target in (other_staff, "nobody"):
        r = web.post(f"staff/members/{target}/status", {"status": "suspended"}, subject="boss")
        assert r.status_code == 404
    bad = web.post(f"staff/members/{ids['desk']}/status", {"status": "deleted"}, subject="boss")
    assert bad.status_code == 422
    still = web.post(f"staff/members/{ids['desk']}/status", {"status": "active"}, subject="desk")
    assert still.status_code == 403                             # a receptionist cannot manage


def test_a_patient_account_cannot_be_reached_through_team_management(web):
    actor, _ = web.patient("pat")
    r = web.post(f"staff/members/{actor}/status", {"status": "suspended"}, subject="boss")
    assert r.status_code == 404
    assert web.get("notifications", subject="pat").status_code == 200   # still works


def test_the_invitation_can_be_sent_again_until_it_is_accepted(web):
    invite(web, "slow.person@example.org", "R04")
    mailer.send_due()
    member = {m["login"]: m for m in directory(web)["staff"]}["slow.person@example.org"]["id"]
    too_soon = web.post(f"staff/members/{member}/resend-invite", subject="boss")
    assert too_soon.status_code == 429
    receipt = _receipt(web)                                     # asked outside any transaction
    with tenant_transaction(web.clinic.ctx):                    # a couple of minutes later
        trust.resend_invitation(
            member, organization_public_id=web.clinic.org, actor_public_id=web.admin,
            decision=receipt, clock=FixedClock(utc_now() + dt.timedelta(minutes=2)))
    mailer.send_due()
    assert len(mail.outbox) == 2
    old, new = (re.search(r"token=([\w.\-]+)", m.body).group(1) for m in mail.outbox)
    assert old != new
    bad = Client().post("/api/v1/auth/reset-password", json.dumps(
        {"token": old, "password": PICKED}), content_type="application/json")
    assert bad.status_code == 422                               # the newer link replaced the old
    good = Client().post("/api/v1/auth/reset-password", json.dumps(
        {"token": new, "password": PICKED}), content_type="application/json")
    assert good.status_code == 200
    again = web.post(f"staff/members/{member}/resend-invite", subject="boss")
    assert again.status_code in (409, 429)                      # accepted: nothing to resend


def _receipt(web):
    with bind_context(web.clinic.ctx):
        return authorize(actor_public_id=web.admin, operation="write:staff",
                         purpose_code="OPERATIONS", organization_public_id=web.clinic.org)


# ------------------------------------------------------------------ records and fences
def test_every_change_is_recorded_without_passwords_or_links(web):
    invite(web, "audited@example.org")
    token = link_in_mail()
    secret = token.split(".")[1]
    web.post("staff/members", {"email": "x@y.org", "role": "R11"}, subject="boss")   # refused
    with tenant_transaction(web.clinic.ctx):
        events = AuditEvent.objects.filter(tenant_id=web.clinic.ctx.tenant_id)
        actions = set(events.values_list("action", flat=True))
        blob = json.dumps(list(events.values_list("details", "object_ref", "reason_code")))
    assert {"staff.invite", "auth.invitation_sent", "access.decide"} <= actions
    assert secret not in blob and "audited@example.org" not in blob
    assert invite(web, subject="desk").status_code == 403
    with tenant_transaction(web.clinic.ctx):
        assert AuditEvent.objects.filter(action="access.decide", outcome="denied").exists()


def test_the_domain_demands_its_own_receipt_even_if_the_endpoint_were_skipped(web):
    with bind_context(web.clinic.ctx):
        other_receipt = authorize(actor_public_id=web.desk, operation="write:booking",
                                  purpose_code="OPERATIONS", organization_public_id=web.clinic.org)
    with tenant_transaction(web.clinic.ctx), pytest.raises(ZoikoError) as excinfo:
        trust.invite_staff(email="sneaky@example.org", role_code="R13",
                           organization_public_id=web.clinic.org, actor_public_id=web.desk,
                           decision=other_receipt)
    assert excinfo.value.code == ErrorCode.POLICY_DENIED
    assert all(m["login"] != "sneaky@example.org" for m in directory(web)["staff"])
