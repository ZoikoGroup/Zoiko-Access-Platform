import datetime as dt

import pytest
from django.db import connection
from django.test import override_settings

from foundation.cell_context import build_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, FixedClock, utc_now
from modules.evidence.api import verify_chain
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import Actor, Organization, Role, RoleAssignment
from modules.trust.roles_seed import DEFAULT_ROLES
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

DEV = {"DEBUG": True, "DEV_AUTH_STUB": True}


def make_tenant(name="Alpha Clinics"):
    tenant = trust.create_tenant(name=name, type=trust.TenantType.PROVIDER_ORG)
    return tenant, build_context(tenant.id)


def code_of(excinfo):
    return excinfo.value.code


# ------------------------------------------------------------------ tenants
def test_a_new_tenant_gets_the_default_roles_and_an_audit_chain():
    tenant, ctx = make_tenant()
    assert tenant.cell_id == ctx.cell_id and len(tenant.public_id) == 22
    with tenant_transaction(ctx):
        roles = Role.objects.filter(tenant_id=ctx.tenant_id)
        codes = sorted(roles.values_list("role_code", flat=True))
        assert codes == sorted(r[0] for r in DEFAULT_ROLES) and len(codes) == len(DEFAULT_ROLES)
        events = AuditEvent.objects.filter(tenant_id=ctx.tenant_id)
        assert [e.action for e in events] == ["tenant.create"]
        assert verify_chain().ok


def test_tenant_names_are_unique_within_a_cell():
    make_tenant("Dup")
    with pytest.raises(ZoikoError) as excinfo:
        make_tenant("Dup")
    assert code_of(excinfo) == ErrorCode.CONFLICT_VERSION
    with pytest.raises(ZoikoError):
        trust.create_tenant(name="  ", type=trust.TenantType.PAYER)


# ------------------------------------------------------------------ organizations and actors
def test_organization_and_actor_are_created_stamped_and_audited():
    _, ctx = make_tenant()
    with tenant_transaction(ctx):
        org = trust.create_organization(name="North Clinic")
        actor = trust.register_actor(
            actor_type=trust.ActorType.CLINICIAN, issuer="idp", subject="u-1")
        assert (org.tenant_id, actor.tenant_id) == (ctx.tenant_id, ctx.tenant_id)
        actions = list(AuditEvent.objects.order_by("chain_seq").values_list("action", flat=True))
        assert actions == ["tenant.create", "organization.create", "actor.register"]


def test_registering_the_same_identity_twice_returns_the_same_actor():
    _, ctx = make_tenant()
    with tenant_transaction(ctx):
        one = trust.register_actor(actor_type=trust.ActorType.CLINICIAN, issuer="idp", subject="u")
        two = trust.register_actor(actor_type=trust.ActorType.CLINICIAN, issuer="idp", subject="u")
        assert one.pk == two.pk and Actor.objects.count() == 1


def test_duplicate_organization_names_are_refused_per_tenant_but_not_across_tenants():
    _, ctx_a = make_tenant("A")
    _, ctx_b = make_tenant("B")
    with tenant_transaction(ctx_a):
        trust.create_organization(name="Main")
        with pytest.raises(ZoikoError):
            trust.create_organization(name="Main")
    with tenant_transaction(ctx_b):
        trust.create_organization(name="Main")


# ------------------------------------------------------------------ roles
def setup_actor_and_org(ctx, subject="u-1"):
    with tenant_transaction(ctx):
        org = trust.create_organization(name="Main")
        actor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject=subject)
    return actor.public_id, org.public_id


def test_assigning_a_role_makes_it_active_and_it_ends_without_deleting_history():
    _, ctx = make_tenant()
    actor, org = setup_actor_and_org(ctx)
    with tenant_transaction(ctx):
        a = trust.assign_role(actor_public_id=actor, organization_public_id=org,
                              role_code="R04", by="system")
        assert trust.active_roles(actor) == [("R04", org)]
        trust.end_assignment(a.public_id, reason="contract_end", by="system")
        assert trust.active_roles(actor) == []
        row = RoleAssignment.objects.get(pk=a.pk)
        assert row.status == "ended" and row.removal_reason == "contract_end"
        assert row.entity_version == 2
        trust.end_assignment(a.public_id, reason="again", by="system")    # repeat is harmless
        assert RoleAssignment.objects.get(pk=a.pk).removal_reason == "contract_end"


