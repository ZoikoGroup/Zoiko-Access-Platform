"""Files attached to a message: what is accepted, who may open it, and that it is always a download
(modules/messaging/attachments.py)."""
import base64
import hashlib
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import OutboxEvent
from modules.messaging import api as messaging
from modules.messaging import attachments as files
from modules.messaging.models import Message, MessageAttachment
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_messaging import opened, started, threads, write

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
TEXT = b"Results from my own blood pressure cuff: 120 over 80.\n"
EXE = b"MZ\x90\x00" + b"\x00" * 30


def b64(data):
    return base64.b64encode(data).decode()


def attach(name, data):
    return {"name": name, "data": b64(data)}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    from modules.comms import api as comms

    comms.register_subscribers()
    yield
    messaging.reset_scanner()


@pytest.fixture
def p():
    return Practice()


def refused(call, *args):
    with pytest.raises(ZoikoError) as caught:
        call(*args)
    return caught.value.code == ErrorCode.VALIDATION_FAILED


# ------------------------------------------------------------------ what is accepted (no database)
@pytest.mark.parametrize("data, kind", [
    (PDF, "application/pdf"), (PNG, "image/png"), (JPEG, "image/jpeg"), (TEXT, "text/plain"),
    (EXE, None), (b"\x00\x01\x02binary", None), (b"", None),
    (b"<html><script>x</script>", "text/plain"), (b"\xff\xfe invalid utf8 \xc3(", None),
])
def test_the_kind_of_a_file_is_read_from_its_bytes(data, kind):
    assert files.sniff(data) == kind


@pytest.mark.parametrize("name, kind, expected", [
    ("scan.pdf", "application/pdf", "scan.pdf"),
    ("../../etc/Report.PDF", "application/pdf", "Report.PDF"),
    ("C:\\Users\\me\\photo.JPG", "image/jpeg", "photo.JPG"),
    ("my photo (1).jpeg", "image/jpeg", "my photo (1).jpeg"),
    ("a<b>c|d.png", "image/png", "a_b_c_d.png"),
    ("notes.txt", "text/plain", "notes.txt"),
])
def test_a_name_is_made_safe_and_must_match_the_kind(name, kind, expected):
    assert files.clean_name(name, kind) == expected


@pytest.mark.parametrize("name, kind", [
    ("scan.png", "application/pdf"), ("photo.pdf", "image/png"), ("noending", "text/plain"),
    ("", "text/plain"), ("   ", "application/pdf"), (".pdf", "application/pdf"),
    (None, "text/plain"), (5, "text/plain"), ("evil.pdf.exe", "application/pdf"),
])
def test_a_name_that_does_not_match_the_file_is_refused(name, kind):
    assert refused(files.clean_name, name, kind)


def test_a_long_name_is_cut_to_the_limit():
    got = files.clean_name("x" * 200 + ".pdf", "application/pdf")
    assert len(got) <= files.MAX_NAME


def test_nothing_attached_is_fine_and_three_files_are_the_most():
    assert files.prepare(None) == [] and files.prepare([]) == []
    three = [attach(f"f{i}.pdf", PDF) for i in range(3)]
    assert [n for n, _, _ in files.prepare(three)] == ["f0.pdf", "f1.pdf", "f2.pdf"]
    assert refused(files.prepare, three + [attach("f3.pdf", PDF)])


@pytest.mark.parametrize("bad", [
    "a pdf", {"name": "a.pdf"}, [{"name": "a.pdf"}], [{"name": "a.pdf", "data": 5}],
    [{"name": "a.pdf", "data": "!!!not base64!!!"}], [{"name": "a.pdf", "data": ""}],
    [{"name": "a.exe", "data": b64(EXE)}], [{"name": "a.pdf", "data": b64(EXE)}],
    [{"name": "a.pdf", "data": b64(PNG)}], ["a.pdf"], [None],
])
def test_anything_malformed_or_of_the_wrong_kind_refuses_the_whole_attachment_list(bad):
    assert refused(files.prepare, bad)


