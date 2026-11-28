"""Drafting a staff reply with Claude (Anthropic): the one outside model allowed (decided 27 Nov
2026, DEV-399). ONLY for drafting a reply that staff read, change and send themselves; never
clinical content, never sent by itself.

* Switched on only with AI_PROVIDER=anthropic and an unexpired permit signed by a clinic
  administrator AND a Zoiko platform officer (permits.py). Otherwise the fixed template answers.
* The text sent has had personal details removed (redact.py); nothing else about the patient is
  sent, and nothing of the text or the answer is stored (only fingerprints, runs.py).
* The API key comes only from the server's environment (ANTHROPIC_API_KEY), read by the SDK. It is
  never logged. A failure or a refusal gives an empty draft (quarantined), never an error text.
* The permit pins the model and this prompt by fingerprint: changing either needs a new permit.
"""
from __future__ import annotations

from django.conf import settings

from .redact import redact

DEFAULT_MODEL = "claude-opus-5-5"
SYSTEM_PROMPT = (
    "You draft short, polite replies for the front-desk staff of a healthcare clinic, answering a "
    "patient's message. A member of staff will read, change and send your draft. Rules: reply "
    "only about practical matters (appointments, opening hours, forms, how to reach the clinic). "
    "Never give medical advice, a diagnosis, test interpretation or medication guidance: if the "
    "message asks for any, say a clinician will reply. If the message sounds urgent or like an "
    "emergency, tell the patient to call the clinic or emergency services now. Personal details "
    "have been replaced with placeholders such as [name] or [date]: keep them as placeholders. "
    "Write plain text, under 120 words, with no greeting name and no signature."
)
MAX_TOKENS = 16000


def model() -> str:
    return str(getattr(settings, "AI_MODEL", "") or DEFAULT_MODEL)


def draft_reply(text: str) -> str:
    """The draft, or "" when Claude could not or would not answer."""
    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model(),
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            # If a safety classifier declines, the API retries on its fallback model itself.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": redact(text)}],
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError):
        return ""
    if response.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in response.content if b.type == "text").strip()
