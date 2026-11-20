import json
import re
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedEmail
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import ActorType, AuthToken, Credential
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"
LOGIN = "new.patient@example.org"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False
    settings.PATIENT_APP_URL = "http://patient.test"
    settings.STAFF_APP_URL = "http://staff.test"
    settings.CLINICIAN_APP_URL = "http://doctor.test"
    settings.PLATFORM_APP_URL = "http://platform.test"


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


def set_email(token, email="asha@example.org", current=PASSWORD):
    return post("auth/email", token, email=email, current_password=current)


def mails(kind):
    return list(QueuedEmail.objects.filter(kind=kind))


def credential(site):
    with tenant_transaction(site.ctx):
        return Credential.objects.get(login=LOGIN)


def clear_mail(site):
    """Forget the mail and verification links sent so far, so the one-a-minute limit does not
    apply to the step under test."""
    QueuedEmail.objects.all().delete()
    with tenant_transaction(site.ctx):
        AuthToken.objects.all().delete()


def verify_now(site):
    with tenant_transaction(site.ctx):
        Credential.objects.filter(login=LOGIN).update(email_verified_at=utc_now())


def test_setting_an_email_stores_it_unverified_and_sends_a_link(site):
    token = sign_up(site)
    clear_mail(site)
    r = set_email(token)
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    assert r.json() == {"email": "a***@example.org", "verified": False, "sent": True}
    cred = credential(site)
    assert cred.email == "asha@example.org" and cred.email_verified_at is None
    (mail,) = mails("verify_email")
    assert "http://patient.test/?action=verify&token=" in mail.body


def test_the_address_is_cleaned_and_bad_ones_are_refused(site):
    token = sign_up(site)
    assert set_email(token, email="  Asha@Example.ORG ").status_code == 200
    assert credential(site).email == "asha@example.org"
    for bad in ("not-an-email", "a@b.org\nbcc:x@y.org", "a@b.org,c@d.org", "", "@x.org"):
        assert set_email(token, email=bad).status_code == 422
    assert credential(site).email == "asha@example.org"


def test_a_wrong_password_changes_nothing_and_is_not_a_401(site):
    token = sign_up(site)
    clear_mail(site)
    r = set_email(token, current="Not-The-Password-1")
    assert r.status_code == 422 and r.json()["fields"] == ["current_password"]
    assert credential(site).email == LOGIN or credential(site).email == ""
    assert mails("verify_email") == []
    assert Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200


def test_wrong_passwords_lock_the_account_like_sign_in_does(site):
    token = sign_up(site)
    for _ in range(5):
        assert set_email(token, current="Not-The-Password-1").status_code == 422
    assert set_email(token).status_code == 422                     # locked: even the right one
    assert credential(site).locked_until is not None


def test_both_values_must_be_text(site):
    token = sign_up(site)
    for body in ({}, {"email": "a@b.org"}, {"email": 1, "current_password": PASSWORD},
                 {"email": "a@b.org", "current_password": None}):
        assert post("auth/email", token, **body).status_code == 422


def test_it_needs_a_login(site):
    assert post("auth/email", None, email="a@b.org", current_password="x").status_code == 401


def test_changing_a_verified_address_warns_the_old_one_and_starts_again(site):
    token = sign_up(site)
    set_email(token, email="old@example.org")
    verify_now(site)
    clear_mail(site)
    r = set_email(token, email="new@example.org")
    assert r.json()["verified"] is False and r.json()["sent"] is True
    (warning,) = mails("email_changed")
    assert warning.subject == "The email of your Zoiko Access account changed"
    assert "new@example.org" not in warning.body and "n***@example.org" in warning.body
    assert len(mails("verify_email")) == 1
    cred = credential(site)
    assert cred.email == "new@example.org" and cred.email_verified_at is None


def test_an_unverified_old_address_gets_no_warning(site):
    token = sign_up(site)
    set_email(token, email="old@example.org")
    clear_mail(site)
    set_email(token, email="new@example.org")
    assert mails("email_changed") == []


def test_the_old_verification_link_stops_working_after_a_change(site):
    token = sign_up(site)
    clear_mail(site)
    set_email(token, email="old@example.org")
    first = re.findall(r"token=([\w.\-]+)", str(mails("verify_email")[0].body))[0]
    clear_mail(site)
    set_email(token, email="new@example.org")
    link = Client().post("/api/v1/auth/verify-email", json.dumps({"token": first}),
                         content_type="application/json")
    assert link.status_code == 422                       # the old link no longer works
    assert credential(site).email_verified_at is None


def test_the_same_verified_address_again_changes_nothing(site):
    token = sign_up(site)
    set_email(token, email="asha@example.org")
    verify_now(site)
    clear_mail(site)
    r = set_email(token, email="asha@example.org")
    assert r.json() == {"email": "a***@example.org", "verified": True, "sent": False}
    assert mails("verify_email") == [] and mails("email_changed") == []


def test_sending_again_too_soon_still_saves_the_address(site):
    token = sign_up(site)
    clear_mail(site)
    set_email(token, email="one@example.org")
    r = set_email(token, email="two@example.org")           # inside the one-a-minute limit
    assert r.status_code == 200 and r.json()["sent"] is False
    assert credential(site).email == "two@example.org"


def test_the_link_opens_the_app_that_fits_the_person(site):
    with tenant_transaction(site.ctx):
        doctor = trust.register_actor(
            actor_type=ActorType.CLINICIAN, issuer="idp", subject="dr.one")
        trust.set_password(doctor.public_id, login="dr.one", password=PASSWORD, by="system")
        facility = trust.register_actor(
            actor_type=ActorType.FACILITY_USER, issuer="idp", subject="desk.one")
        trust.set_password(facility.public_id, login="desk.one", password=PASSWORD, by="system")
    for login, base in (("dr.one", "http://doctor.test"), ("desk.one", "http://staff.test")):
        clear_mail(site)
        sign_in = post("auth/login", organization=site.tenant_name, login=login, password=PASSWORD)
        assert sign_in.status_code == 200
        set_email(sign_in.json()["token"], email=f"{login}@example.org")
        (mail,) = mails("verify_email")
        assert f"{base}/?action=verify&token=" in mail.body


def test_attempts_are_audited_without_a_password(site):
    token = sign_up(site)
    set_email(token, current="Not-The-Password-1")
    set_email(token)
    with tenant_transaction(site.ctx):
        rows = list(AuditEvent.objects.filter(action__in=["auth.set_email", "auth.email_changed"]))
    assert sorted(r.action for r in rows) == ["auth.email_changed", "auth.set_email"]
    dump = json.dumps([[str(v) for v in vars(r).values()] for r in rows])
    assert PASSWORD not in dump and "Not-The-Password-1" not in dump


def test_a_platform_staff_member_gets_the_platform_link(site):
    platform = World(tenant_type=trust.TenantType.PLATFORM)
    with tenant_transaction(platform.ctx):
        staff = trust.register_actor(
            actor_type=ActorType.SUPPORT, issuer="idp", subject="support.one")
        trust.set_password(staff.public_id, login="support.one", password=PASSWORD, by="system")
    QueuedEmail.objects.all().delete()
    sign_in = post("auth/login", organization=platform.tenant_name, login="support.one",
                   password=PASSWORD)
    assert sign_in.status_code == 200
    set_email(sign_in.json()["token"], email="support.one@example.org")
    (mail,) = mails("verify_email")
    assert "http://platform.test/?action=verify&token=" in mail.body
