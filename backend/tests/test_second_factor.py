# ruff: noqa: F811  (pytest fixtures are imported from test_sso and named as arguments)
"""Two-step sign-in (DEV-356): turning it on, signing in with a code or a recovery code, what is
refused, the lock, an administrator's reset, and unlinking an outside sign-in."""
import datetime as dt
import json

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock, utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust import second_factor
from modules.trust.models import Credential, ExternalIdentity, SecondFactor
from tests.test_sso import (  # noqa: F401 - fixtures
    PASSWORD,
    idp,
    post,
    real_login_only,
    sign_in_with_provider,
    sign_up,
    site,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

LOGIN = "asha.rao@example.org"


def body(r):
    return json.loads(r.content)


def sign_in(site, login=LOGIN, password=PASSWORD):
    return post("auth/login", organization=site.tenant_name, login=login, password=password)


def account(site):
    sign_up(site)
    return body(sign_in(site))["token"]


def current_code(secret, ahead=0):
    return second_factor.code_at(secret, second_factor.step_of(utc_now()) + ahead)


def turn_on(site):
    """A patient with two-step sign-in on: (token, secret, recovery codes). The code used to
    confirm is the earlier step's, so the next sign-in can use the current one."""
    token = account(site)
    started = post("auth/second-factor/start", token, password=PASSWORD)
    assert started.status_code == 200, started.content
    secret = body(started)["secret"]
    confirmed = post("auth/second-factor/confirm", token, code=current_code(secret, -1))
    assert confirmed.status_code == 200, confirmed.content
    return token, secret, body(confirmed)["recovery_codes"]


def challenge(site):
    r = sign_in(site)
    assert r.status_code == 200, r.content
    got = body(r)
    assert got["second_factor_required"] is True and "token" not in got
    return got["challenge"]


def verify(ch, code):
    return post("auth/second-factor/verify", challenge=ch, code=code)


def state(token):
    r = Client().get("/api/v1/auth/second-factor", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 200, r.content
    return json.loads(r.content)


# ------------------------------------------------------------------ the codes themselves
def test_the_codes_follow_the_published_standard():
    # RFC 6238 appendix B, SHA-1, the secret "12345678901234567890" (six digits of its eight).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert second_factor.code_at(secret, 59 // 30) == "287082"
    assert second_factor.code_at(secret, 1111111109 // 30) == "081804"


# ------------------------------------------------------------------ turning it on
def test_it_is_off_until_a_code_proves_the_authenticator_works(site):
    token = account(site)
    secret = body(post("auth/second-factor/start", token, password=PASSWORD))["secret"]
    assert state(token) == {"enabled": False, "recovery_left": 0}
    assert "token" in body(sign_in(site))                      # still one step
    assert len(secret) == 32
    wrong = "".join(str((int(d) + 5) % 10) for d in current_code(secret))
    assert post("auth/second-factor/confirm", token, code=wrong).status_code in (200, 422)


def test_turning_it_on_gives_ten_recovery_codes_once_and_stores_no_secret_in_clear(site):
    token, secret, codes = turn_on(site)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert state(token) == {"enabled": True, "recovery_left": 10}
    with tenant_transaction(site.ctx):
        row = SecondFactor.objects.get()
    assert secret not in row.secret_enc
    assert not any(c in json.dumps(row.recovery_hashes) for c in codes)


def test_setting_it_up_needs_the_password(site):
    token = account(site)
    r = post("auth/second-factor/start", token, password="wrong-password-1")
    assert r.status_code == 422 and state(token)["enabled"] is False


def test_it_cannot_be_started_again_while_on(site):
    token, _, _ = turn_on(site)
    assert post("auth/second-factor/start", token, password=PASSWORD).status_code == 409


def test_these_pages_need_a_login(site):
    assert Client().get("/api/v1/auth/second-factor").status_code == 401
    assert post("auth/second-factor/start", password=PASSWORD).status_code == 401
    assert Client().get("/api/v1/auth/identities").status_code == 401


# ------------------------------------------------------------------ signing in
def test_the_password_alone_no_longer_signs_in_and_the_code_does(site):
    _, secret, _ = turn_on(site)
    ch = challenge(site)
    done = verify(ch, current_code(secret))
    assert done.status_code == 200, done.content
    assert body(done)["token"] and body(done)["home"] == "patient"


def test_a_challenge_is_not_a_session(site):
    turn_on(site)
    ch = challenge(site)
    r = Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {ch}")
    assert r.status_code == 401


def test_a_code_works_only_once(site):
    _, secret, _ = turn_on(site)
    code = current_code(secret)
    assert verify(challenge(site), code).status_code == 200
    assert verify(challenge(site), code).status_code == 401


def test_a_recovery_code_works_once_and_is_counted_down(site):
    _, _, codes = turn_on(site)
    done = verify(challenge(site), codes[0].upper())            # case does not matter
    assert done.status_code == 200
    assert state(body(done)["token"])["recovery_left"] == 9
    assert verify(challenge(site), codes[0]).status_code == 401


@pytest.mark.parametrize("bad", ["000000", "12345", "abcde-fghij", "", None, 123456])
def test_a_wrong_code_is_the_same_refusal(site, bad):
    turn_on(site)
    assert verify(challenge(site), bad).status_code == 401


def test_a_damaged_or_old_challenge_is_refused(site):
    _, secret, _ = turn_on(site)
    ch = challenge(site)
    assert verify(ch[:-2] + "xx", current_code(secret)).status_code == 401
    later = FixedClock(utc_now() + dt.timedelta(minutes=6))
    from foundation.errors import ZoikoError

    with pytest.raises(ZoikoError):
        trust.second_factor_verify(challenge=ch, code=current_code(secret), clock=later)


def test_five_wrong_codes_lock_the_login_even_across_new_password_steps(site):
    _, secret, _ = turn_on(site)
    for _ in range(5):
        assert verify(challenge(site), "000000").status_code == 401   # the password step again
    assert sign_in(site).status_code == 401                            # now locked
    with tenant_transaction(site.ctx):
        cred = Credential.objects.get()
        assert cred.locked_until is not None
        reasons = set(AuditEvent.objects.filter(action="auth.second_factor_failed")
                      .values_list("reason_code", flat=True))
    assert reasons == {"bad_code"} and secret


def test_a_password_change_makes_an_open_challenge_useless(site):
    token, secret, _ = turn_on(site)
    ch = challenge(site)
    changed = post("auth/change-password", token, current_password=PASSWORD,
                   new_password="Another-Horse-77")
    assert changed.status_code == 200, changed.content
    assert verify(ch, current_code(secret)).status_code == 401


def test_signing_in_with_a_provider_also_needs_the_code(site, idp):
    _, secret, _ = turn_on(site)
    r = sign_in_with_provider(site, idp)
    assert r.status_code == 200, r.content
    got = body(r)
    assert got["second_factor_required"] is True and "token" not in got
    assert verify(got["challenge"], current_code(secret)).status_code == 200


# ------------------------------------------------------------------ turning it off
def test_turning_it_off_needs_the_password_and_a_code(site):
    token, secret, _ = turn_on(site)
    off = "auth/second-factor/disable"
    assert post(off, token, password="nope-nope-nope", code=current_code(secret)).status_code == 422
    assert post(off, token, password=PASSWORD, code="000000").status_code == 422
    assert state(token)["enabled"] is True
    assert post(off, token, password=PASSWORD, code=current_code(secret)).status_code == 200
    assert state(token)["enabled"] is False
    assert "token" in body(sign_in(site))


# ------------------------------------------------------------------ an administrator's reset
def test_an_administrator_resets_a_team_members_second_step(settings):
    from tests.test_clinician import Practice

    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    p = Practice()
    boss = p.login("boss", trust.ActorType.FACILITY_USER, "R13")
    with tenant_transaction(p.clinic.ctx):
        trust.set_password(p.desk, login="desk@example.org", password=PASSWORD, by="system")
        from modules.trust.models import Actor

        SecondFactor.objects.create(
            actor=Actor.objects.get(public_id=p.desk), secret_enc="x", confirmed_at=utc_now(),
            cell_id=p.clinic.ctx.cell_id, tenant_id=p.clinic.ctx.tenant_id)
    path = f"staff/members/{p.desk}/second-factor-reset"
    assert p.post(path, subject="doc").status_code == 403
    assert p.post(path, subject="pat").status_code == 403
    assert p.post(f"staff/members/{boss}/second-factor-reset", subject="boss").status_code == 403
    assert p.post(path, subject="boss").status_code == 200
    with tenant_transaction(p.clinic.ctx):
        assert SecondFactor.objects.count() == 0
    assert p.post(path, subject="boss").status_code == 409      # nothing left to reset


# ------------------------------------------------------------------ outside sign-ins
def identities(token):
    r = Client().get("/api/v1/auth/identities", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.status_code == 200, r.content
    return json.loads(r.content)["identities"]


def test_an_unlinked_provider_is_refused_and_not_linked_again_until_allowed(site, idp):
    token = account(site)
    assert sign_in_with_provider(site, idp).status_code == 200
    (linked,) = identities(token)
    assert linked["provider_name"] == "Acme ID" and linked["unlinked_at"] is None
    path = f"auth/identities/{linked['id']}"
    assert post(f"{path}/unlink", token, password="wrong-password-1").status_code == 422
    assert post(f"{path}/unlink", token, password=PASSWORD).status_code == 200
    assert sign_in_with_provider(site, idp).status_code == 401
    with tenant_transaction(site.ctx):
        assert ExternalIdentity.objects.count() == 1           # kept, not linked afresh
    assert post(f"{path}/allow", token, password=PASSWORD).status_code == 200
    assert sign_in_with_provider(site, idp).status_code == 200


def test_nobody_changes_someone_elses_outside_sign_in(site, idp):
    token = account(site)
    sign_in_with_provider(site, idp)
    (linked,) = identities(token)
    sign_up(site, "other@example.org", "other@example.net")
    other = body(sign_in(site, "other@example.org"))["token"]
    assert identities(other) == []
    r = post(f"auth/identities/{linked['id']}/unlink", other, password=PASSWORD)
    assert r.status_code == 404
    assert identities(token)[0]["unlinked_at"] is None
    assert post(f"auth/identities/{linked['id']}/remove", token,
                password=PASSWORD).status_code == 404