def test_a_role_with_an_end_date_stops_applying_after_it():
    _, ctx = make_tenant()
    actor, org = setup_actor_and_org(ctx)
    until = utc_now() + dt.timedelta(days=30)
    with tenant_transaction(ctx):
        trust.assign_role(actor_public_id=actor, organization_public_id=org, role_code="R03",
                          effective_until=until, by="system")
        assert trust.active_roles(actor, at=utc_now()) != []
        assert trust.active_roles(actor, at=until + dt.timedelta(seconds=1)) == []
        assert trust.active_roles(actor, at=utc_now() - dt.timedelta(days=1)) == []  # not yet


def test_bad_assignments_are_refused():
    _, ctx = make_tenant()
    actor, org = setup_actor_and_org(ctx)
    with tenant_transaction(ctx):
        base = {"actor_public_id": actor, "organization_public_id": org, "by": "system"}
        with pytest.raises(ZoikoError) as e1:
            trust.assign_role(role_code="R99", **base)                      # no such role
        assert code_of(e1) == ErrorCode.VALIDATION_FAILED
        with pytest.raises(ZoikoError):
            trust.assign_role(role_code="R03", effective_until=utc_now() - dt.timedelta(1), **base)
        with pytest.raises(ZoikoError):
            trust.assign_role(role_code="R03",
                              effective_until=dt.datetime(2099, 1, 1), **base)  # naive time
        trust.assign_role(role_code="R03", **base)
        with pytest.raises(ZoikoError) as e2:
            trust.assign_role(role_code="R03", **base)                      # already held
        assert code_of(e2) == ErrorCode.CONFLICT_VERSION


def test_a_suspended_actor_holds_no_roles_and_cannot_get_new_ones():
    _, ctx = make_tenant()
    actor, org = setup_actor_and_org(ctx)
    with tenant_transaction(ctx):
        trust.assign_role(actor_public_id=actor, organization_public_id=org,
                          role_code="R04", by="system")
        trust.set_actor_status(actor, trust.Status.SUSPENDED, by="system")
        assert trust.active_roles(actor) == []
        with pytest.raises(ZoikoError) as excinfo:
            trust.assign_role(actor_public_id=actor, organization_public_id=org,
                              role_code="R03", by="system")
        assert code_of(excinfo) == ErrorCode.POLICY_DENIED
        trust.set_actor_status(actor, trust.Status.ACTIVE, by="system")
        assert trust.active_roles(actor) == [("R04", org)]


def test_role_checks_use_the_supplied_clock_for_the_start_time():
    _, ctx = make_tenant()
    actor, org = setup_actor_and_org(ctx)
    future = FixedClock(dt.datetime(2030, 1, 1, tzinfo=UTC))
    with tenant_transaction(ctx):
        trust.assign_role(actor_public_id=actor, organization_public_id=org,
                          role_code="R04", by="system", clock=future)
        assert trust.active_roles(actor) == []                    # starts in 2030
        assert trust.active_roles(actor, at=future.now()) != []


# ------------------------------------------------------------------ tenant isolation
def test_one_tenant_cannot_see_or_touch_another_tenants_people():
    _, ctx_a = make_tenant("A")
    _, ctx_b = make_tenant("B")
    actor_a, org_a = setup_actor_and_org(ctx_a)
    _, org_b = setup_actor_and_org(ctx_b, subject="u-2")
    with tenant_transaction(ctx_b):
        with pytest.raises(ZoikoError) as e1:
            trust.active_roles(actor_a)
        assert code_of(e1) == ErrorCode.NOT_FOUND
        with pytest.raises(ZoikoError) as e2:
            trust.assign_role(actor_public_id=actor_a, organization_public_id=org_b,
                              role_code="R04", by="system")
        assert code_of(e2) == ErrorCode.NOT_FOUND
        with pytest.raises(ZoikoError):
            trust.set_actor_status(actor_a, trust.Status.SUSPENDED, by="system")
    with tenant_transaction(ctx_a):
        assert trust.active_roles(actor_a) == []                  # untouched


