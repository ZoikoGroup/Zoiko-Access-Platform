"""Secrets never reach the logs (GHE-20 B-05, DEV-186)."""
import io
import logging
from typing import Any

import pytest
from django.conf import settings

from foundation.log_redaction import REDACTED, SecretRedactionFilter, redact


@pytest.mark.parametrize("line", [
    "partner key pcsec_abcDEF123456_-xyz was rotated",
    "webhook secret whsec_AbCdEfGh12345678 shown once",
    "Authorization: Bearer abc123.def456-ghi789",
    "header was Bearer dev:tenantid:idp:someone@example.org",
    "Zoiko-Signature: t=1700000000,kid=k,v1=" + "a" * 64,
    "stored zf1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA value",
    "login failed password=Hunter2!ok for ravi",
    'payload {"password": "Hunter2!ok", "name": "Ravi"}',
    "api_key=abcd1234 was used", "token: abcd1234efgh", "client_secret = s3cretvalue",
])
def test_a_secret_is_replaced_whatever_the_line_around_it(line):
    out = redact(line)
    assert REDACTED in out
    for leaked in ("abcDEF123456", "AbCdEfGh12345678", "abc123.def456", "idp:someone",
                   "a" * 32, "AAAAAAAAAAAAAAAA", "Hunter2", "abcd1234", "s3cretvalue"):
        assert leaked not in out


@pytest.mark.parametrize("line", [
    "booking confirmed for 2027-07-01 09:00", "request GET /api/v1/bookings took 12 ms",
    "user ravi@example.org signed in", "password reset email queued", "token expired",
    "the secret to good service is listening",
])
def test_ordinary_lines_are_left_alone(line):
    assert redact(line) == line


def test_the_words_alone_do_not_trigger_it_only_a_value_after_them():
    assert redact("password: ") == "password: "
    assert redact("a password is required") == "a password is required"


def logged(level_message, *args, exc=False):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log = logging.getLogger("test.redaction")
    log.handlers, log.propagate = [handler], False
    log.setLevel(logging.DEBUG)
    try:
        if exc:
            try:
                raise ValueError("bad password=Hunter2!ok in the request")
            except ValueError:
                log.exception(level_message, *args)
        else:
            log.warning(level_message, *args)
    finally:
        log.handlers = []
    return stream.getvalue()


def test_the_filter_cleans_a_message_and_its_arguments():
    out = logged("rotated %s for %s", "pcsec_abcDEF123456_-xyz", "ravi")
    assert "abcDEF123456" not in out and REDACTED in out and "ravi" in out


def test_the_filter_cleans_exception_text_too():
    out = logged("something failed", exc=True)
    assert "Hunter2" not in out and "ValueError" in out and REDACTED in out


def test_the_filter_never_drops_a_record_or_breaks_on_odd_input():
    flt = SecretRedactionFilter()
    odd = logging.LogRecord("x", logging.INFO, __file__, 1, "%d items", ("not a number",), None)
    assert flt.filter(odd) is True                      # a bad format must not raise
    assert "withheld" in odd.msg
    plain = logging.LogRecord("x", logging.INFO, __file__, 1, "all fine", (), None)
    assert flt.filter(plain) is True and plain.getMessage() == "all fine"


def test_the_console_handler_is_configured_with_the_filter():
    conf: dict[str, Any] = settings.LOGGING
    handler = conf["handlers"]["console"]
    assert "secrets" in handler["filters"]
    wanted = "foundation.log_redaction.SecretRedactionFilter"
    assert conf["filters"]["secrets"]["()"] == wanted
