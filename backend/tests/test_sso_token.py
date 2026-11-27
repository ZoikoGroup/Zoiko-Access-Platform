"""Checking a provider's ID token (modules/trust/sso.verify_id_token): every check must be able to
refuse, and a good token must pass. No database is needed."""
import datetime as dt
import hashlib

import pytest

from modules.trust import sso
from tests import sso_support as s

NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.UTC)
NONCE = "a-nonce-value"
NONCE_HASH = hashlib.sha256(NONCE.encode()).hexdigest()


@pytest.fixture
def idp(monkeypatch):
    fake = s.Idp()
    monkeypatch.setattr(sso, "http_json", fake.http_json)
    sso.reset_caches()
    yield fake
    sso.reset_caches()


def check(token, provider=None):
    return sso.verify_id_token(token, provider or s.provider(), nonce_hash=NONCE_HASH, now=NOW)


def good(idp, **over):
    return idp.token(NONCE, now=NOW, **over)


# ------------------------------------------------------------------ a good token
def test_a_correct_token_passes_and_returns_its_claims(idp):
    claims = check(good(idp))
    assert claims["sub"] == "subject-123" and claims["email"] == "asha@example.org"


def test_an_audience_list_naming_us_with_the_right_authorized_party_passes(idp):
    claims = check(good(idp, aud=[s.CLIENT_ID, "another"], azp=s.CLIENT_ID))
    assert claims["sub"] == "subject-123"


def test_googles_second_issuer_spelling_is_accepted_for_google_only(idp):
    google = sso.Provider(
        key="google", name="Google", issuer="https://accounts.google.com", client_id=s.CLIENT_ID,
        client_secret="x", authorization_endpoint="https://a.example/", token_endpoint="https://t/",
        jwks_uri=f"{s.ISSUER}/keys")
    assert check(good(idp, iss="accounts.google.com"), google)["sub"]
    with pytest.raises(sso.SsoError):
        check(good(idp, iss="accounts.google.com"))             # not for another provider


# ------------------------------------------------------------------ each check refuses
def refused(token):
    with pytest.raises(sso.SsoError):
        check(token)


def test_a_token_signed_by_someone_else_is_refused(idp):
    refused(idp.token(NONCE, now=NOW, key=s.new_key()))


def test_a_token_with_a_changed_body_is_refused(idp):
    head, body, signature = good(idp).split(".")
    other = s.b64(b'{"iss":"%s","aud":"%s","sub":"evil","nonce":"%s","exp":9999999999}' % (
        s.ISSUER.encode(), s.CLIENT_ID.encode(), NONCE.encode()))
    refused(f"{head}.{other}.{signature}")


@pytest.mark.parametrize("alg", ["none", "HS256", "ES256", "RS512", ""])
def test_only_rs256_is_accepted_never_none_or_a_shared_secret(idp, alg):
    refused(idp.token(NONCE, now=NOW, header={"alg": alg}))


def test_an_unsigned_token_is_refused(idp):
    head = s.b64(b'{"alg":"none"}')
    body = s.b64(b'{"iss":"%s","aud":"%s","sub":"x","nonce":"%s","exp":9999999999}' % (
        s.ISSUER.encode(), s.CLIENT_ID.encode(), NONCE.encode()))
    refused(f"{head}.{body}.")


def test_the_wrong_issuer_is_refused(idp):
    refused(good(idp, iss="https://evil.example.org"))


@pytest.mark.parametrize("aud", ["someone-else", ["a", "b"], None, 5])
def test_a_token_for_another_audience_is_refused(idp, aud):
    refused(good(idp, aud=aud))


def test_several_audiences_need_the_authorized_party_to_be_us(idp):
    refused(good(idp, aud=[s.CLIENT_ID, "another"]))
    refused(good(idp, aud=[s.CLIENT_ID, "another"], azp="another"))


def test_an_expired_token_is_refused_but_a_little_clock_difference_is_not(idp):
    refused(good(idp, exp=int((NOW - dt.timedelta(minutes=5)).timestamp())))
    assert check(good(idp, exp=int((NOW - dt.timedelta(seconds=30)).timestamp())))["sub"]


def test_a_token_from_the_future_is_refused(idp):
    refused(good(idp, iat=int((NOW + dt.timedelta(minutes=10)).timestamp())))


