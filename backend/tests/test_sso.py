"""Sign in with an outside identity provider, end to end (api/sso.py, modules/trust/sso.py).

A pretend provider (tests/sso_support.py) signs real RS256 tokens, so the whole flow runs: the start
address, the one-time state, the code exchange, the token checks, the account matching, the audit
trail, and what must be refused."""
import datetime as dt
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock, utc_now
from modules.evidence.models import AuditEvent
from modules.trust import sso
from modules.trust.models import Credential, ExternalIdentity, SsoAttempt, Status
from tests import sso_support as s
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"
LOGIN = "asha.rao@example.org"
EMAIL = "asha@example.org"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False
    settings.GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_SECRET = ""
    settings.SSO_REDIRECT_URIS = s.REDIRECT
    settings.SSO_PROVIDERS = json.dumps([{
        "key": "acme", "name": "Acme ID", "issuer": s.ISSUER, "client_id": s.CLIENT_ID,
        "client_secret": s.SECRET, "authorization_endpoint": f"{s.ISSUER}/authorize",
        "token_endpoint": f"{s.ISSUER}/token", "jwks_uri": f"{s.ISSUER}/keys"}])


@pytest.fixture
def idp(monkeypatch):
    fake = s.Idp()
    monkeypatch.setattr(sso, "http_json", fake.http_json)
    sso.reset_caches()
    yield fake
    sso.reset_caches()


@pytest.fixture
def site():
    from modules.trust import api as trust

    world = World()
    with tenant_transaction(world.ctx):
        trust.set_self_registration(True, by="system")
    return world


def post(path, token=None, **body):
    extra: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return Client().post(f"/api/v1/{path}", json.dumps(body), content_type="application/json",
                         **extra)


def sign_up(site, login=LOGIN, email=EMAIL, verified=True):
    assert post("auth/register", organization=site.tenant_name, login=login,
                password=PASSWORD).status_code == 201
    with tenant_transaction(site.ctx):
        Credential.objects.filter(login=login).update(
            email=email, email_verified_at=utc_now() if verified else None)


def begin(site, provider="acme", redirect=s.REDIRECT):
    r = post("auth/sso/start", provider=provider, organization=site.tenant_name,
             redirect_uri=redirect)
    assert r.status_code == 200, r.content
    query = {k: v[0] for k, v in parse_qs(urlsplit(r.json()["url"]).query).items()}
    return r, query


def finish(site, idp, query, *, code="the-code", provider="acme", redirect=s.REDIRECT, **over):
    idp.token_answer = {"id_token": idp.token(query["nonce"], **over)}
    return post("auth/sso/finish", provider=provider, code=code, state=query["state"],
                redirect_uri=redirect)


def sign_in_with_provider(site, idp, **over):
    _, query = begin(site)
    return finish(site, idp, query, **over)


def audit(site, action):
    with tenant_transaction(site.ctx):
        return [(r.outcome, r.reason_code) for r in AuditEvent.objects.filter(action=action)]


def links(site):
    with tenant_transaction(site.ctx):
        return list(ExternalIdentity.objects.values_list("provider", "subject", "email"))


# ------------------------------------------------------------------ what the page may show
def test_the_sign_in_page_is_told_which_providers_exist_and_nothing_secret():
    body = Client().get("/api/v1/auth/providers").json()
    assert body["sso"] == [{"key": "acme", "name": "Acme ID"}]
    assert s.SECRET not in json.dumps(body) and s.CLIENT_ID not in json.dumps(body)


def test_with_no_provider_configured_nothing_is_offered(settings):
    settings.SSO_PROVIDERS = ""
    body = Client().get("/api/v1/auth/providers").json()
    assert body["sso"] == [] and body["google"]["enabled"] is False


def test_google_appears_when_it_is_configured(settings):
    settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET = "gid", "gsecret"
    body = Client().get("/api/v1/auth/providers").json()
    assert {p["key"] for p in body["sso"]} == {"acme", "google"}
    assert body["google"]["enabled"] is True and "gsecret" not in json.dumps(body)


# ------------------------------------------------------------------ starting
def test_start_sends_the_person_to_the_provider_with_a_one_time_state_and_pkce(site):
    response, query = begin(site)
    assert response["Cache-Control"] == "no-store"
    url = response.json()["url"]
    assert url.startswith(f"{s.ISSUER}/authorize?")
    assert query["client_id"] == s.CLIENT_ID and query["redirect_uri"] == s.REDIRECT
    assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid email"
    assert query["state"].startswith(site_public_id(site) + ".")
    assert s.SECRET not in url


def site_public_id(site):
    from modules.trust.models import Tenant

    return Tenant.objects.get(name=site.tenant_name).public_id


