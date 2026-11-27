"""Two-step sign-in with an authenticator app (DEV-380): setting it up, signing in with it,
recovery codes, the customer's rule for all staff, and the re-check before risky actions. Mostly
what must be REFUSED: replayed codes, guessing past the lockout, tickets used as sessions."""
import datetime as dt
import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust import two_step
from modules.trust.models import Credential
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False


class Clock:
    """The two-step module's clock, moved by the test (codes change every 30 seconds)."""

    def __init__(self) -> None:
        self.at = utc_now()

    def __call__(self, clock: Any = None) -> dt.datetime:
        return self.at

    def wait(self, seconds: float) -> None:
        self.at += dt.timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(two_step, "utc_now", c)
    return c


class Site(World):
    def __init__(self) -> None:
        super().__init__()
        self.boss = self.staff_login("boss", "R13")            # the clinic administrator
        self.ops = self.staff_login("ops", "R04")

    def staff_login(self, login: str, *roles: str) -> str:
        actor = self.person(*roles)
        with tenant_transaction(self.ctx):
            trust.set_password(actor, login=login, password=PASSWORD, by="system")
        return actor


@pytest.fixture
def site():
    return Site()


def call(method: str, path: str, token: str | None = None, **body: Any):
    extra: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    if method == "GET":
        return Client().get(f"/api/v1/{path}", **extra)
    return Client().post(f"/api/v1/{path}", json.dumps(body), content_type="application/json",
                         **extra)


def sign_in(site: Site, login: str, password: str = PASSWORD):
    return call("POST", "auth/login", organization=site.tenant_name, login=login,
                password=password)


def token_of(site: Site, login: str) -> str:
    r = sign_in(site, login)
    assert r.status_code == 200 and r.json()["token"], r.content
    return r.json()["token"]


def switch_on(site: Site, clock: Clock, login: str = "boss") -> tuple[str, str, list[str]]:
    """Returns (the new session, the secret, the recovery codes)."""
    token = token_of(site, login)
    made = call("POST", "auth/two-step/set-up", token, password=PASSWORD)
    assert made.status_code == 200 and made["Cache-Control"] == "no-store"
    secret = made.json()["secret"]
    done = call("POST", "auth/two-step/confirm", token,
                code=two_step.code_for(secret, clock.at))
    assert done.status_code == 200, done.content
    return done.json()["token"], secret, done.json()["recovery_codes"]


def second_step(site: Site, login: str, code: str):
    first = sign_in(site, login)
    assert first.status_code == 200 and first.json()["second_step"] is True
    return call("POST", "auth/login/second-step", ticket=first.json()["ticket"], code=code)


def me(token: str) -> int:
    return call("GET", "me", token).status_code


# ------------------------------------------------------------------ switching it on
def test_switching_it_on_needs_the_password_and_a_first_code(site, clock):
    token = token_of(site, "boss")
    wrong = call("POST", "auth/two-step/set-up", token, password="Not-The-Password-1")
    assert wrong.status_code == 422 and wrong.json()["fields"] == ["password"]
    made = call("POST", "auth/two-step/set-up", token, password=PASSWORD).json()
    assert made["link"].startswith("otpauth://totp/Zoiko%20Access%3Aboss?secret=")
    bad = call("POST", "auth/two-step/confirm", token, code="000000")
    assert bad.status_code == 422 and bad.json()["fields"] == ["code"]
    with tenant_transaction(site.ctx):
        assert trust.two_step_status(site.boss)["on"] is False       # not on until a code works
    done = call("POST", "auth/two-step/confirm", token,
                code=two_step.code_for(made["secret"], clock.at))
    codes = done.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert me(token) == 401 and me(done.json()["token"]) == 200     # every other session ended
    with tenant_transaction(site.ctx):
        state = trust.two_step_status(site.boss)
    assert state["on"] is True and state["recovery_codes_left"] == 10


def test_the_secret_is_stored_encrypted_and_never_audited(site, clock):
    _, secret, codes = switch_on(site, clock)
    with tenant_transaction(site.ctx):
        cred = Credential.objects.get(actor__public_id=site.boss)
        stored = json.dumps([cred.totp_secret_enc, cred.recovery_code_hashes])
        audit = json.dumps(list(AuditEvent.objects.values("details", "reason_code")), default=str)
    assert secret not in stored and codes[0].replace("-", "") not in stored
    assert secret not in audit and codes[0] not in audit


def test_it_cannot_be_set_up_twice(site, clock):
    token, _, _ = switch_on(site, clock)
    assert call("POST", "auth/two-step/set-up", token, password=PASSWORD).status_code == 409


# ------------------------------------------------------------------ signing in with it
def test_the_password_alone_gives_no_session_only_a_ticket(site, clock):
    switch_on(site, clock)
    r = sign_in(site, "boss")
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    body = r.json()
    assert body["second_step"] is True and "token" not in body and "roles" not in body
    assert me(body["ticket"]) == 401                                # a ticket is not a session


