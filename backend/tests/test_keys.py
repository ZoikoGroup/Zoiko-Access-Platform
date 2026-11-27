"""Where the field-encryption keys live and how a lost key is recovered (foundation/keys.py).

None of these tests needs a database: they exercise the key store, the Shamir recovery split, the
management command and the release check."""
import base64
import io
import os
import secrets
import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from foundation import crypto, keys
from foundation.crypto import CryptoError
from foundation.release_check import release_problems
from tests.test_release_check import good

TENANT = uuid.uuid4()
CONTEXT = "person:abc:legal_name"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


@pytest.fixture(autouse=True)
def fresh_cache():
    keys.reset_cache()
    yield
    keys.reset_cache()


@pytest.fixture
def kek():
    return secrets.token_bytes(32)


def seal(value="Asha Rao"):
    return crypto.encrypt(value, tenant_id=TENANT, context=CONTEXT)


def opens(token) -> bool:
    try:
        return crypto.decrypt(token, tenant_id=TENANT, context=CONTEXT) is not None
    except CryptoError:
        return False


def ring(*ids):
    return keys.KeyRing({i: secrets.token_bytes(32) for i in ids}, ids[-1], "file")


def use_file(settings, tmp_path, key_ring, *, kek=None, provider="file"):
    path = str(tmp_path / "ring.json")
    keys.write_ring_file(path, key_ring, kek=kek)
    settings.FIELD_KEY_PROVIDER = provider
    settings.FIELD_KEY_FILE = path
    settings.FIELD_KEY_ID = ""
    if kek is not None:
        settings.KEY_WRAP_KEK = b64(kek)
    return path


# ------------------------------------------------------------------ the env provider (as before)
def test_the_environment_provider_still_works_and_reports_itself():
    info = keys.describe()
    assert info["ok"] and info["provider"] == "env" and info["current"] == "t1"
    assert [k["id"] for k in info["keys"]] == ["t1"]
    assert any("environment variable" in w for w in info["warnings"])
    assert opens(seal())


def test_status_shows_a_fingerprint_never_the_key(settings):
    raw = secrets.token_bytes(32)
    settings.FIELD_KEYS = f"k1:{base64.b64encode(raw).decode()}"
    settings.FIELD_KEY_ID = "k1"
    info = keys.describe()
    text = repr(info)
    assert info["keys"][0]["fingerprint"] == keys.fingerprint(raw)
    assert len(info["keys"][0]["fingerprint"]) == 8
    assert base64.b64encode(raw).decode() not in text and raw.hex() not in text


def test_a_bad_environment_value_is_refused_without_detail(settings):
    settings.FIELD_KEYS = "not-a-key"
    assert keys.describe()["ok"] is False
    with pytest.raises(CryptoError):
        seal()


# ------------------------------------------------------------------ the key file provider
def test_a_key_ring_file_supplies_the_keys(settings, tmp_path):
    use_file(settings, tmp_path, ring("k1", "k2"))
    info = keys.describe()
    assert info["provider"] == "file" and info["current"] == "k2"
    token = seal()
    assert token.startswith("zf1:k2:") and opens(token)


@pytest.mark.skipif(os.name != "posix", reason="file permission bits are a Linux idea")
def test_a_file_readable_by_other_users_is_refused(settings, tmp_path):
    path = use_file(settings, tmp_path, ring("k1"))
    os.chmod(path, 0o644)
    assert keys.describe()["ok"] is False
    with pytest.raises(CryptoError):
        seal()
    os.chmod(path, 0o600)
    keys.reset_cache()
    assert keys.describe()["ok"] is True


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"keys": {}}', '{"keys": {"a": 5}}',
                                     '{"keys": {"a": "AAAA"}}'])