def test_the_restricted_app_role_sees_only_its_own_tenant():
    _, ctx_a = make_tenant("A")
    _, ctx_b = make_tenant("B")
    setup_actor_and_org(ctx_a)
    with tenant_transaction(ctx_b), as_app_role():
        assert Actor.objects.count() == 0 and Organization.objects.count() == 0
        assert Role.objects.count() == len(DEFAULT_ROLES)             # its own roles only
    with tenant_transaction(ctx_a), as_app_role():
        assert Actor.objects.count() == 1


def test_services_work_as_the_restricted_app_role():
    _, ctx = make_tenant()
    with tenant_transaction(ctx), as_app_role():
        org = trust.create_organization(name="Main")
        actor = trust.register_actor(
            actor_type=trust.ActorType.CLINICIAN, issuer="idp", subject="u")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=org.public_id,
                          role_code="R03", by=actor.public_id)
    with tenant_transaction(ctx):
        assert verify_chain().ok


# ------------------------------------------------------------------ dev login
def test_dev_login_finds_the_actor_and_binds_its_tenant():
    tenant, ctx = make_tenant()
    actor, _ = setup_actor_and_org(ctx)
    with override_settings(**DEV):
        result = trust.authenticate(f"Bearer dev:{tenant.public_id}:idp:u-1")
    assert result.context == ctx and result.actor_public_id == actor
    assert result.actor_type == "facility_user"


@pytest.mark.parametrize("header", [
    None, "", "Bearer abc", "Bearer dev:onlyone", "Bearer dev:a:b:c:d",
    "Bearer dev:{t}:idp:nobody", "Bearer dev:nosuchtenant:idp:u-1", "Bearer dev:{t}:idp:u 1",
    "Basic dev:{t}:idp:u-1",
])
def test_dev_login_refuses_everything_else_with_the_same_answer(header):
    tenant, ctx = make_tenant()
    setup_actor_and_org(ctx)
    header = header.replace("{t}", tenant.public_id) if header else header
    with override_settings(**DEV), pytest.raises(ZoikoError) as excinfo:
        trust.authenticate(header)
    assert code_of(excinfo) == ErrorCode.UNAUTHENTICATED


def test_dev_login_is_off_unless_debug_and_the_stub_flag_are_both_on():
    tenant, ctx = make_tenant()
    setup_actor_and_org(ctx)
    header = f"Bearer dev:{tenant.public_id}:idp:u-1"
    for flags in ({"DEBUG": False, "DEV_AUTH_STUB": True}, {"DEBUG": True, "DEV_AUTH_STUB": False}):
        with override_settings(**flags), pytest.raises(ZoikoError):
            trust.authenticate(header)


def test_dev_login_refuses_suspended_actors_and_tenants_and_other_tenants_people():
    tenant, ctx = make_tenant()
    other, ctx_o = make_tenant("Other")
    actor, _ = setup_actor_and_org(ctx)
    header = f"Bearer dev:{tenant.public_id}:idp:u-1"
    with override_settings(**DEV):
        with pytest.raises(ZoikoError):                          # u-1 does not exist in Other
            trust.authenticate(f"Bearer dev:{other.public_id}:idp:u-1")
        with tenant_transaction(ctx):
            trust.set_actor_status(actor, trust.Status.SUSPENDED, by="system")
        with pytest.raises(ZoikoError):
            trust.authenticate(header)
        with tenant_transaction(ctx):
            trust.set_actor_status(actor, trust.Status.ACTIVE, by="system")
        assert trust.authenticate(header).actor_public_id == actor
        with connection.cursor() as cursor:
            cursor.execute("UPDATE trust_tenant SET status = 'suspended' WHERE id = %s",
                           [tenant.id])
        with pytest.raises(ZoikoError):
            trust.authenticate(header)


def test_dev_login_accepts_email_style_login_ids_but_not_spaces_or_colons():
    tenant, ctx = make_tenant()
    with tenant_transaction(ctx):
        trust.register_actor(
            actor_type=trust.ActorType.PATIENT, issuer="dev-idp", subject="asha.p+x@example.org")
    with override_settings(**DEV):
        got = trust.authenticate(f"Bearer dev:{tenant.public_id}:dev-idp:asha.p+x@example.org")
        assert got.tenant_name == tenant.name
        for bad in ("asha p@example.org", "asha:p@example.org"):
            with pytest.raises(ZoikoError):
                trust.authenticate(f"Bearer dev:{tenant.public_id}:dev-idp:{bad}")
