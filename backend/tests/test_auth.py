import datetime as dt
import json

import pytest
from django.test import Client

from api.support import home_for
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import FixedClock, utc_now
from modules.evidence.api import verify_chain
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust import auth
from modules.trust.models import Credential
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    """Prove real logins work with the dev stub OFF."""
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False


class Site(World):
    """A tenant where people have logins."""

    def user(self, login, *roles, kind=trust.ActorType.FACILITY_USER, password=PASSWORD):
        with tenant_transaction(self.ctx):
            actor = trust.register_actor(actor_type=kind, issuer="local", subject=login)
            for role in roles:
                trust.assign_role(actor_public_id=actor.public_id,
                                  organization_public_id=self.org, role_code=role, by="system")
            trust.set_password(actor.public_id, login=login, password=password, by="system")
        return actor.public_id

    def sign_in(self, login, password=PASSWORD, organization=None, clock=None):
        return trust.login(tenant_name=organization or self.tenant_name, login=login,
                           password=password, clock=clock)


@pytest.fixture
def site():
    return Site()


def post_login(client, **body):
    return client.post("/api/v1/auth/login", json.dumps(body), content_type="application/json")


def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def code_of(excinfo):
    return excinfo.value.code


# ------------------------------------------------------------------ passwords
def test_passwords_are_stored_only_as_salted_hashes():
    one, two = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert one != two and PASSWORD not in one and one.startswith("scrypt$")
    assert auth.verify_password(one, PASSWORD) and not auth.verify_password(one, PASSWORD + "x")
    for junk in ("", "plain", "scrypt$1$2", "bcrypt$x$y$z$a$b", "scrypt$a$b$c$d$e"):
        assert auth.verify_password(junk, PASSWORD) is False


@pytest.mark.parametrize("bad", ["short", "x" * 129, "aaaaaaaaaaaa", "asha.patient"])
def test_weak_passwords_are_refused(site, bad):
    actor = site.user("someone", "R04")
    with tenant_transaction(site.ctx), pytest.raises(ZoikoError) as e:
        trust.set_password(actor, login="asha.patient", password=bad, by="system")
    assert code_of(e) == ErrorCode.VALIDATION_FAILED


def test_two_people_cannot_share_a_login_name(site):
    site.user("shared", "R04")
    other = site.person("R04")
    with tenant_transaction(site.ctx), pytest.raises(ZoikoError) as e:
        trust.set_password(other, login="Shared", password=PASSWORD, by="system")
    assert code_of(e) == ErrorCode.CONFLICT_VERSION


def test_the_same_login_name_is_fine_in_different_organizations():
    a, b = Site(), Site()
    a.user("priya", "R04")
    b.user("priya", "R04")
    assert a.sign_in("priya").tenant_public_id != b.sign_in("priya").tenant_public_id


# ------------------------------------------------------------------ signing in
def test_login_returns_a_token_that_works_without_the_dev_stub(site):
    actor = site.user("ops", "R04")
    client = Client()
    r = post_login(client, organization=site.tenant_name, login="ops", password=PASSWORD)
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    body = r.json()
    assert body["home"] == "operations" and body["actor"]["id"] == actor
    assert body["roles"][0]["role"] == "R04" and body["tenant"]["name"] == site.tenant_name
    me = client.get("/api/v1/me", **bearer(body["token"]))
    assert me.status_code == 200 and me.json()["actor"] == actor
    assert me.json()["home"] == "operations"
    dev_token = f"dev:{body['tenant']['id']}:local:ops"
    dev = client.get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {dev_token}")
    assert dev.status_code == 401                    # the unsigned dev token is off


def test_login_name_is_case_and_space_insensitive_and_organization_too(site):
    site.user("Asha.Patient", "R01", kind=trust.ActorType.PATIENT)
    got = site.sign_in("  ASHA.patient ", organization=site.tenant_name.upper())
    assert got.login == "asha.patient"


@pytest.mark.parametrize(("kind", "roles", "home"), [
    (trust.ActorType.PATIENT, ["R01"], "patient"),
    (trust.ActorType.PROXY, ["R02"], "patient"),
    (trust.ActorType.FACILITY_USER, ["R04"], "operations"),
    (trust.ActorType.SUPPORT, ["R11"], "operations"),
    (trust.ActorType.CLINICIAN, ["R03"], "clinician"),
    (trust.ActorType.PAYER_USER, ["R07"], "payer"),
    (trust.ActorType.EMPLOYER_USER, ["R08"], "employer"),
    (trust.ActorType.WORKLOAD, ["R05"], "partner"),
    (trust.ActorType.FACILITY_USER, [], "none"),
])
def test_each_kind_of_person_is_sent_to_their_own_area(site, kind, roles, home):
    site.user("someone", *roles, kind=kind)
    r = post_login(Client(), organization=site.tenant_name, login="someone", password=PASSWORD)
    assert r.json()["home"] == home
    assert home_for(kind.value, roles) == home


