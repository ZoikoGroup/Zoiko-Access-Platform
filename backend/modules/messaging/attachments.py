"""Files attached to a message (GHE-16).

A patient or the clinic may attach up to three small files to a message. What is accepted is
kept deliberately narrow: a PDF, a PNG or JPEG picture, or a plain text file, each at most 1 MB.
The kind of file is read from the file's own first bytes, never from the name or the type the
sender claims, and the name must agree with it. Every file is passed through a scanner before
anything is stored; a file the scanner refuses makes the whole message refused. The file is stored
encrypted for this customer,
only its sender's side and the other side of THIS conversation can open it, and it is always
downloaded, never shown inside the page (see the download headers in the API layer).

The built-in scanner only recognises the standard anti-virus test file. A real scanner registers
itself with `register_scanner`; until one does, the product says plainly that files are checked for
type and size but not for viruses (see DEV-349).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Callable
from typing import Any

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError

from .models import Message, MessageAttachment

MAX_FILES = 3
MAX_BYTES = 1_000_000
MAX_NAME = 100

# kind of file -> the endings its name may have
ALLOWED: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "text/plain": (".txt",),
}
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]")

Scanner = Callable[[bytes], bool]            # True means the file is clean


def _default_scan(data: bytes) -> bool:
    return EICAR not in data


_scanner: Scanner = _default_scan


def register_scanner(scanner: Scanner) -> None:
    """Use this scanner (a real anti-virus adapter) instead of the built-in one."""
    global _scanner
    _scanner = scanner


def reset_scanner() -> None:
    global _scanner
    _scanner = _default_scan


def _bad() -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["attachments"])


def sniff(data: bytes) -> str | None:
    """The kind of file these bytes really are, or None when it is not an allowed kind."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if data and all(ch >= " " or ch in "\n\r\t" for ch in text):
        return "text/plain"
    return None


def clean_name(name: Any, content_type: str) -> str:
    """A safe file name: no folders, no odd characters, and an ending that matches the file."""
    if not isinstance(name, str):
        raise _bad()
    base = re.split(r"[\\/]", name.strip())[-1]
    base = _NAME.sub("_", base).strip(" .")
    ending = next((e for e in ALLOWED[content_type] if base.lower().endswith(e)), "")
    if not base or not ending:
        raise _bad()
    if len(base) > MAX_NAME:                       # cut the start of the name, never its ending
        base = base[: MAX_NAME - len(ending)] + base[-len(ending):]
    return base


def prepare(files: Any) -> list[tuple[str, str, bytes]]:
    """Check what a sender attached: [{"name": "...", "data": "<base64>"}]. Returns (name, kind,
    bytes) for each, or refuses everything with VALIDATION_FAILED. Nothing is stored here."""
    if files is None or files == []:
        return []
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise _bad()
    out: list[tuple[str, str, bytes]] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("data"), str):
            raise _bad()
        if len(item["data"]) > MAX_BYTES * 4 // 3 + 8:           # cheap check before decoding
            raise _bad()
        try:
            data = base64.b64decode(item["data"], validate=True)
        except (binascii.Error, ValueError):
            raise _bad() from None
        if not data or len(data) > MAX_BYTES:
            raise _bad()
        kind = sniff(data)
        if kind is None:
            raise _bad()
        name = clean_name(item.get("name"), kind)
        if not _scanner(data):                          # a refused file refuses the message
            raise _bad()
        out.append((name, kind, data))
    return out


def _seal(data: bytes, public_id: str) -> str:
    ctx = require_context()
    return encrypt(base64.b64encode(data).decode(), tenant_id=ctx.tenant_id,
                   context=f"attachment:{public_id}:content")


def store(message: Message, prepared: list[tuple[str, str, bytes]], actor_ref: str) -> None:
    ctx = require_context()
    for name, kind, data in prepared:
        row = MessageAttachment(
            message=message, name=name, content_type=kind, size=len(data),
            digest=hashlib.sha256(data).hexdigest(), content_enc="", uploaded_by_ref=actor_ref,
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
        row.content_enc = _seal(data, row.public_id)
        row.save()


def attachment_dict(row: MessageAttachment) -> dict[str, Any]:
    return {"id": row.public_id, "name": row.name, "content_type": row.content_type,
            "size": row.size}


def for_messages(message_ids: list[Any]) -> dict[Any, list[dict[str, Any]]]:
    """The attachments of these messages, grouped by message (no content)."""
    ctx = require_context()
    out: dict[Any, list[dict[str, Any]]] = {}
    rows = MessageAttachment.objects.filter(
        message_id__in=message_ids, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
    ).exclude(content_enc="").order_by("created_at")      # erased files are not offered
    for row in rows:
        out.setdefault(row.message_id, []).append(attachment_dict(row))
    return out


def find(attachment_ref: str) -> dict[str, Any]:
    """Which conversation an attachment belongs to and what it is called (no content), so the
    caller can check the reader may open that conversation first."""
    ctx = require_context()
    row = MessageAttachment.objects.select_related("message__thread").filter(
        public_id=attachment_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return {**attachment_dict(row), "thread_ref": row.message.thread.public_id}


def content_of(attachment_ref: str) -> bytes:
    ctx = require_context()
    row = MessageAttachment.objects.filter(
        public_id=attachment_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    try:
        data = base64.b64decode(decrypt(
            row.content_enc, tenant_id=ctx.tenant_id,
            context=f"attachment:{row.public_id}:content"))
    except (CryptoError, binascii.Error, ValueError):
        raise ZoikoError(ErrorCode.NOT_FOUND) from None
    if hashlib.sha256(data).hexdigest() != row.digest:          # damaged or swapped: never serve it
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return data