def test_the_right_code_signs_in_and_cannot_be_used_twice(site, clock):
    _, secret, _ = switch_on(site, clock)
    clock.wait(30)
    code = two_step.code_for(secret, clock.at)
    r = second_step(site, "boss", code)
    assert r.status_code == 200 and me(r.json()["token"]) == 200
    assert r.json()["home"] == "operations"
    assert second_step(site, "boss", code).status_code == 401       # seen over a shoulder: no


def test_a_code_from_long_ago_or_far_ahead_is_refused(site, clock):
    _, secret, _ = switch_on(site, clock)
    clock.wait(300)
    assert second_step(site, "boss", two_step.code_for(
        secret, clock.at - dt.timedelta(minutes=2))).status_code == 401
    assert second_step(site, "boss", two_step.code_for(
        secret, clock.at + dt.timedelta(minutes=2))).status_code == 401


def test_a_right_password_does_not_reset_the_count_of_wrong_codes(site, clock):
    """Otherwise a stolen password would buy endless guesses: 4 wrong, sign in again, 1 wrong."""
    _, secret, _ = switch_on(site, clock)
    for _ in range(4):
        assert second_step(site, "boss", "111111").status_code == 401
    assert second_step(site, "boss", "222222").status_code == 401   # the 5th: locked now
    clock.wait(30)
    # Locked: even the right password AND code are refused for 15 minutes.
    assert sign_in(site, "boss").status_code == 401


def test_a_forged_or_expired_ticket_is_refused(site, clock):
    _, secret, _ = switch_on(site, clock)
    ticket = sign_in(site, "boss").json()["ticket"]
    clock.wait(30)
    code = two_step.code_for(secret, clock.at)
    head, body, sig = ticket.split(".")
    assert call("POST", "auth/login/second-step", ticket=f"{head}.{body}.x{sig[1:]}",
                code=code).status_code == 401
    token = token_of(site, "ops")                                   # a session is not a ticket
    assert call("POST", "auth/login/second-step", ticket=token, code=code).status_code == 401
    clock.wait(6 * 60)
    assert call("POST", "auth/login/second-step", ticket=ticket,
                code=two_step.code_for(secret, clock.at)).status_code == 401


def test_a_ticket_dies_when_the_person_is_signed_out_everywhere(site, clock):
    token, secret, _ = switch_on(site, clock)
    ticket = sign_in(site, "boss").json()["ticket"]
    call("POST", "auth/logout", token)
    clock.wait(30)
    assert call("POST", "auth/login/second-step", ticket=ticket,
                code=two_step.code_for(secret, clock.at)).status_code == 401


def test_a_recovery_code_works_once(site, clock):
    _, _, codes = switch_on(site, clock)
    assert second_step(site, "boss", codes[3].lower()).status_code == 200   # any case, any dash
    assert second_step(site, "boss", codes[3]).status_code == 401
    with tenant_transaction(site.ctx):
        assert trust.two_step_status(site.boss)["recovery_codes_left"] == 9


def test_new_recovery_codes_replace_the_old_ones(site, clock):
    token, secret, old = switch_on(site, clock)
    assert call("POST", "auth/two-step/recovery-codes", token, code="123456").status_code == 422
    clock.wait(30)
    r = call("POST", "auth/two-step/recovery-codes", token,
             code=two_step.code_for(secret, clock.at))
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    assert second_step(site, "boss", old[0]).status_code == 401
    assert second_step(site, "boss", new[0]).status_code == 200


def test_signing_in_with_it_is_audited(site, clock):
    _, secret, _ = switch_on(site, clock)
    clock.wait(30)
    second_step(site, "boss", two_step.code_for(secret, clock.at))
    second_step(site, "boss", "999999")
    with tenant_transaction(site.ctx):
        rows = set(AuditEvent.objects.filter(action="auth.login_second_step")
                   .values_list("outcome", "reason_code"))
    assert ("allowed", "app") in rows and ("denied", "bad_code") in rows


# ------------------------------------------------------------------ switching it off
def test_switching_it_off_needs_the_password_and_a_code(site, clock):
    token, secret, _ = switch_on(site, clock)
    clock.wait(30)
    code = two_step.code_for(secret, clock.at)
    assert call("POST", "auth/two-step/off", token, password=PASSWORD,
                code="000000").status_code == 422
    assert call("POST", "auth/two-step/off", token, password="Not-The-Password-1",
                code=code).status_code == 422
    r = call("POST", "auth/two-step/off", token, password=PASSWORD, code=code)
    assert r.status_code == 200 and me(token) == 401 and me(r.json()["token"]) == 200
    assert sign_in(site, "boss").json()["token"]                    # the password alone again


