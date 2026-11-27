"""Decided 27 November 2026 (DEV-399): an outside model (Claude) may draft replies for staff only,
with personal details removed first, no clinical content, under a permit signed by a clinic
administrator AND a Zoiko platform officer. Nothing is sent by itself and no text is stored."""
import json
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from foundation.cell_context import tenant_transaction
from modules.ai import api as ai
from modules.ai import claude, providers, runs
from modules.ai.models import AiRequest
from modules.trust import api as trust
from tests.test_clinician import Practice
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
SECRET = "My name is Jane Doe, born 12/03/1980, NHS 9434765919, call 07700 900123 or jane@x.org"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


# ------------------------------------------------------------------ removing personal details
def test_personal_details_are_replaced_before_anything_leaves():
    out = ai.redact(SECRET + " at SW1A 1AA, see https://x.org/me", names=["Jane Doe"])
    for gone in ("Jane", "Doe", "12/03/1980", "9434765919", "07700 900123", "jane@x.org",
                 "SW1A 1AA", "https://x.org/me"):
        assert gone not in out
    for kept in ("[name]", "[date]", "[phone]", "[email]", "[postcode]", "[link]"):
        assert kept in out
    assert ai.redact("Member A12 ref 4410923") == "Member A12 ref [number]"
    assert ai.redact("Can I move my appointment?") == "Can I move my appointment?"


# ------------------------------------------------------------------ the Claude call
class FakeAnthropic(types.ModuleType):
    """Stands in for the SDK: records what would have been sent, answers as told."""

    def __init__(self, answer):
        super().__init__("anthropic")
        self.sent: list[dict[str, Any]] = []
        outer = self

        class APIConnectionError(Exception):
            pass

        class RateLimitError(Exception):
            pass

        class APIStatusError(Exception):
            pass

        self.APIConnectionError, self.RateLimitError = APIConnectionError, RateLimitError
        self.APIStatusError = APIStatusError

        class Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                if answer == "fail":
                    raise outer.APIConnectionError("down")
                return answer

        class Anthropic:
            def __init__(self):
                self.beta = SimpleNamespace(messages=Messages())

        self.Anthropic = Anthropic


def reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=text)])


def test_claude_gets_only_the_redacted_text_and_the_fixed_prompt(monkeypatch):
    fake = FakeAnthropic(reply("We can move your appointment."))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert claude.draft_reply(SECRET) == "We can move your appointment."
    [sent] = fake.sent
    assert sent["model"] == "claude-opus-5-5" and sent["system"] == claude.SYSTEM_PROMPT
    assert sent["fallbacks"] == "default"
    body = json.dumps(sent["messages"])
    assert "9434765919" not in body and "07700" not in body and "jane@x.org" not in body


def test_a_refusal_or_a_failure_gives_an_empty_draft_never_an_error_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", FakeAnthropic(reply("", stop="refusal")))
    assert claude.draft_reply("hello") == ""
    monkeypatch.setitem(sys.modules, "anthropic", FakeAnthropic("fail"))
    assert claude.draft_reply("hello") == ""


def test_switched_on_the_permit_pins_exactly_this_model_and_prompt(settings):
    settings.AI_PROVIDER = "anthropic"
    try:
        providers.register_builtin()
        model, prompt = ai.feature_digests(ai.REPLY_DRAFT)
        assert model == ai.digest(claude.model()) and prompt == ai.digest(claude.SYSTEM_PROMPT)
    finally:
        settings.AI_PROVIDER = ""
        providers.register_builtin()
    assert ai.feature_digests(ai.REPLY_DRAFT) == (None, None)       # the template pins nothing


# ------------------------------------------------------------------ the staff draft
@pytest.fixture
def p():
    practice = Practice()
    practice.boss = practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    setattr(practice, "boss2",  # noqa: B010
            practice.login("boss2", trust.ActorType.FACILITY_USER, "R13"))
    made = practice.post("messages/threads", {"subject": "Moving my visit",
                                              "body": SECRET}, subject="pat")
    assert made.status_code == 201, made.content
    setattr(practice, "thread", json.loads(made.content)["thread"]["id"])  # noqa: B010
    return practice