@pytest.mark.parametrize("over", [{"nonce": "another-nonce"}, {"nonce": None}, {"nonce": 5}])
def test_a_wrong_or_missing_nonce_is_refused(idp, over):
    refused(good(idp, **over))


@pytest.mark.parametrize("over", [{"sub": None}, {"sub": ""}, {"sub": 12}])
def test_a_token_without_a_usable_subject_is_refused(idp, over):
    refused(good(idp, **over))


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "not a token", "...", "e30.e30.e30"])
def test_garbage_is_refused_without_an_error_escaping(idp, token):
    refused(token)


def test_a_missing_expiry_is_refused(idp):
    refused(good(idp, exp=None))


# ------------------------------------------------------------------ the provider's keys
def test_a_rotated_provider_key_is_fetched_again_once(idp):
    assert check(good(idp))["sub"]                              # key set cached with key-1
    idp.kid = "key-2"
    idp.key = s.new_key()                                       # the provider rotated its key
    assert check(good(idp))["sub"]                              # unknown kid: fetched again


def test_a_provider_that_is_down_makes_the_check_fail_cleanly(idp):
    idp.down = True
    refused(good(idp))


def test_a_small_rsa_key_is_refused(idp):
    from cryptography.hazmat.primitives.asymmetric import rsa

    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505
    idp.key = weak
    refused(good(idp))


def test_a_non_rsa_key_in_the_key_set_is_skipped(idp):
    idp.extra_keys = [{"kty": "EC", "kid": "key-1", "crv": "P-256", "x": "AA", "y": "AA"}]
    assert check(good(idp))["sub"]


# ------------------------------------------------------------------ provider configuration
def test_a_provider_needs_every_field_and_https_endpoints(settings):
    import json

    full = {"key": "acme", "name": "Acme", "issuer": s.ISSUER, "client_id": "c",
            "client_secret": "x", "authorization_endpoint": f"{s.ISSUER}/a",
            "token_endpoint": f"{s.ISSUER}/t", "jwks_uri": f"{s.ISSUER}/k"}
    settings.GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_SECRET = ""
    settings.SSO_PROVIDERS = json.dumps([full])
    assert list(sso.providers()) == ["acme"]
    for missing in ("client_id", "client_secret", "key"):
        settings.SSO_PROVIDERS = json.dumps([{**full, missing: ""}])
        assert sso.providers() == {}
    settings.SSO_PROVIDERS = json.dumps([{**full, "token_endpoint": "http://plain.example/t"}])
    assert sso.providers() == {}                                 # plain http is refused
    settings.SSO_ALLOW_INSECURE = True
    assert list(sso.providers()) == ["acme"]                     # unless a developer allows it
    settings.SSO_PROVIDERS = "not json"
    assert sso.providers() == {}


def test_google_is_offered_only_with_both_its_id_and_secret(settings):
    settings.SSO_PROVIDERS = ""
    settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET = "id", ""
    assert sso.providers() == {}
    settings.GOOGLE_CLIENT_SECRET = "secret"
    assert list(sso.providers()) == ["google"]
    assert sso.public_providers() == [{"key": "google", "name": "Google"}]
    assert "secret" not in repr(sso.public_providers())


def test_a_provider_is_discovered_from_its_issuer(settings, monkeypatch):
    import json

    sso.reset_caches()
    found = {"issuer": s.ISSUER, "authorization_endpoint": f"{s.ISSUER}/a",
             "token_endpoint": f"{s.ISSUER}/t", "jwks_uri": f"{s.ISSUER}/k"}
    asked = []

    def fake(url, **kw):
        asked.append(url)
        return found

    monkeypatch.setattr(sso, "http_json", fake)
    settings.GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_SECRET = ""
    settings.SSO_PROVIDERS = json.dumps([{"key": "acme", "name": "Acme", "issuer": s.ISSUER,
                                          "client_id": "c", "client_secret": "x"}])
    assert sso.providers()["acme"].jwks_uri == f"{s.ISSUER}/k"
    sso.providers()
    assert asked == [f"{s.ISSUER}/.well-known/openid-configuration"]   # cached the second time
    sso.reset_caches()