def test_each_start_makes_a_fresh_state_and_nonce(site):
    (_, one), (_, two) = begin(site), begin(site)
    assert one["state"] != two["state"] and one["nonce"] != two["nonce"]
    assert one["code_challenge"] != two["code_challenge"]


def test_only_hashes_of_the_state_and_nonce_are_stored_and_the_verifier_is_encrypted(site):
    _, query = begin(site)
    with tenant_transaction(site.ctx):
        row = SsoAttempt.objects.get()
    assert row.state_hash == hashlib.sha256(query["state"].encode()).hexdigest()
    assert row.nonce_hash == hashlib.sha256(query["nonce"].encode()).hexdigest()
    dump = repr(vars(row))
    assert query["state"] not in dump and query["nonce"] not in dump
    assert row.verifier_enc.startswith("zf1:")                   # sealed with the field key


@pytest.mark.parametrize("body", [
    {"provider": "nobody"},
    {"redirect_uri": "https://evil.example.org/"},
    {"organization": "No Such Clinic"},
])
def test_start_refuses_an_unknown_provider_address_or_organization(site, body):
    good = {"provider": "acme", "organization": site.tenant_name, "redirect_uri": s.REDIRECT}
    assert post("auth/sso/start", **{**good, **body}).status_code == 401


def test_start_needs_its_fields_and_the_right_method(site):
    assert post("auth/sso/start", provider="acme").status_code == 422
    assert post("auth/sso/start", provider="acme", organization="",
                redirect_uri=s.REDIRECT).status_code == 422
    assert Client().get("/api/v1/auth/sso/start").status_code == 405
    assert Client().get("/api/v1/auth/sso/finish").status_code == 405


def test_a_provider_that_is_half_configured_is_not_offered_or_startable(site, settings):
    settings.SSO_PROVIDERS = json.dumps([{
        "key": "acme", "issuer": s.ISSUER, "client_id": s.CLIENT_ID, "client_secret": "",
        "authorization_endpoint": f"{s.ISSUER}/a", "token_endpoint": f"{s.ISSUER}/t",
        "jwks_uri": f"{s.ISSUER}/k"}])
    assert Client().get("/api/v1/auth/providers").json()["sso"] == []
    assert post("auth/sso/start", provider="acme", organization=site.tenant_name,
                redirect_uri=s.REDIRECT).status_code == 401


# ------------------------------------------------------------------ the happy path
def test_a_person_with_a_verified_address_signs_in_and_the_link_is_made(site, idp):
    sign_up(site)
    response = sign_in_with_provider(site, idp)
    assert response.status_code == 200, response.content
    body = response.json()
    assert response["Cache-Control"] == "no-store" and body["token"]
    assert body["actor"]["login"] == LOGIN and body["tenant"]["name"] == site.tenant_name
    assert links(site) == [("acme", "subject-123", EMAIL)]
    me = Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {body['token']}")
    assert me.status_code == 200                                  # a real, working session
    assert audit(site, "auth.sso_link") == [("allowed", "acme")]
    assert audit(site, "auth.sso_login") == [("allowed", "acme")]