def test_a_file_over_the_size_limit_is_refused_and_one_at_the_limit_is_not():
    big = b"%PDF-" + b"x" * (files.MAX_BYTES - 5)
    assert len(big) == files.MAX_BYTES
    assert files.prepare([attach("a.pdf", big)])[0][2] == big
    assert refused(files.prepare, [attach("a.pdf", big + b"y")])
    assert refused(files.prepare, [{"name": "a.pdf", "data": "A" * (files.MAX_BYTES * 2)}])


def test_the_scanner_refuses_the_antivirus_test_file_and_a_real_one_can_be_plugged_in():
    infected = b"%PDF-1.4\n" + files.EICAR
    assert refused(files.prepare, [attach("a.pdf", infected)])
    messaging.register_scanner(lambda data: b"BADWORD" not in data)
    assert refused(files.prepare, [attach("a.pdf", b"%PDF-1.4 BADWORD")])
    assert files.prepare([attach("a.pdf", infected)])                 # now this scanner decides
    messaging.reset_scanner()
    assert refused(files.prepare, [attach("a.pdf", infected)])


def test_one_bad_file_among_good_ones_refuses_all_of_them():
    mixed = [attach("ok.pdf", PDF), attach("bad.exe", EXE), attach("ok.png", PNG)]
    assert refused(files.prepare, mixed)


# ------------------------------------------------------------------ attaching and opening
def start_with(p, *attached, who="pat"):
    r = write(p, who, attachments=list(attached))
    assert r.status_code == 201, r.content
    return json.loads(r.content)["thread"]["id"]


def attachments_of(p, thread, subject="pat"):
    return [a for m in opened(p, thread, subject)["messages"] for a in m["attachments"]]


def download(p, attachment, subject="pat"):
    return p.get(f"messages/attachments/{attachment}", subject=subject)


def test_a_patient_attaches_a_file_and_the_clinic_can_download_exactly_that_file(p):
    thread = start_with(p, attach("My results.pdf", PDF))
    (mine,) = attachments_of(p, thread, "pat")
    assert mine["name"] == "My results.pdf" and mine["content_type"] == "application/pdf"
    assert mine["size"] == len(PDF)
    (theirs,) = attachments_of(p, thread, "ops")
    assert theirs["id"] == mine["id"]
    got = download(p, mine["id"], "ops")
    assert got.status_code == 200 and got.content == PDF


def test_the_clinic_attaches_a_picture_to_a_reply_and_the_patient_downloads_it(p):
    thread = started(p)
    r = p.post(f"messages/threads/{thread}/reply",
               {"body": "Here is the leaflet.", "attachments": [attach("leaflet.png", PNG)]},
               subject="ops")
    assert r.status_code == 200, r.content
    (found,) = attachments_of(p, thread, "pat")
    assert found["content_type"] == "image/png"
    assert download(p, found["id"], "pat").content == PNG


def test_a_file_is_only_ever_a_download_never_shown_in_the_page(p):
    thread = start_with(p, attach("note.txt", b"<html><script>alert(1)</script></html>"))
    (found,) = attachments_of(p, thread)
    got = download(p, found["id"])
    assert got["Content-Type"] == "text/plain"
    assert got["Content-Disposition"] == 'attachment; filename="note.txt"'
    assert got["X-Content-Type-Options"] == "nosniff" and got["Cache-Control"] == "no-store"
    assert "sandbox" in got["Content-Security-Policy"]


def test_up_to_three_files_can_go_with_one_message(p):
    thread = start_with(p, attach("a.pdf", PDF), attach("b.png", PNG), attach("c.txt", TEXT))
    assert [a["name"] for a in attachments_of(p, thread)] == ["a.pdf", "b.png", "c.txt"]


def test_a_message_without_files_shows_an_empty_list_and_works_as_before(p):
    thread = started(p)
    assert [m["attachments"] for m in opened(p, thread)["messages"]] == [[]]
    assert reply_ok(p, thread)


