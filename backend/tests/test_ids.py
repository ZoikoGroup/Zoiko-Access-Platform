import re
import time

from foundation.ids import new_internal_id, new_public_id


def test_internal_id_is_uuid_version_7_rfc_variant():
    value = new_internal_id()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"  # Python's name for the RFC 9562 variant


def test_internal_id_embeds_current_time_in_milliseconds():
    before = int(time.time() * 1000)
    value = new_internal_id()
    after = int(time.time() * 1000)
    embedded_ms = value.int >> 80
    assert before <= embedded_ms <= after


def test_internal_ids_created_in_different_milliseconds_sort_by_time():
    first = new_internal_id()
    time.sleep(0.005)
    second = new_internal_id()
    assert first < second


def test_internal_ids_are_unique():
    ids = {new_internal_id() for _ in range(10_000)}
    assert len(ids) == 10_000


def test_public_id_is_url_safe_and_long_enough():
    value = new_public_id()
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", value)  # 16 bytes -> 22 chars of base64url


def test_public_ids_are_unique_and_carry_no_time():
    # Two ids made back-to-back must not share a prefix the way time-based ids would.
    ids = [new_public_id() for _ in range(10_000)]
    assert len(set(ids)) == 10_000
    assert len({i[:4] for i in ids}) > 5_000  # first characters vary: no timestamp prefix
