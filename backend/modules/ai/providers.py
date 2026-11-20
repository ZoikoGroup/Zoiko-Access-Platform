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
    register_provider(REPLY_DRAFT, reply_draft)
