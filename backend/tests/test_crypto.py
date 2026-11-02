import base64
import uuid

import pytest

from foundation import crypto

TENANT = uuid.UUID("01a19a3a-9deb-714a-bf21-886b1a54b854")
OTHER = uuid.UUID("01a19a3a-9deb-714a-bf21-886b1a54b855")
CTX = "person:abc:legal_name"


def key(byte: bytes) -> str:
    return base64.b64encode(byte * 32).decode()


def test_a_value_survives_a_round_trip_including_non_english_text():
    for text in ("Asha Rao", "अशा राव", "José Müller-O'Brien", "  spaced  "):
        token = crypto.encrypt(text, tenant_id=TENANT, context=CTX)
        assert crypto.decrypt(token, tenant_id=TENANT, context=CTX) == text


def test_the_token_shows_nothing_and_differs_every_time():
    a = crypto.encrypt("Asha Rao", tenant_id=TENANT, context=CTX)
    b = crypto.encrypt("Asha Rao", tenant_id=TENANT, context=CTX)
    assert a != b                                              # a fresh nonce each time
    assert a.startswith("zf1:t1:") and "Asha" not in a and "Rao" not in a


def test_another_customer_cannot_read_it():
    token = crypto.encrypt("Asha Rao", tenant_id=TENANT, context=CTX)
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token, tenant_id=OTHER, context=CTX)


def test_it_cannot_be_moved_to_another_row_or_field():
    token = crypto.encrypt("Asha Rao", tenant_id=TENANT, context="person:abc:legal_name")
    for wrong in ("person:xyz:legal_name", "person:abc:phone", "booking:abc:legal_name"):
        with pytest.raises(crypto.CryptoError):
            crypto.decrypt(token, tenant_id=TENANT, context=wrong)


def test_a_changed_token_is_refused_and_the_error_says_nothing():
    token = crypto.encrypt("Asha Rao", tenant_id=TENANT, context=CTX)
    prefix, key_id, body = token.split(":", 2)
    flipped = body[:-4] + ("AAAA" if body[-4:] != "AAAA" else "BBBB")
    for bad in (f"{prefix}:{key_id}:{flipped}", f"{prefix}:{key_id}:", "zf1:t1", "nonsense",
                f"zf2:{key_id}:{body}", f"{prefix}:nokey:{body}", ""):
        with pytest.raises(crypto.CryptoError) as err:
            crypto.decrypt(bad, tenant_id=TENANT, context=CTX)
        assert str(err.value) == "" and "Asha" not in repr(err.value)


def test_each_customer_gets_its_own_key():
    a = crypto._tenant_key(b"m" * 32, TENANT)
    b = crypto._tenant_key(b"m" * 32, OTHER)
    assert a != b and len(a) == 32 and a != b"m" * 32          # derived, never the master itself


def test_a_key_can_be_rotated_old_values_still_open_and_are_flagged(settings):
    old = crypto.encrypt("Asha Rao", tenant_id=TENANT, context=CTX)          # sealed with t1
    settings.FIELD_KEYS = f"t1:{key(b'A')},t2:{key(b'B')}"
    settings.FIELD_KEY_ID = "t2"
    assert crypto.current_key_id() == "t2"
    assert crypto.decrypt(old, tenant_id=TENANT, context=CTX) == "Asha Rao"   # t1 still readable
    assert crypto.needs_rotation(old) is True
    new = crypto.encrypt("Asha Rao", tenant_id=TENANT, context=CTX)
    assert new.startswith("zf1:t2:") and crypto.needs_rotation(new) is False
    settings.FIELD_KEYS = f"t2:{key(b'B')}"                                    # t1 removed
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(old, tenant_id=TENANT, context=CTX)


def test_the_default_key_is_the_first_listed(settings):
    settings.FIELD_KEYS = f"a1:{key(b'A')},b1:{key(b'B')}"
    settings.FIELD_KEY_ID = ""
    assert crypto.current_key_id() == "a1"


@pytest.mark.parametrize("raw", [
    "", "k1", "k1:", "k1:not base64!!", f"k1:{base64.b64encode(b'short').decode()}",
    f"bad-id:{base64.b64encode(b'x' * 32).decode()}",
])
def test_a_bad_key_setting_is_rejected(raw, settings):
    with pytest.raises(ValueError):
        crypto.parse_keys(raw)
    settings.FIELD_KEYS = raw
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("x", tenant_id=TENANT, context=CTX)


def test_choosing_a_key_that_is_not_configured_stops_encryption(settings):
    settings.FIELD_KEY_ID = "gone"
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("x", tenant_id=TENANT, context=CTX)
