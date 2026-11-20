"""'Remember me for 30 days' (DEV-129): only a patient can have it, only when they ask, and signing
out ends it everywhere."""
import datetime as dt

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust import auth
from tests.test_auth import PASSWORD, Site, bearer, post_login

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False


@pytest.fixture
def site():
    s = Site()
    s.user("pat@example.org", kind=trust.ActorType.PATIENT)
    s.user("ops", "R04")
    s.user("doc", "R03", kind=trust.ActorType.CLINICIAN)
    return s


def sign_in(site, login, remember):
    return site.client_login(login, remember) if hasattr(site, "client_login") else \
        trust.login(tenant_name=site.tenant_name, login=login, password=PASSWORD,
                    remember=remember)


def hours(result):
    return round((result.expires_at - utc_now()).total_seconds() / 3600)


def test_a_patient_who_asks_gets_thirty_days(site):
    result = sign_in(site, "pat@example.org", True)
    assert result.remembered is True and hours(result) in (719, 720)


def test_a_patient_who_does_not_ask_gets_the_short_login(site):
    result = sign_in(site, "pat@example.org", False)
    assert result.remembered is False and hours(result) == 8


@pytest.mark.parametrize("login", ["ops", "doc"])
def test_staff_and_doctors_always_get_the_short_login_even_if_they_ask(site, login):
    result = sign_in(site, login, True)
    assert result.remembered is False and hours(result) == 8


@pytest.mark.parametrize("value", ["true", "yes", 1, "1", None, [], {"x": 1}])
def test_only_a_plain_true_counts_as_asking_through_the_api(site, value):
    r = post_login(Client(), organization=site.tenant_name, login="pat@example.org",
                   password=PASSWORD, remember=value)
    assert r.status_code == 200
    body = r.json()
    assert body["remembered"] is False and body["expires_at"] < (
        utc_now() + dt.timedelta(hours=9)).isoformat()


def test_the_api_says_whether_it_was_remembered(site):
    r = post_login(Client(), organization=site.tenant_name, login="pat@example.org",
                   password=PASSWORD, remember=True)
    assert r.status_code == 200 and r.json()["remembered"] is True
    plain = post_login(Client(), organization=site.tenant_name, login="ops",
                       password=PASSWORD, remember=True)
    assert plain.json()["remembered"] is False


def test_a_remembered_token_works_for_a_month_and_not_a_day_longer(site):
    token = sign_in(site, "pat@example.org", True).token
    auth.read_token(token, now=utc_now() + dt.timedelta(days=29, hours=23))
    with pytest.raises(ZoikoError):
        auth.read_token(token, now=utc_now() + dt.timedelta(days=30, minutes=5))


def test_a_short_token_still_ends_after_eight_hours(site):
    token = sign_in(site, "pat@example.org", False).token
    with pytest.raises(ZoikoError):
        auth.read_token(token, now=utc_now() + dt.timedelta(hours=8, minutes=5))


def test_signing_out_ends_every_remembered_device_at_once(site):
    first = sign_in(site, "pat@example.org", True)
    second = sign_in(site, "pat@example.org", True)
    client = Client()
    assert client.get("/api/v1/me", **bearer(first.token)).status_code == 200
    assert client.get("/api/v1/me", **bearer(second.token)).status_code == 200
    with tenant_transaction(site.ctx):
        trust.revoke_sessions(first.actor_public_id, by=first.actor_public_id)
    assert client.get("/api/v1/me", **bearer(first.token)).status_code == 401
    assert client.get("/api/v1/me", **bearer(second.token)).status_code == 401


def test_changing_the_password_ends_a_remembered_login(site):
    old = sign_in(site, "pat@example.org", True)
    with tenant_transaction(site.ctx):
        trust.set_password(old.actor_public_id, login="pat@example.org",
                           password="Another-Long-Pass-77", by="system")
    assert Client().get("/api/v1/me", **bearer(old.token)).status_code == 401


def test_a_remembered_login_is_recorded_as_such_in_the_audit_trail(site):
    sign_in(site, "pat@example.org", True)
    sign_in(site, "pat@example.org", False)
    with tenant_transaction(site.ctx):
        reasons = [e.reason_code for e in AuditEvent.objects.filter(action="auth.login")
                   .order_by("chain_seq")]
    assert reasons.count("remembered") == 1


def test_a_wrong_password_with_remember_is_the_same_refusal_as_without(site):
    client = Client()
    a = post_login(client, organization=site.tenant_name, login="pat@example.org",
                   password="wrong-password-1", remember=True)
    b = post_login(client, organization=site.tenant_name, login="pat@example.org",
                   password="wrong-password-1")
    assert a.status_code == b.status_code == 401
    same = ("type", "title", "status", "code")
    assert {k: a.json().get(k) for k in same} == {k: b.json().get(k) for k in same}