def test_every_failure_looks_exactly_the_same(site):
    site.user("ok", "R04")
    suspended = site.user("sleeper", "R04")
    with tenant_transaction(site.ctx):
        trust.set_actor_status(suspended, trust.Status.SUSPENDED, by="system")
    client = Client()
    attempts = [
        {"organization": site.tenant_name, "login": "ok", "password": "wrong-password-1"},
        {"organization": site.tenant_name, "login": "nobody", "password": PASSWORD},
        {"organization": "No Such Organization", "login": "ok", "password": PASSWORD},
        {"organization": site.tenant_name, "login": "sleeper", "password": PASSWORD},
    ]
    seen = []
    for body in attempts:
        r = post_login(client, **body)
        assert r.status_code == 401
        data = r.json()
        data.pop("request_id")
        seen.append(data)
    assert all(item == seen[0] for item in seen) and seen[0]["code"] == "UNAUTHENTICATED"


def test_unknown_logins_cost_the_same_hashing_work(site, monkeypatch):
    site.user("real", "R04")
    calls = []
    original = auth.verify_password
    monkeypatch.setattr(auth, "verify_password",
                        lambda stored, pw: calls.append(1) or original(stored, pw))
    for tenant, login in ((site.tenant_name, "ghost"), ("Nowhere", "real")):
        calls.clear()
        with pytest.raises(ZoikoError):
            site.sign_in(login, organization=tenant)
        assert calls == [1]                      # one hash check even for a login that is not there


def test_blank_or_malformed_requests_are_refused_cleanly(site):
    client = Client()
    assert client.get("/api/v1/auth/login").status_code == 405
    assert post_login(client, organization=site.tenant_name, login="x").status_code == 422
    assert post_login(client, organization="", login="x", password="y").status_code == 422
    assert post_login(client, organization=site.tenant_name, login="x", password=5
                      ).status_code == 422
    raw = client.post("/api/v1/auth/login", "{nope", content_type="application/json")
    assert raw.status_code == 422


# ------------------------------------------------------------------ lockout
def test_five_wrong_passwords_lock_the_login_even_for_the_right_password(site):
    site.user("target", "R04")
    for _ in range(auth.MAX_FAILURES):
        with pytest.raises(ZoikoError):
            site.sign_in("target", password="wrong-wrong-1")
    with pytest.raises(ZoikoError):                      # now even the right password fails
        site.sign_in("target")
    with tenant_transaction(site.ctx):
        cred = Credential.objects.get(login="target")
        assert cred.locked_until is not None and cred.locked_until > utc_now()
    later = FixedClock(utc_now() + auth.LOCK_FOR + dt.timedelta(minutes=1))
    assert site.sign_in("target", clock=later).login == "target"       # lock expired


def test_a_good_login_resets_the_failure_count(site):
    site.user("careful", "R04")
    for _ in range(auth.MAX_FAILURES - 1):
        with pytest.raises(ZoikoError):
            site.sign_in("careful", password="wrong-wrong-1")
    site.sign_in("careful")
    with pytest.raises(ZoikoError):
        site.sign_in("careful", password="wrong-wrong-1")
    site.sign_in("careful")                              # not locked: the count started over
    with tenant_transaction(site.ctx):
        assert Credential.objects.get(login="careful").failed_attempts == 0


# ------------------------------------------------------------------ tokens
def test_tampered_expired_and_foreign_tokens_are_refused(site, settings):
    site.user("ops", "R04")
    token = site.sign_in("ops").token
    client = Client()
    assert client.get("/api/v1/me", **bearer(token)).status_code == 200
    head, body, sig = token.split(".")
    flipped = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    forged_body = body[:-2] + ("AA" if body[-2:] != "AA" else "BB")
    for bad in (f"{head}.{body}.{flipped}", f"{head}.{forged_body}.{sig}", f"zt2.{body}.{sig}",
                f"{head}.{body}", "zt1.x.y", "zt1.."):
        assert client.get("/api/v1/me", **bearer(bad)).status_code == 401
    with pytest.raises(ZoikoError):
        auth.read_token(token, now=utc_now() + auth.TOKEN_LIFETIME + dt.timedelta(seconds=5))
    settings.SECRET_KEY = "a-different-secret-key-entirely"
    assert client.get("/api/v1/me", **bearer(token)).status_code == 401   # signed by another key