def test_the_code_is_exchanged_with_the_secret_and_a_matching_pkce_verifier(site, idp):
    import base64

    sign_up(site)
    _, query = begin(site)
    assert finish(site, idp, query).status_code == 200
    (_, form), = [c for c in idp.calls if c[0].endswith("/token")]
    assert form["code"] == "the-code" and form["grant_type"] == "authorization_code"
    assert form["client_secret"] == s.SECRET and form["redirect_uri"] == s.REDIRECT
    digest = hashlib.sha256(form["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == query["code_challenge"]


def test_once_linked_the_next_sign_in_goes_by_the_subject_even_if_the_email_changed(site, idp):
    sign_up(site)
    assert sign_in_with_provider(site, idp).status_code == 200
    with tenant_transaction(site.ctx):
        Credential.objects.filter(login=LOGIN).update(email="someone.else@example.org")
    again = sign_in_with_provider(site, idp, email="renamed@example.org", email_verified=False)
    assert again.status_code == 200
    assert len(links(site)) == 1


def test_signing_in_this_way_does_not_disturb_the_password(site, idp):
    sign_up(site)
    assert sign_in_with_provider(site, idp).status_code == 200
    assert post("auth/login", organization=site.tenant_name, login=LOGIN,
                password=PASSWORD).status_code == 200


def test_matching_the_address_ignores_letter_case(site, idp):
    sign_up(site, email="Asha@Example.ORG")
    assert sign_in_with_provider(site, idp, email="ASHA@example.org").status_code == 200


# ------------------------------------------------------------------ who may NOT sign in
def test_an_address_the_provider_did_not_verify_is_refused_and_links_nothing(site, idp):
    sign_up(site)
    assert sign_in_with_provider(site, idp, email_verified=False).status_code == 401
    assert links(site) == []
    assert ("denied", "acme:email_unverified") in audit(site, "auth.sso_login")


def test_an_account_whose_own_address_is_not_verified_is_not_matched(site, idp):
    sign_up(site, verified=False)
    assert sign_in_with_provider(site, idp).status_code == 401
    assert links(site) == []


def test_nobody_is_created_from_a_provider_saying_so(site, idp):
    assert sign_in_with_provider(site, idp).status_code == 401    # no account exists
    with tenant_transaction(site.ctx):
        assert Credential.objects.count() == 0
    assert links(site) == []
    assert ("denied", "acme:no_account") in audit(site, "auth.sso_login")


def test_two_accounts_sharing_an_address_are_never_guessed_between(site, idp):
    sign_up(site, login="first@example.org")
    sign_up(site, login="second@example.org")
    assert sign_in_with_provider(site, idp).status_code == 401
    assert links(site) == []
    assert ("denied", "acme:ambiguous") in audit(site, "auth.sso_login")


def test_an_inactive_account_cannot_sign_in_by_address_or_by_link(site, idp):
    sign_up(site)
    assert sign_in_with_provider(site, idp).status_code == 200      # linked
    with tenant_transaction(site.ctx):
        cred = Credential.objects.select_related("actor").get(login=LOGIN)
        cred.actor.status = Status.SUSPENDED
        cred.actor.save(update_fields=["status", "updated_at"])
    assert sign_in_with_provider(site, idp).status_code == 401
    assert ("denied", "acme:inactive") in audit(site, "auth.sso_login")


def test_a_link_for_one_tenant_does_not_sign_anyone_in_at_another(site, idp):
    sign_up(site)
    assert sign_in_with_provider(site, idp).status_code == 200
    other = World()
    assert sign_in_with_provider(other, idp).status_code == 401    # same subject, other customer
    with tenant_transaction(other.ctx):      # the test database user skips row-level security
        mine = ExternalIdentity.objects.filter(tenant_id=other.ctx.tenant_id)
        assert mine.count() == 0


def test_a_signed_in_session_is_ended_by_signing_out_everywhere_like_any_other(site, idp):
    sign_up(site)
    token = sign_in_with_provider(site, idp).json()["token"]
    assert post("auth/logout", token).status_code in (200, 204)
    assert Client().get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401


# ---------------------------------------------- the state: once, in time, intact
def test_a_state_can_be_used_only_once(site, idp):
    sign_up(site)
    _, query = begin(site)
    assert finish(site, idp, query).status_code == 200
    assert finish(site, idp, query).status_code == 401
    assert ("denied", "acme:state_used") in audit(site, "auth.sso_login")


def test_a_failed_attempt_still_uses_the_state_up(site, idp):
    sign_up(site)
    _, query = begin(site)
    assert finish(site, idp, query, aud="someone-else").status_code == 401
    assert finish(site, idp, query).status_code == 401              # the good token is too late
    assert audit(site, "auth.sso_login").count(("denied", "acme:state_used")) == 1


def test_a_state_expires_after_ten_minutes(site, idp):
    sign_up(site)
    _, query = begin(site)
    idp.token_answer = {"id_token": idp.token(query["nonce"])}
    late = FixedClock(utc_now() + dt.timedelta(minutes=11))
    from foundation.errors import ZoikoError

    with pytest.raises(ZoikoError) as caught:
        sso.finish(provider_key="acme", code="c", state=query["state"], redirect_uri=s.REDIRECT,
                   clock=late)
    assert caught.value.code.name == "UNAUTHENTICATED"


@pytest.mark.parametrize("change", ["tamper", "other_tenant", "no_dot", "empty", "long"])
def test_a_damaged_or_foreign_state_is_refused(site, idp, change):
    sign_up(site)
    _, query = begin(site)
    state = {
        "tamper": query["state"][:-3] + "abc", "no_dot": "nodothere", "empty": "",
        "other_tenant": "AAAAAAAAAAAAAAAAAAAAAA." + query["state"].split(".", 1)[1],
        "long": query["state"] + "x" * 500}[change]
    idp.token_answer = {"id_token": idp.token(query["nonce"])}
    r = post("auth/sso/finish", provider="acme", code="c", state=state, redirect_uri=s.REDIRECT)
    assert r.status_code in (401, 422)
    assert links(site) == []


def test_finishing_with_another_address_than_the_one_started_with_is_refused(site, idp, settings):
    settings.SSO_REDIRECT_URIS = f"{s.REDIRECT},https://other.example.org/"
    sign_up(site)
    _, query = begin(site)
    assert finish(site, idp, query, redirect="https://other.example.org/").status_code == 401


def test_finishing_with_another_provider_than_the_one_started_with_is_refused(site, idp,
                                                                             settings):
    both = json.loads(settings.SSO_PROVIDERS)
    both.append({**both[0], "key": "other", "name": "Other"})
    settings.SSO_PROVIDERS = json.dumps(both)
    sign_up(site)
    _, query = begin(site)
    assert finish(site, idp, query, provider="other").status_code == 401


def test_old_attempts_are_tidied_away_when_new_ones_start(site):
    begin(site)
    with tenant_transaction(site.ctx):
        SsoAttempt.objects.update(expires_at=utc_now() - dt.timedelta(days=1))
    begin(site)
    with tenant_transaction(site.ctx):
        assert SsoAttempt.objects.count() == 1


# ----------------------------------------------- the provider answer is checked
@pytest.mark.parametrize("over", [
    {"aud": "another-client"}, {"iss": "https://evil.example.org"}, {"nonce": "not-ours"},
    {"exp": 1}, {"sub": None}])
def test_a_token_that_fails_any_check_signs_nobody_in(site, idp, over):
    sign_up(site)
    assert sign_in_with_provider(site, idp, **over).status_code == 401
    assert links(site) == []
    assert ("denied", "acme:bad_token") in audit(site, "auth.sso_login")


def test_a_token_signed_with_the_wrong_key_signs_nobody_in(site, idp):
    sign_up(site)
    assert sign_in_with_provider(site, idp, key=s.new_key()).status_code == 401
    assert links(site) == []


def test_a_provider_that_is_down_or_answers_nonsense_signs_nobody_in(site, idp):
    sign_up(site)
    _, query = begin(site)
    idp.token_answer = None                                        # the token endpoint fails
    r = post("auth/sso/finish", provider="acme", code="c", state=query["state"],
             redirect_uri=s.REDIRECT)
    assert r.status_code == 401
    _, query = begin(site)
    idp.token_answer = {"id_token": 12345}
    assert post("auth/sso/finish", provider="acme", code="c", state=query["state"],
                redirect_uri=s.REDIRECT).status_code == 401
    assert links(site) == []


@pytest.mark.parametrize("body", [{"code": ""}, {"state": ""}, {"provider": "nobody"}])
def test_finish_refuses_missing_or_unknown_inputs(site, idp, body):
    _, query = begin(site)
    good = {"provider": "acme", "code": "c", "state": query["state"], "redirect_uri": s.REDIRECT}
    assert post("auth/sso/finish", **{**good, **body}).status_code in (401, 422)


def test_every_failure_looks_the_same_to_the_caller(site, idp):
    sign_up(site)
    _, a = begin(site)
    one = finish(site, idp, a, aud="x")
    _, b = begin(site)
    two = finish(site, idp, b, email_verified=False, sub="other")
    wrong = post("auth/login", organization=site.tenant_name, login=LOGIN, password="wrong-Pass-1")
    assert one.status_code == two.status_code == wrong.status_code == 401
    assert one.json()["code"] == two.json()["code"] == wrong.json()["code"]


# ---------------------------------------------- nothing secret is kept or logged
def test_no_token_code_or_secret_reaches_the_audit_trail_or_the_database_rows(site, idp):
    sign_up(site)
    _, query = begin(site)
    token = idp.token(query["nonce"])
    idp.token_answer = {"id_token": token}
    assert post("auth/sso/finish", provider="acme", code="the-secret-code", state=query["state"],
                redirect_uri=s.REDIRECT).status_code == 200
    with tenant_transaction(site.ctx):
        dump = json.dumps(
            [[str(v) for v in vars(r).values()] for r in AuditEvent.objects.all()]
            + [[str(v) for v in vars(r).values()] for r in ExternalIdentity.objects.all()]
            + [[str(v) for v in vars(r).values()] for r in SsoAttempt.objects.all()])
    for secret in (token, "the-secret-code", s.SECRET, query["state"], query["nonce"]):
        assert secret not in dump


def test_the_two_new_tables_are_protected_by_row_level_security():
    with connection.cursor() as cursor:
        cursor.execute("SELECT relname, relrowsecurity FROM pg_class "
                       "WHERE relname IN ('trust_externalidentity', 'trust_ssoattempt')")
        assert sorted(cursor.fetchall()) == [("trust_externalidentity", True),
                                             ("trust_ssoattempt", True)]