def permit_live(p):
    import datetime as dt

    from foundation.timeutil import utc_now
    with tenant_transaction(p.clinic.ctx):
        made = ai.create_permit(feature_id=ai.REPLY_DRAFT, purpose="Draft replies for staff",
                                lane="A", level="A1", model_digest="a" * 64,
                                prompt_digest="b" * 64, created_by_ref=p.boss)
        for state in ("QUALIFICATION", "DESIGN_CONTROL", "VALIDATION", "SANDBOX", "SHADOW",
                      "RELEASE_REVIEW"):
            ai.advance(made["id"], state, actor_ref=p.boss)
        ai.sign_off(made["id"], actor_ref=p.boss2)
        ai.zoiko_sign(made["id"], officer_ref="zoiko-officer")
        ai.advance(made["id"], "AUTHORIZED", actor_ref=p.boss,
                   expiry=utc_now() + dt.timedelta(days=30))


def draft(p, who="ops"):
    return p.post(f"messages/threads/{p.thread}/draft-reply", {}, subject=who)


def test_without_a_permit_there_is_no_draft(p):
    assert draft(p).status_code == 403


def test_with_a_permit_staff_get_a_draft_and_nothing_is_sent_or_stored(p):
    permit_live(p)
    r = draft(p)
    assert r.status_code == 200, r.content
    body = json.loads(r.content)
    assert body["draft"] and body["decision"] == "HUMAN_REVIEW"
    with tenant_transaction(p.clinic.ctx):
        [req] = AiRequest.objects.all()
        assert len(req.input_digest) == 64                       # a fingerprint, never the text
    thread = json.loads(p.get(f"messages/threads/{p.thread}", subject="ops").content)["thread"]
    assert len(thread["messages"]) == 1                          # nothing was sent


def test_patients_cannot_ask_for_a_draft(p):
    permit_live(p)
    assert draft(p, who="pat").status_code == 403


def test_the_redacted_text_is_what_the_provider_sees(p, monkeypatch):
    permit_live(p)
    seen: list[str] = []

    def provider(text: str) -> str:
        seen.append(text)
        return "A draft."

    monkeypatch.setitem(runs._PROVIDERS, ai.REPLY_DRAFT, (provider, None, None, None))
    assert draft(p).status_code == 200
    assert seen and "9434765919" not in seen[0] and "jane@x.org" not in seen[0]


# ------------------------------------------------------------------ the Zoiko officer
def test_a_zoiko_officer_signs_a_customers_permit_from_the_platform(p):
    from tests.test_packs import call
    w = Platform()
    w.staff("officer", "R11")
    with tenant_transaction(p.clinic.ctx):
        made = ai.create_permit(feature_id=ai.REPLY_DRAFT, purpose="Draft replies for staff",
                                lane="A", level="A1", model_digest="a" * 64,
                                prompt_digest="b" * 64, created_by_ref=p.boss)
    r = call(w, "POST", f"platform/ai-permits/{p.clinic.tenant_public_id}/{made['id']}/sign",
             {}, "officer")
    assert r.status_code == 409                                  # not yet in release review
    with tenant_transaction(p.clinic.ctx):
        for state in ("QUALIFICATION", "DESIGN_CONTROL", "VALIDATION", "SANDBOX", "SHADOW",
                      "RELEASE_REVIEW"):
            ai.advance(made["id"], state, actor_ref=p.boss)
    r = call(w, "POST", f"platform/ai-permits/{p.clinic.tenant_public_id}/{made['id']}/sign",
             {}, "officer")
    assert r.status_code == 200 and json.loads(r.content)["permit"]["zoiko_signed"] is True
    listed = call(w, "GET", f"platform/ai-permits?customer={p.clinic.tenant_public_id}",
                  who="officer")
    assert json.loads(listed.content)["permits"][0]["zoiko_signed"] is True


def test_a_clinic_login_cannot_use_the_platform_signature(p):
    assert p.post(f"platform/ai-permits/{p.clinic.tenant_public_id}/x/sign", {},
                  subject="boss").status_code == 403
