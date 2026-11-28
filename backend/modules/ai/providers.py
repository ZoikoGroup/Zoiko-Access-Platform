"""The only "model" shipped today: a fixed template. It calls nothing outside this server and never
repeats what it was given, so no patient data can leave through it. A real model provider is a
decision for the lead (cost, contract, region, privacy); it would plug in through
`register_provider`.
"""

from __future__ import annotations

from .runs import register_provider

REPLY_DRAFT = "message.reply_draft"
_TEMPLATE = (
    "Thank you for your message. A member of our team has read it and will reply as soon as "
    "they can. If this is urgent, please call the clinic or emergency services."
)


def reply_draft(_input_text: str) -> str:
    return _TEMPLATE


def register_builtin() -> None:
    """The reply draft's provider: Claude when AI_PROVIDER is "anthropic" (DEV-399, pinned by the
    fingerprints of its model and prompt), otherwise the fixed template."""
    from django.conf import settings

    if getattr(settings, "AI_PROVIDER", "") == "anthropic":
        from . import claude
        from .runs import digest

        register_provider(REPLY_DRAFT, claude.draft_reply, model_digest=digest(claude.model()),
                          prompt_digest=digest(claude.SYSTEM_PROMPT))
        return
    register_provider(REPLY_DRAFT, reply_draft)
