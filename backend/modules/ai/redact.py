"""Removing personal details before any text leaves for an outside model (decided 27 Nov 2026,
DEV-399: "with personal details removed first").

What is replaced: the names we know (the patient's, the staff's), email addresses, phone numbers,
dates, long numbers (NHS, insurance, member and reference numbers) and UK postcodes. Each becomes a
plain placeholder such as [name] or [phone]. This is a safety net, not a guarantee: free text can
name people in ways no pattern catches, which is why only staff see a draft and only staff send.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)
_DATE = re.compile(
    r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+\d{4})?)\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{7,}\d(?![\w])")
_NUMBER = re.compile(r"\b\d{6,}\b")


def redact(text: str, names: Iterable[str] = ()) -> str:
    out = _EMAIL.sub("[email]", text or "")
    out = _URL.sub("[link]", out)
    out = _DATE.sub("[date]", out)
    out = _POSTCODE.sub("[postcode]", out)
    out = _PHONE.sub("[phone]", out)
    out = _NUMBER.sub("[number]", out)
    # Whole names first, then each part of two letters or more (a first or a family name alone).
    parts: set[str] = set()
    for name in names:
        clean = " ".join(str(name or "").split())
        if len(clean) >= 2:
            parts.add(clean)
            parts.update(p for p in clean.split(" ") if len(p) >= 2)
    for part in sorted(parts, key=len, reverse=True):
        out = re.sub(rf"(?<![\w]){re.escape(part)}(?![\w])", "[name]", out, flags=re.IGNORECASE)
    return out
