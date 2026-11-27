"""Identifier generators.

Every table gets two ids made here (the third, `ref_no`, needs a database sequence: later step):

  id         internal primary key. UUIDv7: time-ordered, so new rows append to the end of the
             index. It reveals roughly WHEN a row was created, so it never leaves the cell.
  public_id  the id used in URLs, APIs, events and links. Pure randomness, no time inside
             (GHE-13: public ids must not encode a timestamp, national id, MRN or name).
"""
import secrets
import time
import uuid


def new_internal_id() -> uuid.UUID:
    """UUIDv7 (RFC 9562): 48-bit unix time in ms | version | random bits | variant.

    Python's stdlib only gained uuid7 in 3.14, so we build it by hand.
    Trade-off: two ids made in the SAME millisecond are ordered by their random bits, not by
    creation order. That is fine for index locality; it is NOT a guarantee of strict order.
    Never use this id to decide "which happened first" (use the recorded_at timestamp).
    """
    unix_ms = time.time_ns() // 1_000_000
    value = (unix_ms & 0xFFFF_FFFF_FFFF) << 80      # 48 bits of time at the top
    value |= secrets.randbits(74)                   # 74 random bits (12 + 62)
    # Overwrite the 4 version bits and the 2 variant bits with their required values.
    value = (value & ~(0xF << 76)) | (0x7 << 76)    # version = 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)    # variant = RFC 9562 (binary 10)
    return uuid.UUID(int=value)


def new_public_id() -> str:
    """128 random bits as a URL-safe string (22 characters). Cryptographically random.

    `secrets` (not `random`) is used on purpose: `random` is predictable, and a guessable
    public id would let someone enumerate other people's records.
    """
    return secrets.token_urlsafe(16)
