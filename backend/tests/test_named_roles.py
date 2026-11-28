"""Decided 27 November 2026: two named people each hold a sealed recovery copy of the master key,
which lives in AWS KMS (DEV-396); production releases are signed by the CTO and the Clinical safety
lead, two different people, and the deploy step refuses anything else (DEV-397)."""
import base64
import datetime as dt
import io
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from foundation import aws, crypto
from tests import test_readiness
from tests.test_packs import call
from tests.test_readiness import attest, make_world, release, show, sign_g3

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
world = test_readiness.world      # everyone named, every other hold settled


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def name(w, title, who, by="sam", **extra):
    return call(w, "POST", "platform/named-roles", {"title": title, "holder": w.named[who],
                                                     **extra}, by)


def named(w, title, who, **extra):
    r = name(w, title, who, **extra)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["named"]


def step(w, made, what, who):
    return call(w, "POST", f"platform/named-roles/{made['public_id']}/{what}", {}, who)


def listing(w, who="sam"):
    r = call(w, "GET", "platform/named-roles", who=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def requirement(rel_now, name_):
    return next(q for q in rel_now["requirements"] if q["requirement"] == name_)


# ------------------------------------------------------------------ naming people
@pytest.fixture
def w():
    return make_world()


def test_a_support_officer_names_someone_and_only_that_person_confirms(w):
    made = named(w, "cto", "cto")
    assert made["status"] == "waiting_for_the_person"
    assert step(w, made, "confirm", "sam").status_code == 403       # not the person named
    r = step(w, made, "confirm", "cto")
    assert r.status_code == 200 and json.loads(r.content)["named"]["status"] == "confirmed"


def test_only_a_support_officer_names_never_themselves_and_only_staff_of_the_right_kind(w):
    assert name(w, "cto", "cto", by="iyer").status_code == 403      # a safety officer
    r = call(w, "POST", "platform/named-roles", {"title": "cto", "holder": w.named["cto"]}, "cto")
    assert r.status_code == 422                                      # never yourself
    assert name(w, "cto", "lead").status_code == 422                 # the CTO is a support officer
    assert name(w, "clinical_safety_lead", "cto").status_code == 422  # the lead a safety officer
    assert name(w, "chairman", "cto").status_code == 422


def test_one_cto_and_one_lead_at_a_time_until_withdrawn(w):
    first = named(w, "cto", "cto")
    assert name(w, "cto", "kh1").status_code == 409
    assert step(w, first, "withdraw", "sam").status_code == 200
    assert name(w, "cto", "kh1").status_code == 201


def test_a_key_holder_says_where_the_copy_is_sealed_and_for_which_key(w):
    assert name(w, "key_recovery", "kh1").status_code == 422          # no envelope code
    made = named(w, "key_recovery", "kh1", envelope_code="safe-a-1")
    assert made["key_id"] == crypto.current_key_id() and made["envelope_code"] == "safe-a-1"
    assert name(w, "key_recovery", "kh1", envelope_code="safe-b-2").status_code == 409


def test_a_person_may_step_down_themselves_and_a_withdrawn_name_cannot_be_confirmed(w):
    made = named(w, "key_recovery", "kh1", envelope_code="safe-a-1")
    assert step(w, made, "withdraw", "iyer").status_code == 403      # not support, not them
    assert step(w, made, "withdraw", "kh1").status_code == 200
    assert step(w, made, "confirm", "kh1").status_code == 409


# ------------------------------------------------------------------ the key's recovery copies
def test_going_live_needs_two_different_confirmed_holders_of_the_current_key(w):
    rel = release(w)
    assert "g3.key_recovery:missing" in show(w, rel)["blockers"]
    one = named(w, "key_recovery", "kh1", envelope_code="safe-a-1")
    two = named(w, "key_recovery", "kh2", envelope_code="safe-b-1")
    step(w, one, "confirm", "kh1")
    assert "g3.key_recovery:missing" in show(w, rel)["blockers"]       # one is not enough
    step(w, two, "confirm", "kh2")
    assert "g3.key_recovery:missing" not in show(w, rel)["blockers"]
    assert listing(w)["key_holders_confirmed"] == 2
    step(w, two, "withdraw", "sam")
    assert "g3.key_recovery:missing" in show(w, rel)["blockers"]


def test_holders_of_an_older_key_do_not_count_after_the_key_changes(world, settings):
    rel = release(world)
    assert "g3.key_recovery:missing" not in show(world, rel)["blockers"]
    new_key = base64.b64encode(b"n" * 32).decode()
    settings.FIELD_KEYS = f"{settings.FIELD_KEYS},k9new:{new_key}"
    settings.FIELD_KEY_ID = "k9new"
    assert "g3.key_recovery:missing" in show(world, rel)["blockers"]


def test_the_list_shows_names_and_codes_never_any_key_material(world):
    body = json.dumps(listing(world))
    assert "safe-a-1" in body
    for raw in crypto._keys().values():
        assert base64.b64encode(raw).decode() not in body


# ------------------------------------------------------------------ who signs a release
def test_only_the_confirmed_cto_and_clinical_lead_sign_their_requirements(world):
    rel = release(world)
    assert attest(world, rel, "cto_signoff", "s1").status_code == 403       # support, not CTO
    assert attest(world, rel, "clinical_lead_signoff", "c1").status_code == 403
    assert attest(world, rel, "cto_signoff", "lead").status_code == 403     # wrong kind
    assert attest(world, rel, "cto_signoff", "cto").status_code == 200
    assert attest(world, rel, "clinical_lead_signoff", "lead").status_code == 200
    now = show(world, rel)
    assert requirement(now, "cto_signoff")["status"] == "signed"


def test_an_unconfirmed_or_withdrawn_signer_cannot_sign(w):
    rel = release(w)
    made = named(w, "cto", "cto")
    assert attest(w, rel, "cto_signoff", "cto").status_code == 403         # not confirmed yet
    step(w, made, "confirm", "cto")
    step(w, made, "withdraw", "cto")
    assert attest(w, rel, "cto_signoff", "cto").status_code == 403


def test_the_cto_cannot_sign_a_release_they_made(world):
    rel = release(world, who="cto")
    assert attest(world, rel, "cto_signoff", "cto").status_code == 403


def test_without_both_signatures_a_release_is_not_ready(world):
    rel = release(world)
    sign_g3(world, rel)
    assert show(world, rel)["decision"] == "g3_ready"
    other = release(world, ref="rel-2026-11-2")
    from tests.test_readiness import G3
    for n, req in enumerate(G3, start=1):
        attest(world, other, req, f"s{n}")
    attest(world, other, "cto_signoff", "cto")
    now = show(world, other)
    assert now["decision"] == "no_go" and "g3.clinical_lead_signoff:missing" in now["blockers"]


def test_either_signer_can_veto(world):
    rel = release(world)
    sign_g3(world, rel)
    r = call(world, "POST", f"platform/readiness/{rel['public_id']}/veto",
             {"requirement": "cto_signoff", "note": "Not this build"}, "lead")
    assert r.status_code == 200 and show(world, rel)["decision"] == "no_go"


# ------------------------------------------------------------------ the deploy step's gate
def gate(**options):
    out = io.StringIO()
    call_command("release_gate", stdout=out, **options)
    return out.getvalue()


def test_the_deploy_gate_passes_only_an_approved_release_for_its_own_build(world):
    rel = release(world)
    with pytest.raises(CommandError, match="not approved"):
        gate(release=rel["release_ref"], build=rel["build"])
    sign_g3(world, rel)
    assert "approved" in gate(release=rel["release_ref"], build=rel["build"])
    with pytest.raises(CommandError, match="different build"):
        gate(release=rel["release_ref"], build="build-something-else")
    with pytest.raises(CommandError, match="no such release"):
        gate(release="rel-unknown", build=rel["build"])
    with pytest.raises(CommandError, match="not approved"):          # G4 not signed
        gate(release=rel["release_ref"], build=rel["build"], country="GB")


def test_a_veto_stops_the_deploy_gate(world):
    rel = release(world)
    sign_g3(world, rel)
    call(world, "POST", f"platform/readiness/{rel['public_id']}/veto",
         {"requirement": "security_signoff", "note": "Found a problem"}, "s8")
    with pytest.raises(CommandError, match="security_signoff"):
        gate(release=rel["release_ref"], build=rel["build"])


# ------------------------------------------------------------------ the key service
def test_the_aws_signature_matches_awss_published_example():
    """AWS SigV4 test suite, "get-vanilla"."""
    creds = aws.Credentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
    signed = aws.sign(method="GET", url="https://example.amazonaws.com/", headers={}, body=b"",
                      service="service", area="us-east-1", creds=creds,
                      now=dt.datetime(2015, 8, 30, 12, 36, tzinfo=dt.timezone.utc))
    assert signed["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_with_the_key_service_the_keys_are_unwrapped_by_kms_and_kept_in_memory(settings,
                                                                               monkeypatch):
    real = b"r" * 32
    asked = []

    def fake(blob):
        asked.append(blob)
        return real

    monkeypatch.setattr(aws, "kms_decrypt", fake)
    crypto._unwrapped.clear()
    settings.FIELD_KEY_SOURCE = "aws_kms"
    settings.FIELD_KEYS = "kms1:d3JhcHBlZC1ibG9i"
    settings.FIELD_KEY_ID = "kms1"
    try:
        token = crypto.encrypt("hello", tenant_id="t-1", context="x")
        assert crypto.decrypt(token, tenant_id="t-1", context="x") == "hello"
        assert asked == ["d3JhcHBlZC1ibG9i"]                       # asked once, then remembered
    finally:
        crypto._unwrapped.clear()


def test_when_the_key_service_fails_nothing_is_readable_and_nothing_leaks(settings,
                                                                          monkeypatch):
    def broken(blob):
        raise aws.AwsError("kms answered 400")

    monkeypatch.setattr(aws, "kms_decrypt", broken)
    crypto._unwrapped.clear()
    settings.FIELD_KEY_SOURCE = "aws_kms"
    settings.FIELD_KEYS = "kms1:d3JhcHBlZC1ibG9i"
    with pytest.raises(crypto.CryptoError) as caught:
        crypto.encrypt("hello", tenant_id="t-1", context="x")
    assert str(caught.value) == ""
