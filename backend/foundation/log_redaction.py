"""Keep secrets out of the logs (GHE-20 B-05, DEV-186).

A log line is read by many people and kept for a long time, so a secret that reaches one is a leak.
Nobody should log one on purpose; this filter is the safety net for the accident. It rewrites the
message and any exception text before any handler sees it, replacing what looks like a secret with
`[redacted]`. It knows the secret shapes this product issues (partner and webhook secrets, bearer
tokens, signatures, sealed field values) and the common `password=...` pattern. It cannot recognise
a secret of a shape it has never seen, so it is a net and not a licence to log them.
"""
from __future__ import annotations

import logging
import re

REDACTED = "[redacted]"

# Whole tokens that are secret by their shape.
_TOKENS = re.compile(
    r"(?:pcsec_|whsec_)[A-Za-z0-9_\-]{8,}"           # partner and webhook signing secrets
    r"|zf\d+:[A-Za-z0-9_\-+/=:.]{10,}"                # a sealed field value (ciphertext)
    r"|v1=[0-9a-fA-F]{32,}"                           # the signature inside Zoiko-Signature
)
# A bearer credential in a header: "Authorization: Bearer <token>" (dev tokens contain colons).
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=:\-]{6,}")
# key=value, key: value and "key": "value" for words that name a secret.
_PAIR = re.compile(
    r"(?i)(\b[\w-]*(?:password|passwd|pwd|secret|token|api[_-]?key|authorization)[\w-]*"
    r"[\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[redacted\])[^\s\"',;&})]{3,}"
)


def redact(text: str) -> str:
    """The text with every recognised secret replaced by `[redacted]`."""
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _PAIR.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return _TOKENS.sub(REDACTED, text)


class SecretRedactionFilter(logging.Filter):
    """Rewrites the message and any exception text on every record. It never drops a record and
    never raises: a logging failure must not become an application failure."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message or record.args:
                record.msg, record.args = cleaned, ()
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:  # noqa: BLE001 - see the docstring
            record.msg, record.args = "[a log line could not be checked and was withheld]", ()
            record.exc_info = record.exc_text = None
        return True