def reply_ok(p, thread):
    r = p.post(f"messages/threads/{thread}/reply", {"body": "Thank you."}, subject="ops")
    return r.status_code == 200


# ------------------------------------------------------------------ refusals leave nothing behind
@pytest.mark.parametrize("bad", [
    [attach("virus.exe", EXE)], [attach("a.pdf", PDF)] * 4, "a file", [{"name": "a.pdf"}],
    [attach("a.pdf", b"%PDF-1.4\n" + files.EICAR)], [attach("wrong.png", PDF)],
])
def test_a_refused_attachment_stops_the_whole_message_and_stores_nothing(p, bad):
    r = write(p, "pat", attachments=bad)
    assert r.status_code == 422
    assert threads(p, "pat") == []
    with tenant_transaction(p.clinic.ctx):
        assert Message.objects.count() == 0 and MessageAttachment.objects.count() == 0


def test_a_refused_attachment_on_a_reply_adds_neither_the_text_nor_the_file(p):
    thread = started(p)
    r = p.post(f"messages/threads/{thread}/reply",
               {"body": "see attached", "attachments": [attach("x.exe", EXE)]}, subject="ops")
    assert r.status_code == 422
    assert len(opened(p, thread)["messages"]) == 1


def test_sending_the_same_request_twice_stores_the_file_once(p):
    thread = started(p)
    body = {"body": "here it is", "attachments": [attach("a.pdf", PDF)]}
    for _ in range(2):
        r = p.post(f"messages/threads/{thread}/reply", body, subject="ops",
                   key="reply-with-a-file-once-1")
        assert r.status_code == 200, r.content
    with tenant_transaction(p.clinic.ctx):
        assert MessageAttachment.objects.count() == 1


# ------------------------------------------------------------------ who may open it
def test_only_people_in_the_conversation_may_open_a_file(p):
    thread = start_with(p, attach("private.pdf", PDF))
    (found,) = attachments_of(p, thread)
    p.patient("other")
    assert download(p, found["id"], "other").status_code in (403, 404)
    assert p.client.get(API + f"messages/attachments/{found['id']}").status_code == 401
    assert download(p, "nosuchattachment000000").status_code == 404


def test_another_clinics_staff_cannot_open_it_or_even_see_it_exists(p):
    thread = start_with(p, attach("private.pdf", PDF))
    (found,) = attachments_of(p, thread)
    other = Practice()
    assert other.get(f"messages/attachments/{found['id']}", subject="ops").status_code == 404


def test_the_download_is_a_read_only_address(p):
    thread = start_with(p, attach("a.pdf", PDF))
    (found,) = attachments_of(p, thread)
    assert p.post(f"messages/attachments/{found['id']}", {}, subject="pat").status_code == 405


# ------------------------------------------------------------------ how it is kept
def test_the_content_is_stored_encrypted_with_a_fingerprint_of_the_original(p):
    thread = start_with(p, attach("a.pdf", PDF))
    (found,) = attachments_of(p, thread)
    with tenant_transaction(p.clinic.ctx):
        row = MessageAttachment.objects.get(public_id=found["id"])
    assert row.digest == hashlib.sha256(PDF).hexdigest()
    assert b64(PDF) not in row.content_enc and PDF.decode("latin1") not in row.content_enc
    assert row.name == "a.pdf" and row.size == len(PDF)


def test_a_file_whose_fingerprint_no_longer_matches_is_never_served(p):
    thread = start_with(p, attach("a.pdf", PDF))
    (found,) = attachments_of(p, thread)
    with tenant_transaction(p.clinic.ctx):
        MessageAttachment.objects.filter(public_id=found["id"]).update(digest="0" * 64)
    assert download(p, found["id"]).status_code == 404


def test_file_names_and_content_never_travel_in_events(p):
    start_with(p, attach("my secret results.pdf", PDF))
    with tenant_transaction(p.clinic.ctx):
        blob = json.dumps([e.payload for e in OutboxEvent.objects.all()])
    assert "secret" not in blob and "results" not in blob and ".pdf" not in blob
