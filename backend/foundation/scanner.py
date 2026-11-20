"""Virus scanning of files people upload (DEV-345). Nothing uploaded is opened until scanned clean.

Scanners (setting `ATTACHMENT_SCANNER`):
  "clamav"  - a ClamAV daemon (clamd) over TCP, INSTREAM command; `CLAMAV_HOST`, `CLAMAV_PORT`.
  "pretend" - DEVELOPMENT ONLY: finds the standard EICAR test file and nothing else.
  ""        - no scanner: every file stays "pending" and can never be opened.
A scanner that cannot be reached leaves the file pending, to be tried again by the worker; it is
never treated as clean.
"""

from __future__ import annotations

import socket
import struct
from typing import Literal

from django.conf import settings

Verdict = Literal["clean", "infected", "pending"]
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
_CHUNK = 64 * 1024


def _clamav(data: bytes) -> Verdict:
    host = getattr(settings, "CLAMAV_HOST", "")
    port = int(getattr(settings, "CLAMAV_PORT", 3310) or 3310)
    if not host:
        return "pending"
    try:
        with socket.create_connection((host, port), timeout=15) as conn:
            conn.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), _CHUNK):
                part = data[i:i + _CHUNK]
                conn.sendall(struct.pack("!L", len(part)) + part)
            conn.sendall(struct.pack("!L", 0))
            answer = conn.recv(4096).strip(b"\0").decode("utf-8", "replace")
    except OSError:
        return "pending"  # unreachable: try again later, never "clean"
    if answer.endswith("OK"):
        return "clean"
    if "FOUND" in answer:
        return "infected"
    return "pending"


def _pretend(data: bytes) -> Verdict:
    return "infected" if EICAR in data else "clean"


def scan(data: bytes) -> Verdict:
    name = getattr(settings, "ATTACHMENT_SCANNER", "")
    if name == "clamav":
        return _clamav(data)
    if name == "pretend" and getattr(settings, "DEBUG", False):
        return _pretend(data)
    return "pending"


def scanner_name() -> str:
    return getattr(settings, "ATTACHMENT_SCANNER", "") or "none"