def test_a_damaged_or_empty_key_file_is_refused(settings, tmp_path, content):
    path = use_file(settings, tmp_path, ring("k1"))
    with open(path, "w", encoding="utf8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    assert keys.describe()["ok"] is False


def test_a_missing_file_or_path_is_refused(settings, tmp_path):
    settings.FIELD_KEY_PROVIDER = "file"
    settings.FIELD_KEY_FILE = ""
    assert keys.describe()["ok"] is False
    settings.FIELD_KEY_FILE = str(tmp_path / "nope.json")
    assert keys.describe()["ok"] is False


def test_a_current_key_id_that_is_not_in_the_file_is_refused(settings, tmp_path):
    use_file(settings, tmp_path, ring("k1", "k2"))
    settings.FIELD_KEY_ID = "k9"
    assert keys.describe()["ok"] is False


def test_an_unknown_provider_is_refused(settings):
    settings.FIELD_KEY_PROVIDER = "cloud"
    assert keys.describe()["ok"] is False


def test_a_changed_file_is_picked_up_within_a_minute(settings, tmp_path, monkeypatch):
    first = ring("k1")
    path = use_file(settings, tmp_path, first)
    assert keys.load_ring().current == "k1"
    keys.write_ring_file(path, keys.new_key_ring("k2", first.keys))
    assert keys.load_ring().current == "k1"                     # cached: not asked for every value
    now = [keys.time.monotonic()]
    monkeypatch.setattr(keys.time, "monotonic", lambda: now[0] + keys.REFRESH_SECONDS + 1)
    assert keys.load_ring().current == "k2"


# ------------------------------------------------------------------ rotation to a new key
def test_adding_a_key_keeps_old_values_readable_and_encrypts_new_ones_with_it(settings, tmp_path):
    first = ring("k1")
    path = use_file(settings, tmp_path, first)
    old = seal()
    keys.write_ring_file(path, keys.new_key_ring("k2", first.keys))
    keys.reset_cache()
    new = seal()
    assert old.startswith("zf1:k1:") and new.startswith("zf1:k2:")
    assert opens(old) and opens(new) and crypto.needs_rotation(old)
    moved = crypto.reencrypt(old, tenant_id=TENANT, context=CONTEXT)
    assert moved.startswith("zf1:k2:") and opens(moved)


def test_a_new_key_needs_a_new_id_made_of_plain_characters():
    base = ring("k1")
    with pytest.raises(ValueError):
        keys.new_key_ring("k1", base.keys)
    with pytest.raises(ValueError):
        keys.new_key_ring("bad id!", base.keys)
    assert keys.new_key_ring("k-2_b", base.keys).current == "k-2_b"


# ------------------------------------------------------------------ wrapped keys (a key service)
def test_wrapped_keys_are_unlocked_by_the_key_service_and_open_values(settings, tmp_path, kek):
    use_file(settings, tmp_path, ring("k1", "k2"), kek=kek, provider="wrapped")
    assert keys.describe()["provider"] == "wrapped"
    assert opens(seal())


def test_the_wrapped_file_alone_reveals_no_key(settings, tmp_path, kek):
    plain = ring("k1")
    path = use_file(settings, tmp_path, plain, kek=kek, provider="wrapped")
    content = open(path, encoding="utf8").read()
    assert b64(plain.keys["k1"]) not in content
    settings.KEY_WRAP_KEK = ""                      # the service is unreachable or the KEK is gone
    keys.reset_cache()
    assert keys.describe()["ok"] is False
    with pytest.raises(CryptoError):
        seal()


def test_the_wrong_key_encryption_key_opens_nothing(settings, tmp_path, kek):
    use_file(settings, tmp_path, ring("k1"), kek=kek, provider="wrapped")
    settings.KEY_WRAP_KEK = b64(secrets.token_bytes(32))
    keys.reset_cache()
    assert keys.describe()["ok"] is False


def test_a_wrapped_key_is_bound_to_its_key_id(settings, tmp_path, kek):
    path = use_file(settings, tmp_path, ring("k1", "k2"), kek=kek, provider="wrapped")
    import json
    data = json.loads(open(path, encoding="utf8").read())
    data["keys"]["k1"], data["keys"]["k2"] = data["keys"]["k2"], data["keys"]["k1"]
    with open(path, "w", encoding="utf8") as handle:
        json.dump(data, handle)
    os.chmod(path, 0o600)
    keys.reset_cache()
    assert keys.describe()["ok"] is False                       # k1's slot no longer holds k1's key


def test_a_real_key_service_can_plug_in_its_own_unwrapper(settings, tmp_path, kek):
    path = use_file(settings, tmp_path, ring("k1"), kek=kek, provider="wrapped")
    asked = []

    def service(key_id, wrapped):
        asked.append(key_id)
        return keys._unwrap_with_env_kek(key_id, wrapped)

    keys.register_unwrapper("test-service", service)
    settings.KEY_UNWRAPPER = "test-service"
    keys.reset_cache()
    assert opens(seal()) and asked == ["k1"] and os.path.exists(path)
    settings.KEY_UNWRAPPER = "nobody"
    keys.reset_cache()
    assert keys.describe()["ok"] is False


# ------------------------------------------------------------------ recovery: splitting a key ring
def test_any_threshold_of_the_shares_rebuild_the_secret_and_fewer_do_not():
    secret = secrets.token_bytes(96)
    shares = keys.split_secret(secret, threshold=3, shares=5)
    assert len(shares) == 5 and len(set(shares)) == 5
    import itertools
    for subset in itertools.combinations(shares, 3):
        assert keys.combine_shares(list(subset)) == secret
    assert keys.combine_shares(shares) == secret                # more than needed is fine
    for subset in itertools.combinations(shares, 2):
        with pytest.raises(keys.KeyRingError):
            keys.combine_shares(list(subset))


def test_a_share_alone_does_not_contain_the_secret():
    secret = b"a very secret ring of keys " * 3
    for share in keys.split_secret(secret, threshold=2, shares=3):
        assert secret.decode() not in share
        assert b64(secret) not in share


def test_the_same_share_twice_does_not_count_as_two():
    shares = keys.split_secret(b"x" * 40, threshold=3, shares=4)
    with pytest.raises(keys.KeyRingError):
        keys.combine_shares([shares[0], shares[0], shares[1]])


def test_shares_from_different_splits_cannot_be_mixed():
    a = keys.split_secret(b"first secret value" * 2, threshold=2, shares=3)
    b = keys.split_secret(b"first secret value" * 2, threshold=2, shares=3)
    with pytest.raises(keys.KeyRingError):
        keys.combine_shares([a[0], b[1]])


def test_a_damaged_share_is_caught_not_silently_wrong():
    shares = keys.split_secret(b"keep me safe" * 4, threshold=2, shares=3)
    prefix, tail = shares[0].rsplit(".", 1)
    raw = bytearray(base64.urlsafe_b64decode(tail + "=" * (-len(tail) % 4)))
    raw[0] ^= 0x01
    broken = f"{prefix}.{base64.urlsafe_b64encode(bytes(raw)).decode()}"
    with pytest.raises(keys.KeyRingError):
        keys.combine_shares([broken, shares[1]])
    for junk in ("", "zk1.2.1", "nonsense", "zk1.x.y.z.w"):
        with pytest.raises(keys.KeyRingError):
            keys.combine_shares([junk, shares[1]])
    with pytest.raises(keys.KeyRingError):
        keys.combine_shares([])


@pytest.mark.parametrize("threshold,shares", [(1, 3), (4, 3), (2, 17), (0, 0)])
def test_nonsense_split_sizes_are_refused(threshold, shares):
    with pytest.raises(ValueError):
        keys.split_secret(b"abc", threshold=threshold, shares=shares)
    with pytest.raises(ValueError):
        keys.split_secret(b"", threshold=2, shares=3)


def test_a_lost_key_ring_is_rebuilt_from_shares_and_opens_the_old_values(settings, tmp_path):
    original = ring("k1", "k2")
    use_file(settings, tmp_path, original)
    token = seal("Priya Menon")
    shares = keys.split_secret(keys.ring_to_json(original).encode(), threshold=3, shares=5)
    # the ring is lost: the file is gone and nothing can open the value
    settings.FIELD_KEY_FILE = str(tmp_path / "lost.json")
    keys.reset_cache()
    assert opens(token) is False
    rebuilt = keys.ring_from_json(keys.combine_shares([shares[4], shares[1], shares[2]]).decode())
    assert rebuilt.keys == original.keys and rebuilt.current == "k2"
    use_file(settings, tmp_path, rebuilt)
    keys.reset_cache()
    assert crypto.decrypt(token, tenant_id=TENANT, context=CONTEXT) == "Priya Menon"


# ------------------------------------------------------------------ the command
def run(*args, **kw):
    out = io.StringIO()
    call_command("field_keys", *args, stdout=out, **kw)
    return out.getvalue()


def test_the_status_command_lists_keys_and_warnings_but_no_key():
    text = run("status")
    assert "provider: env" in text and "t1" in text and "warning" in text
    assert "QUFBQUFB" not in text                               # the configured key material


def test_the_command_makes_wraps_splits_and_restores_a_key_ring(settings, tmp_path, kek):
    plain = str(tmp_path / "plain.json")
    wrapped = str(tmp_path / "wrapped.json")
    settings.KEY_WRAP_KEK = b64(kek)
    out = run("new", "k2", "--file", plain)
    assert "current is k2" in out
    if os.name == "posix":
        assert os.stat(plain).st_mode & 0o077 == 0              # only its owner may read it
    run("wrap", "--from", plain, "--file", wrapped)
    settings.FIELD_KEY_PROVIDER, settings.FIELD_KEY_FILE = "wrapped", wrapped
    settings.FIELD_KEY_ID = ""
    keys.reset_cache()
    assert keys.describe()["ok"] and keys.describe()["current"] == "k2"
    lines = [ln for ln in run("split", "--shares", "4", "--threshold", "2").splitlines()
             if ln.startswith("zk1.")]
    assert len(lines) == 4
    restored = str(tmp_path / "restored.json")
    run("combine", lines[3], lines[0], "--file", restored)
    assert keys.ring_from_json(open(restored, encoding="utf8").read()).current == "k2"
    with pytest.raises(CommandError):
        run("combine", lines[3], lines[0], "--file", restored)   # never overwrites a file
    with pytest.raises(CommandError):
        run("combine", lines[3], "--file", str(tmp_path / "two.json"))


def test_the_command_refuses_bad_input(tmp_path):
    with pytest.raises(CommandError):
        run("new")
    with pytest.raises(CommandError):
        run("new", "bad id!", "--file", str(tmp_path / "x.json"))
    with pytest.raises(CommandError):
        run("wrap")
    with pytest.raises(CommandError):
        run("combine", "junk", "--file", str(tmp_path / "y.json"))


# ------------------------------------------------------------------ the release check
def test_keys_in_an_environment_variable_are_flagged_unless_knowingly_accepted():
    assert any("environment variable" in p for p in release_problems(good(ALLOW_ENV_KEYS=False)))
    assert not any("environment variable" in p for p in release_problems(good()))


def test_an_unknown_provider_and_an_unloadable_ring_are_flagged(settings, tmp_path):
    assert any("ZOIKO_FIELD_KEY_PROVIDER" in p
               for p in release_problems(good(FIELD_KEY_PROVIDER="cloud")))
    settings.FIELD_KEY_PROVIDER, settings.FIELD_KEY_FILE = "file", str(tmp_path / "absent.json")
    assert any("key ring cannot be loaded" in p
               for p in release_problems(good(FIELD_KEY_PROVIDER="file")))


def test_a_good_key_file_clears_the_key_problems(settings, tmp_path):
    use_file(settings, tmp_path, ring("k1"))
    found = release_problems(good(FIELD_KEY_PROVIDER="file", ALLOW_ENV_KEYS=False))
    assert not any("key" in p.lower() and "ring" in p.lower() for p in found)
    assert not any("environment variable" in p for p in found)
