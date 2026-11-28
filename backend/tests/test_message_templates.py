"""Every email and text is an approved template (DEV-386, GHE-16): a message whose wording version
is not approved by a safety officer AND, separately, privacy is held, and goes the moment it is
approved; a new wording needs a new approval; nobody gives both sign-offs. Mostly refusals."""
import json
import logging
import re

import pytest

from foundation import mail as mailer
from foundation import message_templates as catalogue_module
from foundation import template_policy
from foundation.cell_context import tenant_transaction
from foundation.models import MailState, QueuedEmail, QueuedText
from foundation.release_check import release_problems
from foundation.texts import queue_text
from tests.test_platform import Platform
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

LINK = {"link": "https://app.test/?action=verify&token=abc"}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_TEMPLATE_GATING = "enforce"


@pytest.fixture
def zoiko():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12"), ("noor", "R12")):
        w.staff(subject, role)
    return w


def call(world, method, path, body=None, who="sam"):
    header = world.header(world.tenant_public_id, who)
    if method == "GET":
        return world.client.get(f"/api/v1/{path}", **header)
    return world.client.post(f"/api/v1/{path}", json.dumps(body or {}),
                             content_type="application/json", **header)


def sign(world, key, as_role, who, channel="email"):
    return call(world, "POST", f"platform/templates/{channel}/{key}/approve", {"as": as_role}, who)


def queue(world, kind="verify_email", values=None):
    with tenant_transaction(world.ctx):
        return mailer.queue_email(kind=kind, to_address="asha@example.org",
                                  values=LINK if values is None else values)


def templates(world, who="sam"):
    return json.loads(call(world, "GET", "platform/templates", who=who).content)


