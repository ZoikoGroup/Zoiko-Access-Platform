"""Partners sending to us (GHE-13 WF-04, WF-08; DEV-131): credentials managed by a clinic
administrator, and a signed message checked in a fixed order before anything is read."""
import base64
import io
import json
import time

import pytest
from django.core.management import call_command
from django.test import Client

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.integration.delivery import sign, signature_header
from modules.integration.inbound import _SIGNATURE
from modules.integration.models import InboundEvent, PartnerClient
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
URL = "/api/v1/partner-events"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def add(p, label="Lab network", types=("partner.lab.",), subject="boss"):
    return p.post("staff/integrations/partners",
                  {"label": label, "allowed_types": list(types)}, subject=subject)


def partner(p, **kw):
    r = add(p, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["client"]


def body_of(kind="partner.lab.result_ready.v1", **data):
    return json.dumps({"type": kind, "data": data or {"order": "ord-123"}},
                      separators=(",", ":")).encode()


def post(client, raw, delivery="delivery-0001", *, secret, key_id, at=None, signature=None):
    stamp = int(at if at is not None else time.time())
    header = signature if signature is not None else signature_header(
        secret, key_id, stamp, delivery, raw)
    return Client().post(URL, raw, content_type="application/json",
                         HTTP_ZOIKO_SIGNATURE=header, HTTP_ZOIKO_DELIVERY_ID=delivery)


def send(c, raw=None, delivery="delivery-0001", **kw):
    return post(c, raw if raw is not None else body_of(), delivery,
                secret=c["secret"], key_id=c["key_id"], **kw)


def events(p, state="", subject="boss"):
    path = "staff/integrations/inbound" + (f"?state={state}" if state else "")
    return json.loads(p.get(path, subject=subject).content)["events"]


def clients(p, subject="boss"):
    return json.loads(p.get("staff/integrations/partners", subject=subject).content)["clients"]


# ------------------------------------------------------------------ the credential
def test_an_administrator_adds_a_partner_and_sees_the_secret_only_once(p):
    c = partner(p)
    assert c["secret"].startswith("pcsec_") and c["state"] == "active"
    assert c["key_id"].startswith("pc.") and len(c["key_id"].split(".")) == 3
    shown = clients(p)
    assert [x["public_id"] for x in shown] == [c["public_id"]]
    assert "secret" not in shown[0] and c["secret"] not in json.dumps(shown)


def test_the_secret_is_stored_sealed(p):
    c = partner(p)
    with tenant_transaction(p.clinic.ctx):
        row = PartnerClient.objects.get(public_id=c["public_id"])
    assert row.secret_enc.startswith("zf1:") and c["secret"] not in row.secret_enc


def test_only_a_clinic_administrator_can_manage_partners(p):
    for who in ("ops", "doc", "pat"):
        assert add(p, subject=who).status_code == 403
        assert p.get("staff/integrations/partners", subject=who).status_code == 403
        assert p.get("staff/integrations/inbound", subject=who).status_code == 403


@pytest.mark.parametrize("types", [[], ["booking."], ["partner"], ["partner.Lab."],
                                   [f"partner.t{i}." for i in range(11)], "partner.lab.", None])
def test_a_partner_can_only_be_allowed_partner_event_kinds(p, types):
    r = p.post("staff/integrations/partners", {"label": "Lab", "allowed_types": types},
               subject="boss")
    assert r.status_code == 422


def test_at_most_ten_live_partners(p):
    for i in range(10):
        assert add(p, label=f"Partner {i}").status_code == 201
    assert add(p, label="One too many").status_code == 429


# ------------------------------------------------------------------ the message is accepted
def test_a_correctly_signed_message_is_accepted_and_kept(p):
    c = partner(p)
    r = send(c)
    assert r.status_code == 202 and r.json()["received"] is True and r.json()["duplicate"] is False
    [row] = events(p)
    assert row["state"] == "received" and row["event_type"] == "partner.lab.result_ready.v1"
    assert row["client_label"] == "Lab network" and clients(p)[0]["last_used_at"]


def test_the_outbound_signature_format_is_the_same_one_the_inbound_side_checks(p):
    c = partner(p)
    header = signature_header(c["secret"], c["key_id"], 1_700_000_000, "delivery-0001", b"{}")
    assert _SIGNATURE.match(header)
    assert header.endswith(sign(c["secret"], 1_700_000_000, "delivery-0001", b"{}"))


# ------------------------------------------------------------------ identity failures: one answer
def failures(p, c):
    """Every way of failing to prove who is sending."""
    raw = body_of()
    other = partner(p, label="Other partner")
    good = signature_header(c["secret"], c["key_id"], int(time.time()), "delivery-0001", raw)
    stamp = int(time.time())
    yield post(c, raw, secret=c["secret"], key_id=c["key_id"], signature="")
    yield post(c, raw, secret=c["secret"], key_id=c["key_id"], signature="nonsense")
    yield post(c, raw, secret="wrong-secret", key_id=c["key_id"])
    yield post(c, raw, secret=other["secret"], key_id=c["key_id"])        # another partner's secret
    yield post(c, raw, secret=c["secret"], key_id="pc.unknowncustomer.deadbeef")
    yield post(c, raw, secret=c["secret"], key_id=c["key_id"][:-2] + "zz")
    yield post(c, raw, secret=c["secret"], key_id=c["key_id"], at=stamp - 600)    # too old
    yield post(c, raw, secret=c["secret"], key_id=c["key_id"], at=stamp + 600)    # from the future
    yield post(c, body_of(order="tampered"), secret=c["secret"], key_id=c["key_id"],
               signature=good)                                    # body changed after signing
    yield post(c, raw, "delivery-9999", secret=c["secret"], key_id=c["key_id"],
               signature=good)                                            # id changed after signing
    yield Client().post(URL, raw, content_type="application/json")        # no headers at all


def test_every_identity_failure_gets_the_same_401(p):
    c = partner(p)
    answers = list(failures(p, c))
    assert len(answers) == 11
    assert {a.status_code for a in answers} == {401}
    assert len({json.dumps({k: v for k, v in a.json().items() if k != "request_id"},
                           sort_keys=True) for a in answers}) == 1
    assert events(p) == []


def test_nothing_in_the_body_is_read_before_the_signature_is_proved(p):
    c = partner(p)
    not_json = b"\xff\xfe not json at all"
    r = post(c, not_json, secret="wrong-secret", key_id=c["key_id"])
    assert r.status_code == 401 and events(p) == []


def test_an_oversized_body_is_refused_before_anything_else(p):
    c = partner(p)
    big = json.dumps({"type": "partner.lab.x.v1", "data": {}, "pad": "x" * 5000}).encode()
    assert send(c, big).status_code == 413
    assert Client().post(URL, big, content_type="application/json").status_code == 413


def test_only_post_is_allowed(p):
    assert Client().get(URL).status_code == 405


# ------------------------------------------------------------------ repeats
def test_the_same_delivery_twice_is_accepted_safely_and_stored_once(p):
    c = partner(p)
    first, second = send(c), send(c)
    assert first.status_code == second.status_code == 202
    assert second.json()["duplicate"] is True and second.json()["id"] == first.json()["id"]
    assert len(events(p)) == 1


def test_a_reused_delivery_id_with_different_content_is_refused(p):
    c = partner(p)
    send(c)
    r = send(c, body_of(order="a-different-order"))
    assert r.status_code == 409
    assert len(events(p)) == 1


# ------------------------------------------------------------------ held messages
@pytest.mark.parametrize("raw,reason", [
    (b"not json", "schema"),
    (b'["a list"]', "schema"),
    (json.dumps({"type": "Bad Type", "data": {}}).encode(), "schema"),
    (json.dumps({"type": "partner.lab.x.v1", "data": {"nested": {"a": 1}}}).encode(), "schema"),
    (json.dumps({"type": "partner.lab.x.v1", "data": {}, "extra": 1}).encode(), "schema"),
    (json.dumps({"type": "partner.lab.x.v1", "data": {"note": "y" * 200}}).encode(), "schema"),
    (json.dumps({"type": "partner.imaging.x.v1", "data": {}}).encode(), "unsupported_event"),
])
def test_a_signed_message_of_the_wrong_shape_or_kind_is_held_not_stored(p, raw, reason):
    c = partner(p)
    r = send(c, raw)
    assert r.status_code == 422 and r.json()["held"] is True and r.json()["error"] == reason
    [row] = events(p, "quarantined")
    assert row["reason"] == reason and len(row["body_digest"]) == 64
    with tenant_transaction(p.clinic.ctx):
        assert InboundEvent.objects.get(public_id=row["public_id"]).body == ""


def test_a_held_message_repeated_is_still_one_row(p):
    c = partner(p)
    send(c, b"not json")
    again = send(c, b"not json")
    assert again.status_code == 202 and again.json()["duplicate"] is True
    assert len(events(p)) == 1


# ------------------------------------------------------------------ the budget
def test_a_partner_over_its_per_minute_budget_is_told_to_wait(p):
    c = partner(p)
    with tenant_transaction(p.clinic.ctx):
        PartnerClient.objects.filter(public_id=c["public_id"]).update(rate_per_minute=3)
    for i in range(3):
        assert send(c, delivery=f"delivery-{i:04d}").status_code == 202
    r = send(c, delivery="delivery-0099")
    assert r.status_code == 429 and r["Retry-After"] == "60"


def test_one_partners_budget_does_not_use_up_another(p):
    one, two = partner(p, label="One"), partner(p, label="Two")
    with tenant_transaction(p.clinic.ctx):
        PartnerClient.objects.filter(public_id=one["public_id"]).update(rate_per_minute=1)
    assert send(one, delivery="delivery-0001").status_code == 202
    assert send(one, delivery="delivery-0002").status_code == 429
    assert send(two, delivery="delivery-0001").status_code == 202


# ------------------------------------------------------------------ suspending, revoking, rotating
def test_a_suspended_partner_is_refused_at_once_and_welcome_again_when_resumed(p):
    c = partner(p)
    step = f"staff/integrations/partners/{c['public_id']}"
    assert send(c).status_code == 202
    assert p.post(f"{step}/suspend", subject="boss").status_code == 200
    assert send(c, delivery="delivery-0002").status_code == 401
    assert p.post(f"{step}/resume", subject="boss").status_code == 200
    assert send(c, delivery="delivery-0003").status_code == 202


def test_a_revoked_partner_is_final(p):
    c = partner(p)
    step = f"staff/integrations/partners/{c['public_id']}"
    assert p.post(f"{step}/revoke", subject="boss").status_code == 200
    assert send(c).status_code == 401
    for name in ("resume", "suspend", "rotate-secret"):
        assert p.post(f"{step}/{name}", subject="boss").status_code == 409
    assert p.post(f"{step}/explode", subject="boss").status_code == 404


def test_rotating_the_secret_ends_the_old_one_at_once(p):
    c = partner(p)
    r = p.post(f"staff/integrations/partners/{c['public_id']}/rotate-secret", subject="boss")
    new = json.loads(r.content)["client"]
    assert new["secret"] != c["secret"] and new["key_id"] == c["key_id"]
    assert send(c, delivery="delivery-0001").status_code == 401              # old secret
    assert post(new, body_of(), "delivery-0002", secret=new["secret"],
                key_id=new["key_id"]).status_code == 202


def test_steps_are_for_administrators_only(p):
    c = partner(p)
    step = f"staff/integrations/partners/{c['public_id']}"
    for who in ("ops", "doc", "pat"):
        assert p.post(f"{step}/suspend", subject=who).status_code == 403
    assert clients(p)[0]["state"] == "active"


# ------------------------------------------------------------------ separation and record
def test_another_customer_sees_none_of_it_and_cannot_use_the_key(p):
    c = partner(p)
    send(c)
    outsider = Web()
    outsider.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    assert json.loads(outsider.get("staff/integrations/partners",
                                   subject="admin2").content)["clients"] == []
    assert json.loads(outsider.get("staff/integrations/inbound",
                                   subject="admin2").content)["events"] == []
    assert outsider.post(f"staff/integrations/partners/{c['public_id']}/suspend",
                         subject="admin2").status_code == 404


def test_changes_are_audited_without_the_secret(p):
    c = partner(p)
    step = f"staff/integrations/partners/{c['public_id']}"
    p.post(f"{step}/suspend", subject="boss")
    p.post(f"{step}/resume", subject="boss")
    rotated = json.loads(p.post(f"{step}/rotate-secret", subject="boss").content)["client"]
    with tenant_transaction(p.clinic.ctx):
        found = list(AuditEvent.objects.filter(object_ref=c["public_id"]).order_by("chain_seq"))
    assert [e.action for e in found] == [
        "integration.partner_created", "integration.partner_suspend",
        "integration.partner_resume", "integration.partner_secret_rotated"]
    dump = json.dumps([e.details for e in found])
    assert c["secret"] not in dump and rotated["secret"] not in dump


def test_the_sealed_secret_moves_to_a_new_key_and_still_verifies(p, settings):
    c = partner(p)
    settings.FIELD_KEYS = f"{settings.FIELD_KEYS},t2:{base64.b64encode(b'C' * 32).decode()}"
    settings.FIELD_KEY_ID = "t2"
    call_command("rotate_field_keys", stdout=io.StringIO())
    with tenant_transaction(p.clinic.ctx):
        assert PartnerClient.objects.get(public_id=c["public_id"]).secret_enc.startswith("zf1:t2:")
    assert send(c).status_code == 202


# ------------------------------------------------------ a partner cannot pick its customer (T-12)
@pytest.mark.parametrize("extra", [{"tenant_id": "x"}, {"cell_id": "ZZ-99"}, {"patient": "p-1"},
                                   {"region": "EU"}, {"organization": "other"}])
def test_a_message_that_tries_to_name_a_customer_cell_patient_or_region_is_held(p, extra):
    c = partner(p)
    raw = json.dumps({"type": "partner.lab.x.v1", "data": {}, **extra}).encode()
    r = send(c, raw)
    assert r.status_code == 422 and r.json()["held"] is True
    assert [e["state"] for e in events(p)] == ["quarantined"]


def test_values_inside_the_data_never_move_a_message_to_another_customer(p):
    other = Web()
    other.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    c = partner(p)
    raw = json.dumps({"type": "partner.lab.result_ready.v1",
                      "data": {"tenant_id": "someone-else", "order": "ord-1"}}).encode()
    assert send(c, raw).status_code == 202
    assert len(events(p)) == 1
    assert json.loads(other.get("staff/integrations/inbound", subject="admin2").content
                      )["events"] == []