def test_a_token_stops_working_when_the_person_or_organization_is_switched_off(site):
    actor = site.user("ops", "R04")
    token = site.sign_in("ops").token
    client = Client()
    with tenant_transaction(site.ctx):
        trust.set_actor_status(actor, trust.Status.SUSPENDED, by="system")
    assert client.get("/api/v1/me", **bearer(token)).status_code == 401
    with tenant_transaction(site.ctx):
        trust.set_actor_status(actor, trust.Status.ACTIVE, by="system")
    assert client.get("/api/v1/me", **bearer(token)).status_code == 200
    from modules.trust.models import Tenant
    Tenant.objects.filter(name=site.tenant_name).update(status="suspended")
    assert client.get("/api/v1/me", **bearer(token)).status_code == 401


def test_signing_out_kills_every_token_but_a_new_login_works(site):
    site.user("ops", "R04")
    first, second = site.sign_in("ops").token, site.sign_in("ops").token
    client = Client()
    assert client.post("/api/v1/auth/logout", **bearer(first)).status_code == 200
    for token in (first, second):                        # signed out EVERYWHERE
        assert client.get("/api/v1/me", **bearer(token)).status_code == 401
    assert client.get("/api/v1/me", **bearer(site.sign_in("ops").token)).status_code == 200


def test_changing_a_password_signs_the_person_out_and_the_old_password_stops(site):
    actor = site.user("ops", "R04")
    token = site.sign_in("ops").token
    with tenant_transaction(site.ctx):
        trust.set_password(actor, login="ops", password="Another-Passw0rd!", by="system")
    assert Client().get("/api/v1/me", **bearer(token)).status_code == 401
    with pytest.raises(ZoikoError):
        site.sign_in("ops")
    assert site.sign_in("ops", password="Another-Passw0rd!").login == "ops"


def test_a_token_from_one_organization_never_opens_another(site):
    other = Site()
    site.user("ops", "R04")
    token = site.sign_in("ops").token
    head, body, _ = token.split(".")
    import base64
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["t"] = other.tenant_public_id                 # re-pointing it would need the key
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert Client().get("/api/v1/me", **bearer(f"{head}.{forged}.{token.split('.')[2]}")
                        ).status_code == 401


# ------------------------------------------------------------------ records
def test_logins_are_audited_and_the_log_never_holds_secrets(site):
    site.user("ops", "R04")
    token = site.sign_in("ops").token
    with pytest.raises(ZoikoError):
        site.sign_in("ops", password="wrong-wrong-1")
    with tenant_transaction(site.ctx):
        rows = list(AuditEvent.objects.filter(action="auth.login").order_by("chain_seq"))
        assert [(r.outcome, r.reason_code) for r in rows] == [("allowed", ""),
                                                              ("denied", "bad_password")]
        blob = json.dumps([[r.details, r.object_ref, r.actor_ref, r.reason_code]
                           for r in AuditEvent.objects.all()])
        assert PASSWORD not in blob and token not in blob and "wrong-wrong-1" not in blob
        assert {"auth.set_password", "auth.login"} <= set(
            AuditEvent.objects.values_list("action", flat=True))
        assert verify_chain().ok


def test_the_stored_credential_holds_no_plaintext(site):
    site.user("ops", "R04")
    with tenant_transaction(site.ctx):
        cred = Credential.objects.get(login="ops")
        assert PASSWORD not in cred.password_hash and cred.session_epoch == 1


def test_the_old_dev_token_still_needs_the_dev_flags(site, settings):
    site.user("ops", "R04")
    header = {"HTTP_AUTHORIZATION": f"Bearer dev:{site.tenant_public_id}:local:ops"}
    assert Client().get("/api/v1/me", **header).status_code == 401
    settings.DEBUG, settings.DEV_AUTH_STUB = True, True
    assert Client().get("/api/v1/me", **header).status_code == 200


# ------------------------------------------------------------------ self-registration
def open_registration(site, enabled=True):
    with tenant_transaction(site.ctx):
        trust.set_self_registration(enabled, by="system")


