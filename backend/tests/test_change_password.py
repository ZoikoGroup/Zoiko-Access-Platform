import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedEmail
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import Credential
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"
NEW_PASSWORD = "Brand-New-Passw0rd"
LOGIN = "new.patient@example.org"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False


@pytest.fixture
def site():
    world = World()
    with tenant_transaction(world.ctx):
        trust.set_self_registration(True, by="system")
    return world


def post(path, token=None, **body):
    extra: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return Client().post(f"/api/v1/{path}", json.dumps(body), content_type="application/json",
                         **extra)


def sign_up(site):
    r = post("auth/register", organization=site.tenant_name, login=LOGIN, password=PASSWORD)
    assert r.status_code == 201
    return r.json()["token"]


def sign_in(site, password):
    return post("auth/login", organization=site.tenant_name, login=LOGIN, password=password)


def change(token, current=PASSWORD, new=NEW_PASSWORD):
    return post("auth/change-password", token, current_password=current, new_password=new)


def me(token):
    return Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code


def test_changing_the_password_switches_to_the_new_one(site):
    token = sign_up(site)
    r = change(token)
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    assert sign_in(site, PASSWORD).status_code == 401
    assert sign_in(site, NEW_PASSWORD).status_code == 200


def test_this_session_continues_with_a_fresh_token_and_the_old_one_stops(site):
    token = sign_up(site)
    other_device = sign_in(site, PASSWORD).json()["token"]
    fresh = change(token).json()["token"]
    assert fresh != token
    assert me(fresh) == 200
    assert me(token) == 401 and me(other_device) == 401        # every other session ended


def test_a_wrong_current_password_changes_nothing_and_is_not_a_401(site):
    token = sign_up(site)
    r = change(token, current="Not-The-Password-1")
    assert r.status_code == 422 and r.json()["fields"] == ["current_password"]
    assert me(token) == 200                                    # the session is still good
    assert sign_in(site, PASSWORD).status_code == 200
    assert sign_in(site, NEW_PASSWORD).status_code == 401


def test_wrong_guesses_lock_the_account_like_sign_in_does(site):
    token = sign_up(site)
    for _ in range(5):
        assert change(token, current="Not-The-Password-1").status_code == 422
    assert change(token).status_code == 422                    # locked: even the right one fails
    assert sign_in(site, PASSWORD).status_code == 401          # and so does signing in
    with tenant_transaction(site.ctx):
        assert Credential.objects.get(login=LOGIN).locked_until is not None


def test_a_weak_or_unchanged_new_password_is_refused(site):
    token = sign_up(site)
    for new in ("short", PASSWORD, LOGIN, "password"):
        assert change(token, new=new).status_code == 422
    assert me(token) == 200 and sign_in(site, PASSWORD).status_code == 200


def test_both_passwords_must_be_text(site):
    token = sign_up(site)
    for body in ({}, {"current_password": PASSWORD}, {"current_password": 1, "new_password": 2},
                 {"current_password": None, "new_password": NEW_PASSWORD}):
        assert post("auth/change-password", token, **body).status_code == 422


def test_it_needs_a_login(site):
    assert post("auth/change-password", None, current_password="a", new_password="b"
                ).status_code == 401
    assert change("not-a-token").status_code == 401


def verify_email(site):
    with tenant_transaction(site.ctx):
        Credential.objects.filter(login=LOGIN).update(
            email=LOGIN, email_verified_at=utc_now())


def test_a_verified_address_gets_a_notice_that_never_holds_a_password(site):
    token = sign_up(site)
    verify_email(site)
    QueuedEmail.objects.all().delete()
    change(token)
    (mail,) = QueuedEmail.objects.filter(kind="password_changed")
    assert mail.subject == "Your Zoiko Access password was changed"
    assert PASSWORD not in mail.body and NEW_PASSWORD not in mail.body


def test_an_unverified_address_gets_nothing(site):
    token = sign_up(site)
    QueuedEmail.objects.filter(kind="password_changed").delete()
    change(token)
    assert QueuedEmail.objects.filter(kind="password_changed").count() == 0


def test_attempts_are_audited_without_any_password(site):
    token = sign_up(site)
    change(token, current="Not-The-Password-1")
    change(token)
    with tenant_transaction(site.ctx):
        rows = list(AuditEvent.objects.filter(action="auth.change_password"))
    assert sorted(r.outcome for r in rows) == ["allowed", "denied"]
    dump = json.dumps([[str(v) for v in vars(r).values()] for r in rows])
    assert PASSWORD not in dump and NEW_PASSWORD not in dump and "Not-The-Password-1" not in dump