# ------------------------------------------------------------------ the catalogue
def test_every_email_the_code_sends_is_in_the_catalogue():
    """Scan the code for queue_email(kind=...): each kind must be an approved template."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    used: set[str] = set()
    for path in [*root.glob("modules/**/*.py"), *root.glob("foundation/*.py")]:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'queue_email\(kind="([a-z_]+)"', text))
        for constant in re.findall(r"queue_email\(kind=([A-Z_]+)", text):
            used |= set(re.findall(rf'^{constant} = "([a-z_]+)"', text, re.MULTILINE))
    assert used and used <= set(catalogue_module.EMAIL_TEMPLATES), used - set(
        catalogue_module.EMAIL_TEMPLATES)


def test_a_change_of_wording_is_a_new_version(monkeypatch):
    before = catalogue_module.EMAIL_TEMPLATES["password_changed"].version
    changed = catalogue_module.EmailTemplate(
        "password_changed", "security", "any", "Your Zoiko Access password was changed",
        "Something else entirely.")
    assert changed.version != before


def test_no_template_carries_health_information():
    assert {t.exposure for t in catalogue_module.EMAIL_TEMPLATES.values()} == {"none"}
    assert {row["exposure"] for row in catalogue_module.catalogue()} == {"none"}


# ------------------------------------------------------------------ held until approved
def test_an_unapproved_email_is_held_and_not_sent(zoiko):
    row = queue(zoiko)
    assert row.state == MailState.HELD and row.template_key == "verify_email"
    assert row.template_version
    assert mailer.send_due().sent == 0
    assert mailer.mail_stats()["held"] == 1


def test_one_sign_off_is_not_enough(zoiko):
    queue(zoiko)
    r = sign(zoiko, "verify_email", "clinical", "iyer")
    assert r.status_code == 200 and json.loads(r.content)["template"]["approved"] is False
    assert QueuedEmail.objects.get().state == MailState.HELD


def test_both_sign_offs_approve_it_and_what_was_held_goes(zoiko):
    queue(zoiko)
    sign(zoiko, "verify_email", "clinical", "iyer")
    r = sign(zoiko, "verify_email", "privacy", "sam")
    done = json.loads(r.content)["template"]
    assert done["approved"] is True and done["released"] == 1
    assert mailer.send_due().sent == 1
    assert queue(zoiko).state == MailState.QUEUED                       # later ones go straight


def test_nobody_gives_both_sign_offs(zoiko):
    zoiko.staff("both", "R11")
    with tenant_transaction(zoiko.ctx):
        from modules.trust import api as trust
        actor = next(a for a, label in trust.actor_labels().items() if "both" in label)
        trust.assign_role(actor_public_id=actor, organization_public_id=zoiko.org,
                          role_code="R12", by="system")
    sign(zoiko, "verify_email", "clinical", "both")
    assert sign(zoiko, "verify_email", "privacy", "both").status_code == 403


def test_each_role_signs_only_its_own_part(zoiko):
    assert sign(zoiko, "verify_email", "clinical", "sam").status_code == 403    # support
    assert sign(zoiko, "verify_email", "privacy", "iyer").status_code == 403    # safety
    assert sign(zoiko, "verify_email", "anyone", "sam").status_code == 422
    assert sign(zoiko, "no_such_template", "privacy", "sam").status_code == 404


def test_a_sign_off_cannot_be_given_twice_or_after_approval(zoiko):
    sign(zoiko, "verify_email", "clinical", "iyer")
    assert sign(zoiko, "verify_email", "clinical", "noor").status_code == 409
    sign(zoiko, "verify_email", "privacy", "sam")
    assert sign(zoiko, "verify_email", "privacy", "tess").status_code == 409


def test_new_wording_needs_a_new_approval(zoiko, monkeypatch):
    sign(zoiko, "password_changed", "clinical", "iyer")
    sign(zoiko, "password_changed", "privacy", "sam")
    assert queue(zoiko, "password_changed", {}).state == MailState.QUEUED
    new = catalogue_module.EmailTemplate(
        "password_changed", "security", "any", "Your Zoiko Access password was changed",
        "Hello,\n\nYour password changed. Contact your clinic if this was not you.\n")
    monkeypatch.setitem(catalogue_module.EMAIL_TEMPLATES, "password_changed", new)
    assert queue(zoiko, "password_changed", {}).state == MailState.HELD
    row = next(t for t in templates(zoiko)["templates"] if t["key"] == "password_changed")
    assert row["approved"] is False and "every other device" in row["previous_wording"]


def test_texts_are_held_and_released_the_same_way(zoiko):
    with tenant_transaction(zoiko.ctx):
        held = queue_text(kind="patient_update", to_number="+447700900123", template="update")
    assert held.state == MailState.HELD and held.template_version
    sign(zoiko, "update", "clinical", "iyer", channel="text")
    sign(zoiko, "update", "privacy", "sam", channel="text")
    assert QueuedText.objects.get().state == MailState.QUEUED


# ------------------------------------------------------------------ the screen
def test_the_screen_lists_every_message_and_what_is_held(zoiko):
    queue(zoiko)
    data = templates(zoiko)
    keys = {(t["channel"], t["key"]) for t in data["templates"]}
    assert ("email", "verify_email") in keys and ("text", "code") in keys
    row = next(t for t in data["templates"] if t["key"] == "verify_email")
    assert row["held"] == 1 and "{link}" in row["wording"] and row["variables"] == ["link"]
    assert data["may_sign_as"] == ["privacy"] and data["mode"] == "enforce"
    assert templates(zoiko, "iyer")["may_sign_as"] == ["clinical"]


def test_customers_cannot_see_or_sign_templates(zoiko):
    from tests.test_clinician import Practice

    p = Practice()
    assert p.get("platform/templates", subject="ops").status_code == 403


def test_a_sign_off_is_audited(zoiko):
    sign(zoiko, "verify_email", "clinical", "iyer")
    from modules.evidence.models import AuditEvent
    with tenant_transaction(zoiko.ctx):
        assert AuditEvent.objects.filter(action="template.sign_off",
                                         tenant_id=zoiko.ctx.tenant_id).exists()


# ------------------------------------------------------------------ report, off, production
def test_report_sends_and_logs_only_the_template_and_version(zoiko, settings, caplog):
    settings.ZOIKO_TEMPLATE_GATING = "report"
    with caplog.at_level(logging.WARNING, logger="zoiko.templates"):
        assert queue(zoiko).state == MailState.QUEUED
    [line] = [r.getMessage() for r in caplog.records if r.name == "zoiko.templates"]
    assert line.startswith("template_gating would_hold channel=email template=verify_email")
    assert "asha" not in line and "token" not in line


def test_an_unknown_mode_holds(zoiko, settings):
    settings.ZOIKO_TEMPLATE_GATING = "perhaps"
    assert queue(zoiko).state == MailState.HELD


@pytest.mark.parametrize("value", ["report", "off", ""])
def test_a_real_server_must_enforce(value):
    assert any("ZOIKO_TEMPLATE_GATING" in p
               for p in release_problems(good(ZOIKO_TEMPLATE_GATING=value)))
    assert release_problems(good(ZOIKO_TEMPLATE_GATING="enforce")) == []


def test_the_policy_reads_approvals_for_this_cell_only(zoiko):
    assert template_policy.is_approved("email", "verify_email", "0" * 16) is False