def register(client, site, login="New.Patient@Example.org", password=PASSWORD, organization=None):
    return client.post(
        "/api/v1/auth/register",
        json.dumps({"organization": organization or site.tenant_name, "login": login,
                    "password": password}),
        content_type="application/json")


def test_registration_is_off_unless_the_organization_turns_it_on(site):
    client = Client()
    r = register(client, site)
    assert r.status_code == 403 and r.json()["code"] == "POLICY_DENIED"
    assert register(client, site, organization="No Such Place").status_code == 403
    with tenant_transaction(site.ctx):
        from modules.trust.models import Actor
        assert Actor.objects.filter(subject="new.patient@example.org").count() == 0


def test_a_patient_can_create_an_account_and_is_signed_in_as_a_patient(site):
    open_registration(site)
    client = Client()
    r = register(client, site)
    assert r.status_code == 201 and r["Cache-Control"] == "no-store"
    body = r.json()
    assert body["home"] == "patient" and body["actor"]["login"] == "new.patient@example.org"
    assert [x["role"] for x in body["roles"]] == ["R01"]
    me = client.get("/api/v1/me", **bearer(body["token"])).json()
    assert me["home"] == "patient" and len(me["persons"]) == 1
    assert site.sign_in("new.patient@example.org").actor_type == "patient"   # can sign in again
    with tenant_transaction(site.ctx):
        from modules.trust.models import Person
        person = Person.objects.get(public_id=me["persons"][0])
        assert person.identity_status == "unverified"          # nothing about them is verified
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        assert {"auth.register", "person.create", "auth.set_password"} <= actions
        assert verify_chain().ok


def test_registering_never_takes_over_an_existing_account(site):
    site.user("ops@north.example", "R04")
    open_registration(site)
    client = Client()
    for login in ("ops@north.example", "OPS@north.example"):
        r = register(client, site, login=login, password="Attacker-Passw0rd!")
        assert r.status_code == 409
    assert site.sign_in("ops@north.example").login == "ops@north.example"   # old password intact
    with pytest.raises(ZoikoError):
        site.sign_in("ops@north.example", password="Attacker-Passw0rd!")


def test_a_failed_registration_leaves_nothing_behind(site):
    open_registration(site)
    client = Client()
    for body in ({"login": "a@b.org", "password": "short"},
                 {"login": "not-an-email", "password": PASSWORD},
                 {"login": "x" * 200 + "@b.org", "password": PASSWORD},
                 {"login": "a@b.org", "password": "a@b.org"}):
        r = client.post("/api/v1/auth/register",
                        json.dumps({"organization": site.tenant_name, **body}),
                        content_type="application/json")
        assert r.status_code == 422
    with tenant_transaction(site.ctx):
        from modules.trust.models import Actor, Person
        assert Actor.objects.filter(actor_type="patient").count() == 0
        assert Person.objects.count() == 0 and Credential.objects.count() == 0


def test_registrations_are_capped_per_organization(site, monkeypatch):
    open_registration(site)
    monkeypatch.setattr(auth, "REGISTER_MAX", 2)
    client = Client()
    assert register(client, site, login="one@example.org").status_code == 201
    assert register(client, site, login="two@example.org").status_code == 201
    third = register(client, site, login="three@example.org")
    assert third.status_code == 429 and third.json()["code"] == "RATE_LIMITED"


def test_the_sign_in_options_list_only_what_is_really_set_up(site, settings):
    client = Client()
    assert client.get("/api/v1/auth/providers").json() == {
        "google": {"enabled": False, "client_id": None}}
    settings.GOOGLE_CLIENT_ID = "example-client-id.apps.googleusercontent.com"
    assert client.get("/api/v1/auth/providers").json()["google"]["enabled"] is True
    assert client.post("/api/v1/auth/providers").status_code == 405


def test_the_minimum_password_length_is_a_setting_that_defaults_to_ten(site, settings):
    actor = site.user("someone", "R04")
    with tenant_transaction(site.ctx):
        with pytest.raises(ZoikoError):                       # 8 characters: too short by default
            trust.set_password(actor, login="someone", password="demo@123", by="system")
        settings.PASSWORD_MIN_LENGTH = 8                      # what the dev Docker setup does
        trust.set_password(actor, login="someone", password="demo@123", by="system")
        with pytest.raises(ZoikoError):                       # still not shorter than 8, or weak
            trust.set_password(actor, login="someone", password="demo@12", by="system")
        with pytest.raises(ZoikoError):
            trust.set_password(actor, login="someone", password="aaaaaaaa", by="system")
