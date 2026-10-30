import json

import pytest
from django.db import connection
from django.test import Client

from api.support import home_for
from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent
from modules.evidence.models import AuditEvent
from modules.policy.api import decide
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.trust import auth
from tests.test_api import API
from tests.test_policy import World
from tests.test_scheduling import Clinic

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PATIENT_LOGIN = "pat.private@example.org"
PASSWORD = "Correct-Horse-9"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Platform(World):
    """Zoiko's own tenant, with support (R11) and safety (R12) staff."""

    def __init__(self):
        super().__init__(tenant_type=trust.TenantType.PLATFORM)
        self.client = Client()

    def staff(self, subject, role, kind=trust.ActorType.SUPPORT):
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(actor_type=kind, issuer="idp", subject=subject)
            trust.assign_role(actor_public_id=actor.public_id, organization_public_id=self.org,
                              role_code=role, by="system")
        return actor.public_id

    def header(self, tenant_public_id, subject):
        return {"HTTP_AUTHORIZATION": f"Bearer dev:{tenant_public_id}:idp:{subject}"}

    def overview(self, subject, tenant_public_id=None):
        return self.client.get(API + "platform/overview",
                               **self.header(tenant_public_id or self.tenant_public_id, subject))


@pytest.fixture
def platform():
    return Platform()


def customer_with_a_booking(login=PATIENT_LOGIN):
    clinic = Clinic()
    actor, person = clinic.patient(login)
    slot = clinic.slots(count=3)[0]["public_id"]
    decision = clinic.allow(actor, "SELF_SERVICE", person=person)
    with tenant_transaction(clinic.ctx):
        booking = scheduling.book(slot_ref=slot, person_ref=person, actor_public_id=actor,
                                  decision=decision)
    return clinic, actor, person, booking["public_id"]


def rows_by_name(response):
    return {t["name"]: t for t in json.loads(response.content)["tenants"]}


# ------------------------------------------------------------------ what Zoiko staff see
def test_platform_staff_see_one_row_per_customer_with_numbers_only(platform):
    platform.staff("vikram", "R11")
    a, actor, person, booking = customer_with_a_booking()
    b = Clinic()
    r = platform.overview("vikram")
    assert r.status_code == 200
    body = json.loads(r.content)
    rows = rows_by_name(r)
    assert set(rows) == {platform.tenant_name, a.tenant_name, b.tenant_name}
    row = rows[a.tenant_name]
    assert row["counts"]["patients"] == 1 and row["counts"]["organizations"] == 2
    assert row["bookings"] == {"requested": 1, "confirmed": 0, "cancelled": 0, "open_slots": 2}
    assert row["services"] == {"active": 1, "inactive": 0}
    assert row["audit"]["chain_ok"] is True and row["decisions"]["total"] >= 1
    assert rows[b.tenant_name]["counts"]["patients"] == 0
    assert body["outbox"]["pending"] >= 1 and set(body["outbox"]) == {
        "pending", "quarantined", "oldest_pending_seconds"}
    # numbers only: nothing that identifies a patient or a booking
    text = r.content.decode()
    for secret in (PATIENT_LOGIN, "pat.private", person, actor, booking):
        assert secret not in text


def test_the_safety_officer_can_look_too_but_other_platform_roles_cannot(platform):
    platform.staff("iyer", "R12", kind=trust.ActorType.SUPPORT)
    platform.staff("integrator", "R10", kind=trust.ActorType.WORKLOAD)
    platform.staff("clerk", "R04", kind=trust.ActorType.FACILITY_USER)
    assert platform.overview("iyer").status_code == 200
    assert platform.overview("integrator").status_code == 403
    assert platform.overview("clerk").status_code == 403


# ------------------------------------------------------------------ who is refused
def test_customers_and_patients_are_refused_and_it_is_the_tenant_check_that_stops_them(platform):
    platform.staff("vikram", "R11")
    clinic = Clinic()
    with tenant_transaction(clinic.ctx):
        support_actor = trust.register_actor(
            actor_type=trust.ActorType.SUPPORT, issuer="idp", subject="custsupport")
        trust.assign_role(actor_public_id=support_actor.public_id,
                          organization_public_id=clinic.org, role_code="R11", by="system")
        ops_actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="custops")
        trust.assign_role(actor_public_id=ops_actor.public_id,
                          organization_public_id=clinic.org, role_code="R04", by="system")
    patient, person = clinic.patient("pat")
    with tenant_transaction(clinic.ctx):
        # the policy table alone WOULD allow this role to read the platform class ...
        assert decide(actor_public_id=support_actor.public_id, operation="read:platform",
                      purpose_code="SUPPORT", organization_public_id=clinic.org).allowed
    for subject in ("custsupport", "pat", "custops"):
        r = platform.client.get(API + "platform/overview",
                                **platform.header(clinic.tenant_public_id, subject))
        # ... yet every one of them is refused, because their tenant is not a platform tenant
        assert r.status_code == 403, subject
        assert json.loads(r.content)["code"] == "POLICY_DENIED"
    assert Client().get(API + "platform/overview").status_code == 401
    assert person and patient