# ------------------------------------------------------------------ the customer's rule
def test_the_administrator_must_have_it_before_requiring_it(site, clock):
    token = token_of(site, "boss")
    assert call("POST", "staff/two-step-rule", token, required=True).status_code == 409
    token, _, _ = switch_on(site, clock)
    r = call("POST", "staff/two-step-rule", token, required=True)
    assert r.status_code == 200 and r.json()["required"] is True
    assert r.json()["staff_without_two_step"] == 1                  # "ops"


def test_only_the_administrator_sets_the_rule(site, clock):
    token = token_of(site, "ops")
    assert call("GET", "staff/two-step-rule", token).json()["can_set"] is False
    assert call("POST", "staff/two-step-rule", token, required=False).status_code == 403


def test_while_required_a_staff_login_can_only_set_it_up(site, clock):
    boss, _, _ = switch_on(site, clock)
    call("POST", "staff/two-step-rule", boss, required=True)
    r = sign_in(site, "ops")
    assert r.status_code == 200 and r.json()["must_set_up_two_step"] is True
    token = r.json()["token"]
    assert me(token) == 200                                        # who am I: yes
    stuck = call("GET", "staff/directory", token)
    assert stuck.status_code == 401
    assert stuck.json()["code"] == "STEP_UP_REQUIRED"
    assert stuck.json()["fields"] == ["two_step_set_up"]
    made = call("POST", "auth/two-step/set-up", token, password=PASSWORD).json()
    done = call("POST", "auth/two-step/confirm", token,
                code=two_step.code_for(made["secret"], clock.at)).json()
    assert call("GET", "staff/directory", done["token"]).status_code == 200


def test_while_required_staff_cannot_switch_theirs_off(site, clock):
    boss, secret, _ = switch_on(site, clock)
    call("POST", "staff/two-step-rule", boss, required=True)
    clock.wait(30)
    r = call("POST", "auth/two-step/off", boss, password=PASSWORD,
             code=two_step.code_for(secret, clock.at))
    assert r.status_code == 403 and r.json()["fields"] == ["required"]


def test_the_rule_never_applies_to_patients(site, clock):
    site_ctx = site.ctx
    with tenant_transaction(site_ctx):
        trust.set_self_registration(True, by="system")
    boss, _, _ = switch_on(site, clock)
    call("POST", "staff/two-step-rule", boss, required=True)
    r = call("POST", "auth/register", organization=site.tenant_name,
             login="pat@example.org", password=PASSWORD)
    assert r.status_code == 201 and r.json()["must_set_up_two_step"] is False
    assert call("GET", "bookings", r.json()["token"]).status_code == 200


# ------------------------------------------------------------------ the re-check before risky acts
def test_a_risky_act_asks_for_the_code_again_after_fifteen_minutes(site, clock):
    token, secret, _ = switch_on(site, clock)
    call("POST", "staff/two-step-rule", token, required=True)
    clock.wait(16 * 60)
    stale = call("POST", "staff/two-step-rule", token, required=False)
    assert stale.status_code == 401 and stale.json()["fields"] == ["two_step"]
    assert call("POST", "auth/two-step/recheck", token, code="000000").status_code == 422
    fresh = call("POST", "auth/two-step/recheck", token,
                 code=two_step.code_for(secret, clock.at)).json()["token"]
    r = call("POST", "staff/two-step-rule", fresh, required=False)
    assert r.status_code == 200 and r.json()["required"] is False


def test_inviting_staff_is_a_risky_act(site, clock):
    token, _, _ = switch_on(site, clock)
    clock.wait(16 * 60)
    r = call("POST", "staff/members", token, email="new@example.org", role="R04")
    assert r.status_code == 401 and r.json()["code"] == "STEP_UP_REQUIRED"


def test_logins_without_two_step_are_not_rechecked(site, clock):
    token = token_of(site, "boss")
    clock.wait(16 * 60)
    r = call("POST", "staff/members", token, email="new@example.org", role="R04")
    assert r.status_code == 201, r.content


def test_administrator_can_reset_a_staff_members_lost_two_step(site, clock):
    boss, _, _ = switch_on(site, clock, login="boss")
    ops_tok, _, _ = switch_on(site, clock, login="ops")
    ops_id = call("GET", "me", ops_tok).json()["actor"]
    staff = call("GET", "staff/directory", boss).json()["staff"]
    assert next(s for s in staff if s["id"] == ops_id)["two_step_on"] is True
    assert call("POST", f"staff/members/{ops_id}/reset-two-step", ops_tok).status_code == 403
    boss_id = call("GET", "me", boss).json()["actor"]
    assert call("POST", f"staff/members/{boss_id}/reset-two-step", boss).status_code == 403
    res = call("POST", f"staff/members/{ops_id}/reset-two-step", boss)
    assert res.status_code == 200 and res.json()["member"]["two_step_on"] is False
    assert me(ops_tok) == 401
    assert sign_in(site, "ops").json()["token"]
