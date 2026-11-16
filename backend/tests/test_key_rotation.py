"""Rotating the field-encryption key: add a new key, make it current, run the command, and every
stored value moves onto it, so the old key can be retired. DEV-122."""
import base64
import io
import json

import pytest
from django.core.management import call_command

from foundation.cell_context import tenant_transaction
from foundation.crypto import CryptoError, decrypt, encrypt, needs_rotation, reencrypt
from modules.coverage.models import Coverage
from modules.messaging.models import Message, Thread
from modules.trust import api as trust
from modules.trust.models import ActorName, PersonProfile
from tests.test_clinician import Practice
from tests.test_coverage import add as add_coverage
from tests.test_profile import save as save_profile

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

NEW_KEY = base64.b64encode(b"B" * 32).decode()


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def stored_data(practice: Practice) -> None:
    """One value of each kind, all sealed with the current (old) key."""
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
        trust.set_display_name(practice.doc, "Dr Asha Rao")
    assert save_profile(practice, practice.person, subject="ops").status_code == 200
    assert practice.post("messages/threads", {"subject": "A question", "body": "Hello there"},
                         subject="pat").status_code == 201
    assert add_coverage(practice).status_code == 201


def tokens(practice: Practice) -> list[str]:
    """This practice's six stored values (the test database user ignores row-level security, so
    each lookup names the tenant itself)."""
    mine = {"tenant_id": practice.clinic.ctx.tenant_id}
    with tenant_transaction(practice.clinic.ctx):
        found = [PersonProfile.objects.get(**mine).legal_name_enc,
                 PersonProfile.objects.get(**mine).phone_enc,
                 ActorName.objects.get(**mine).name_enc, Thread.objects.get(**mine).subject_enc,
                 Message.objects.get(**mine).body_enc, Coverage.objects.get(**mine).member_ref_enc]
    return found


def start_rotation(settings):
    old = settings.FIELD_KEYS
    settings.FIELD_KEYS = f"{old},t2:{NEW_KEY}"
    settings.FIELD_KEY_ID = "t2"
    return old


def run(*args) -> str:
    out = io.StringIO()
    call_command("rotate_field_keys", *args, stdout=out)
    return out.getvalue()


# ------------------------------------------------------------------ one value
def test_reencrypting_keeps_the_text_and_moves_it_to_the_current_key(settings):
    ctx = {"tenant_id": "11111111-1111-1111-1111-111111111111", "context": "person:abc:legal_name"}
    old = encrypt("Asha Rao", **ctx)
    start_rotation(settings)
    assert needs_rotation(old) and old.startswith("zf1:t1:")
    fresh = reencrypt(old, **ctx)
    assert fresh.startswith("zf1:t2:") and decrypt(fresh, **ctx) == "Asha Rao"
    assert reencrypt(fresh, **ctx) == fresh                  # already current: untouched


def test_reencrypting_refuses_a_value_in_the_wrong_place(settings):
    ctx = {"tenant_id": "11111111-1111-1111-1111-111111111111", "context": "person:abc:legal_name"}
    old = encrypt("Asha Rao", **ctx)
    start_rotation(settings)
    with pytest.raises(CryptoError):
        reencrypt(old, tenant_id=ctx["tenant_id"], context="person:other:legal_name")


# ------------------------------------------------------------------ the command
def test_a_dry_run_counts_and_changes_nothing(settings):
    p = Practice()
    stored_data(p)
    before = tokens(p)
    start_rotation(settings)
    out = run("--dry-run")
    assert "dry run" in out and "would move" in out
    assert tokens(p) == before                               # nothing changed


def test_the_command_moves_every_kind_of_value_and_the_old_key_can_then_be_retired(settings):
    p = Practice()
    stored_data(p)
    assert all(t.startswith("zf1:t1:") for t in tokens(p))
    old = start_rotation(settings)
    out = run()
    assert "0 unreadable" in out
    assert all(t.startswith("zf1:t2:") for t in tokens(p))
    settings.FIELD_KEYS = f"t2:{NEW_KEY}"                    # the old key is retired
    assert old                                               # (only the new one is configured)
    shown = json.loads(p.get("profile", subject="pat").content)["profiles"]
    assert shown[0]["name"] == "Asha Rao"
    thread = json.loads(p.get("messages/threads", subject="pat").content)["threads"][0]
    opened = json.loads(p.get(f"messages/threads/{thread['id']}", subject="pat").content)
    assert opened["thread"]["messages"][0]["body"] == "Hello there"


def test_running_it_again_finds_nothing_left_to_do(settings):
    p = Practice()
    stored_data(p)
    start_rotation(settings)
    run()
    again = run()
    assert "Total: 0 moved, 0 unreadable" in again


def test_every_tenant_is_rotated(settings):
    first, second = Practice(), Practice()
    stored_data(first)
    stored_data(second)
    start_rotation(settings)
    run()
    assert all(t.startswith("zf1:t2:") for t in tokens(first))
    assert all(t.startswith("zf1:t2:") for t in tokens(second))


def test_a_damaged_value_is_counted_and_left_alone_while_the_rest_still_move(settings):
    p = Practice()
    stored_data(p)
    with tenant_transaction(p.clinic.ctx):
        Message.objects.update(body_enc="zf1:t1:AAAAAAAAAAAAAAAAAAAAAAAA")   # cannot be read
    start_rotation(settings)
    out = run()
    assert "1 unreadable" in out
    got = tokens(p)
    assert got[4] == "zf1:t1:AAAAAAAAAAAAAAAAAAAAAAAA"        # the damaged one is untouched
    assert all(t.startswith("zf1:t2:") for i, t in enumerate(got) if i != 4)


def test_nothing_readable_appears_in_the_command_output(settings):
    p = Practice()
    stored_data(p)
    start_rotation(settings)
    out = run()
    for secret in ("Asha", "Hello there", "Dr Asha", "STAR-12345", "98765"):
        assert secret not in out