def test_platform_staff_land_in_the_platform_console_and_customer_staff_do_not(platform):
    assert home_for("support", ["R11"], "platform") == "platform"
    assert home_for("support", ["R12"], "platform") == "platform"
    assert home_for("support", ["R11"], "provider_org") == "operations"
    assert home_for("facility_user", ["R04"], "platform") == "operations"
    clinic = Clinic()
    with tenant_transaction(clinic.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="local",
                                     subject="custsupport")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=clinic.org,
                          role_code="R11", by="system")
        trust.set_password(actor.public_id, login="custsupport", password=PASSWORD, by="system")
    platform.staff("zoikostaff", "R11")
    with tenant_transaction(platform.ctx):
        pid = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="local",
                                   subject="vikram2")
        trust.assign_role(actor_public_id=pid.public_id, organization_public_id=platform.org,
                          role_code="R11", by="system")
        trust.set_password(pid.public_id, login="vikram2", password=PASSWORD, by="system")

    def home(tenant_name, login):
        r = Client().post("/api/v1/auth/login", json.dumps(
            {"organization": tenant_name, "login": login, "password": PASSWORD}),
            content_type="application/json")
        return r.json()["home"]

    assert home(platform.tenant_name, "vikram2") == "platform"
    assert home(clinic.tenant_name, "custsupport") == "operations"


# ------------------------------------------------------------------ what it reveals
def test_a_tampered_audit_log_shows_up_for_that_customer_only(platform):
    platform.staff("vikram", "R11")
    a, *_ = customer_with_a_booking()
    b = Clinic()
    with connection.cursor() as cursor:                  # an insider with full database access
        cursor.execute("ALTER TABLE evidence_auditevent DISABLE TRIGGER USER")
        cursor.execute("UPDATE evidence_auditevent SET outcome = 'denied' "
                       "WHERE tenant_id = %s AND chain_seq = 2", [a.ctx.tenant_id])
        cursor.execute("ALTER TABLE evidence_auditevent ENABLE TRIGGER USER")
    rows = rows_by_name(platform.overview("vikram"))
    assert rows[a.tenant_name]["audit"]["chain_ok"] is False
    assert rows[a.tenant_name]["audit"]["chain_bad_seq"] == 2
    assert rows[b.tenant_name]["audit"]["chain_ok"] is True
    assert rows[platform.tenant_name]["audit"]["chain_ok"] is True


def test_failed_sign_ins_are_counted_per_customer(platform):
    platform.staff("vikram", "R11")
    clinic = Clinic()
    with tenant_transaction(clinic.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.FACILITY_USER, issuer="local",
                                     subject="target")
        trust.set_password(actor.public_id, login="target", password=PASSWORD, by="system")
    for _ in range(3):
        with pytest.raises(Exception):  # noqa: B017 - ZoikoError, one answer for every failure
            auth.login(tenant_name=clinic.tenant_name, login="target", password="wrong-wrong-1")
    rows = rows_by_name(platform.overview("vikram"))
    assert rows[clinic.tenant_name]["audit"]["failed_logins"] == 3


def test_every_view_is_recorded_in_the_platform_tenants_own_chain_only(platform):
    platform.staff("vikram", "R11")
    clinic = Clinic()
    def clinic_events():
        with tenant_transaction(clinic.ctx):     # filtered: the test database user bypasses RLS
            return AuditEvent.objects.filter(tenant_id=clinic.ctx.tenant_id).count()

    before = clinic_events()
    platform.overview("vikram")
    platform.overview("vikram")
    with tenant_transaction(platform.ctx):
        views = AuditEvent.objects.filter(
            action="platform.overview", tenant_id=platform.ctx.tenant_id)
        assert views.count() == 2 and views.first().details == {"tenants": 2}
    assert clinic_events() == before        # the customer's log is untouched


def test_the_outbox_backlog_counts_events_across_the_cell(platform):
    platform.staff("vikram", "R11")
    customer_with_a_booking()
    customer_with_a_booking("other.patient@example.org")
    backlog = json.loads(platform.overview("vikram").content)["outbox"]
    assert backlog["pending"] == OutboxEvent.objects.count() >= 2
    assert backlog["oldest_pending_seconds"] is not None and backlog["quarantined"] == 0


def test_the_platform_console_works_with_a_real_token_and_the_dev_stub_off(platform, settings):
    platform.staff("seed", "R11")
    with tenant_transaction(platform.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="local",
                                     subject="realvikram")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=platform.org,
                          role_code="R11", by="system")
        trust.set_password(actor.public_id, login="realvikram", password=PASSWORD, by="system")
    token = auth.login(tenant_name=platform.tenant_name, login="realvikram",
                       password=PASSWORD).token
    settings.DEBUG = settings.DEV_AUTH_STUB = False
    r = Client().get(API + "platform/overview", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 200 and "tenants" in json.loads(r.content)
