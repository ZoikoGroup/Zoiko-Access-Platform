"""Outgoing addresses approved by Zoiko (DEV-384): registering a webhook address asks for approval;
nothing is sent to an address Zoiko has not approved for that clinic (the challenge or any
delivery); approving, refusing and revoking; nobody approves their own proposal; and production
must enforce. Mostly what must be REFUSED."""
import json
import logging

import pytest

from foundation.release_check import release_problems
from modules.integration import services
from tests.test_platform import Platform
from tests.test_release_check import good
from tests.test_webhooks import URL, Receiver, active, add, deliveries, happen, made, send

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

HOST = "partner.example.org"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_EGRESS = "enforce"
    services.register_subscribers()


@pytest.fixture
def zoiko():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12")):
        w.staff(subject, role)
    return w


@pytest.fixture
def p():
    from modules.trust import api as trust
    from tests.test_clinician import Practice

    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


@pytest.fixture
def receiver(monkeypatch):
    fake = Receiver()
    monkeypatch.setattr(services, "http_post", fake)
    return fake


def platform(world, method, path, body=None, who="sam"):
    header = world.header(world.tenant_public_id, who)
    if method == "GET":
        return world.client.get(f"/api/v1/{path}", **header)
    return world.client.post(f"/api/v1/{path}", json.dumps(body or {}),
                             content_type="application/json", **header)


def permits(world, who="sam"):
    return json.loads(platform(world, "GET", "platform/egress", who=who).content)["permits"]


def permit_for(world, p, host=HOST):
    return next(x for x in permits(world)
                if x["host"] == host and x["customer"] == p.clinic.tenant_public_id)


def decide(world, permit, step, who="sam", reason=""):
    return platform(world, "POST", f"platform/egress/{permit['id']}/{step}",
                    {"reason": reason}, who)


def verify(p):
    ep = made(p)
    return ep, p.post(f"staff/integrations/webhooks/{ep['public_id']}/verify", subject="boss")


# ------------------------------------------------------------------ asking
def test_registering_an_address_asks_zoiko_and_the_clinic_sees_it_waiting(zoiko, p):
    ep = made(p)
    assert ep["address_permit"] == "proposed"
    permit = permit_for(zoiko, p)
    assert permit["state"] == "proposed" and permit["purpose"] == "webhook"
    assert permit["customer_name"] and permit["proposed_by"]          # who asked, for Zoiko


def test_the_same_address_asked_twice_is_one_request(zoiko, p):
    made(p)
    made(p, label="Second")
    assert len([x for x in permits(zoiko) if x["host"] == HOST]) == 1


# ------------------------------------------------------------------ nothing goes out unapproved
def test_an_unapproved_address_gets_no_challenge(zoiko, p, receiver):
    _, r = verify(p)
    assert r.status_code == 403 and json.loads(r.content)["fields"] == ["address_not_approved"]
    assert receiver.calls == []                                       # nothing was sent


def test_once_approved_the_challenge_is_sent_and_deliveries_flow(zoiko, p, receiver):
    made(p)
    assert decide(zoiko, permit_for(zoiko, p), "approve").status_code == 200
    ep = active(p, label="After approval")
    assert ep["address_permit"] == "approved"
    happen(p)
    assert send(p, receiver)["delivered"] == 1


def test_revoking_stops_the_next_delivery_and_says_why(zoiko, p, receiver):
    made(p)
    decide(zoiko, permit_for(zoiko, p), "approve")
    active(p, label="Live")
    decide(zoiko, permit_for(zoiko, p), "revoke", reason="Partner contract ended")
    sent_before = len(receiver.calls)
    happen(p)
    assert send(p, receiver)["dead_letter"] == 1
    assert len(receiver.calls) == sent_before                       # not sent
    [row] = deliveries(p, "dead_letter")
    assert row["last_error"] == "address_not_approved"


def test_an_approval_for_one_clinic_does_not_cover_another(zoiko, p, receiver):
    from modules.trust import api as trust
    from tests.test_clinician import Practice

    made(p)
    decide(zoiko, permit_for(zoiko, p), "approve")
    other = Practice()
    other.login("boss", trust.ActorType.FACILITY_USER, "R13")
    _, r = verify(other)
    assert r.status_code == 403


# ------------------------------------------------------------------ Zoiko decides
def test_refusing_and_revoking_need_a_reason(zoiko, p):
    made(p)
    permit = permit_for(zoiko, p)
    assert decide(zoiko, permit, "refuse").status_code == 422
    assert decide(zoiko, permit, "refuse", reason="Unknown company").status_code == 200
    assert decide(zoiko, permit, "approve").status_code == 409       # refused stays refused
    made(p, label="Asked again")                                     # the clinic asks again
    assert permit_for(zoiko, p)["state"] == "proposed"


def test_nobody_approves_their_own_proposal(zoiko, p):
    r = platform(zoiko, "POST", "platform/egress",
                 {"customer": p.clinic.tenant_public_id, "host": "lab.example.net",
                  "purpose": "connector"})
    assert r.status_code == 201
    permit = permit_for(zoiko, p, "lab.example.net")
    assert decide(zoiko, permit, "approve", who="sam").status_code == 403
    assert decide(zoiko, permit, "approve", who="tess").status_code == 200


@pytest.mark.parametrize("host", ["*.example.org", "10.0.0.1", "localhost", "", "a" * 260])
def test_only_exact_public_host_names_can_be_proposed(zoiko, p, host):
    r = platform(zoiko, "POST", "platform/egress",
                 {"customer": p.clinic.tenant_public_id, "host": host, "purpose": "connector"})
    assert r.status_code == 422


def test_customers_cannot_see_or_decide_permits(zoiko, p):
    made(p)
    assert p.get("platform/egress", subject="boss").status_code == 403


# ------------------------------------------------------------------ report, off and production
def test_report_sends_and_logs_only_the_host_and_purpose(zoiko, p, receiver, settings, caplog):
    settings.ZOIKO_EGRESS = "report"
    with caplog.at_level(logging.WARNING, logger="zoiko.egress"):
        _, r = verify(p)
    assert r.status_code == 200 and receiver.calls
    assert f"egress would_refuse purpose=webhook host={HOST}" in [
        x.getMessage() for x in caplog.records if x.name == "zoiko.egress"]


def test_an_unknown_mode_fails_closed(zoiko, p, receiver, settings):
    settings.ZOIKO_EGRESS = "sometimes"
    _, r = verify(p)
    assert r.status_code == 403 and receiver.calls == []


@pytest.mark.parametrize("value", ["report", "off", ""])
def test_a_real_server_must_enforce(value):
    assert any("ZOIKO_EGRESS" in x for x in release_problems(good(ZOIKO_EGRESS=value)))
    assert release_problems(good(ZOIKO_EGRESS="enforce")) == []


def test_the_email_provider_must_be_a_known_host(settings):
    from django.core.mail import EmailMessage

    from foundation.sendgrid_backend import SendGridBackend, SendGridError

    settings.SENDGRID_API_KEY = "SG.test"
    settings.SENDGRID_API_HOST = "https://mail.attacker.example"
    with pytest.raises(SendGridError):
        SendGridBackend().send_messages([EmailMessage("x", "y", "a@zoiko.example", ["b@ex.org"])])


def test_the_address_shape_rules_still_apply(p):
    assert add(p, url="http://partner.example.org/x").status_code == 422
    assert URL.startswith("https://")
