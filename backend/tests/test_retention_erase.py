"""Erasing what is past an approved retention period (DEV-351): off unless the deployment switches
it on, only for the clinic's administrators, only closed conversations, never a patient on a lawful
hold, and only for exactly the number that was shown."""
import base64
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.comms import api as comms
from modules.evidence.models import AuditEvent
from modules.messaging import retention as message_retention
from modules.messaging.models import Message, MessageAttachment, Thread
from modules.rights.models import RightsCase
from modules.trust import api as trust
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_messaging import SECRET, opened, started, threads, write
from tests.test_retention import _approved, report, row

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

URL = "staff/retention-erasure/message_threads"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


@pytest.fixture
def switched_on(settings):
    settings.RETENTION_DELETION_ENABLED = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    practice.login("boss2", trust.ActorType.FACILITY_USER, "R13")
    return practice


def age(p, thread, days=800):
    with tenant_transaction(p.clinic.ctx):
        Thread.objects.filter(public_id=thread).update(
            last_message_at=utc_now() - dt.timedelta(days=days))


def old_thread(p, closed=True, days=800, **over):
    thread = started(p, **over)
    if closed:
        assert p.post(f"messages/threads/{thread}/close", subject="ops").status_code == 200
    age(p, thread, days)
    return thread


def rule(p, days=365):
    _approved(p, days=days, key="message_threads")


def preview(p, subject="boss"):
    r = p.get(URL, subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def erase(p, confirm, subject="boss", url=URL):
    return p.post(url, {"confirm": confirm}, subject=subject)


def kept(p, thread):
    return opened(p, thread, "ops")["messages"][0]["body"] == SECRET


def test_it_is_switched_off_unless_the_deployment_turns_it_on(p):
    thread = old_thread(p)
    rule(p)
    assert report(p)["deletes_anything"] is False
    found = preview(p)
    assert found["enabled"] is False and found["due"] == 1
    assert erase(p, 1).status_code == 403
    assert kept(p, thread)


def test_the_report_says_which_kinds_can_be_erased(p, switched_on):
    body = report(p)
    assert body["deletes_anything"] is True
    assert row(body, "message_threads")["erasable"] is True
    assert row(body, "bookings")["erasable"] is False


def test_erasing_removes_what_was_said_and_attached_and_keeps_the_conversation(p, switched_on):
    pdf = base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode()
    made = write(p, attachments=[{"name": "results.pdf", "data": pdf}])
    assert made.status_code == 201, made.content
    thread = json.loads(made.content)["thread"]["id"]
    assert p.post(f"messages/threads/{thread}/close", subject="ops").status_code == 200
    age(p, thread)
    attached = opened(p, thread)["messages"][0]["attachments"][0]["id"]
    rule(p)

    assert preview(p)["due"] == 1
    done = erase(p, 1)
    assert done.status_code == 200, done.content
    assert json.loads(done.content) == {"data_class": "message_threads", "erased": 1}

    shown = opened(p, thread)
    assert shown["erased"] is True and shown["subject"] == "" and shown["status"] == "closed"
    assert [(m["body"], m["attachments"]) for m in shown["messages"]] == [("", [])]
    assert [t["id"] for t in threads(p)] == [thread]               # who and when stay
    assert p.get(f"messages/attachments/{attached}", subject="pat").status_code == 404
    with tenant_transaction(p.clinic.ctx):
        assert Thread.objects.get(public_id=thread).subject_enc == ""
        assert set(Message.objects.values_list("body_enc", flat=True)) == {""}
        file = MessageAttachment.objects.get(public_id=attached)
        assert (file.content_enc, file.digest, file.name) == ("", "", "erased")
        audit = AuditEvent.objects.get(action="retention.erased")
        assert audit.details == {"erased": 1} and audit.reason_code == "message_threads"
    assert preview(p)["due"] == 0                                   # nothing is counted twice
    assert row(report(p), "message_threads")["past_period"] == 0


def test_open_conversations_and_recent_ones_are_left_alone(p, switched_on):
    still_open = old_thread(p, closed=False)
    recent = old_thread(p, days=10)
    rule(p)
    assert preview(p)["due"] == 0
    assert json.loads(erase(p, 0).content)["erased"] == 0
    assert kept(p, still_open) and kept(p, recent)


def test_a_patient_on_a_lawful_hold_is_never_erased(p, switched_on):
    thread = old_thread(p)
    rule(p)
    with tenant_transaction(p.clinic.ctx):
        RightsCase.objects.create(
            ref_no="DSR-TEST0002", person_ref=p.person,
            organization_ref=Thread.objects.get(public_id=thread).organization_ref,
            requested_by_ref=p.patient_actor, request_type="erasure", lawful_hold=True,
            hold_reason="Litigation", cell_id=p.clinic.ctx.cell_id,
            tenant_id=p.clinic.ctx.tenant_id)
    assert preview(p)["due"] == 0
    assert erase(p, 1).status_code == 409
    assert kept(p, thread)


def test_nothing_is_erased_when_the_number_is_not_the_one_shown(p, switched_on):
    first = old_thread(p)
    rule(p)
    seen = preview(p)["due"]
    second = old_thread(p, subject="Another question")           # one more became due meanwhile
    assert erase(p, seen).status_code == 409
    assert kept(p, first) and kept(p, second)


@pytest.mark.parametrize("bad", [None, "1", True, -1, 1.0])
def test_the_confirmation_must_be_a_whole_number(p, switched_on, bad):
    thread = old_thread(p)
    rule(p)
    assert erase(p, bad).status_code == 422
    assert kept(p, thread)


def test_there_must_be_an_approved_period_and_a_kind_that_can_be_erased(p, switched_on):
    old_thread(p)
    assert p.get(URL, subject="boss").status_code == 409            # no approved period
    assert erase(p, 1).status_code == 409
    _approved(p, key="bookings")
    for kind in ("bookings", "medical_notes"):
        url = f"staff/retention-erasure/{kind}"
        assert p.get(url, subject="boss").status_code == 404
        assert erase(p, 0, url=url).status_code == 404


def test_only_the_clinics_administrators_may_look_or_erase(p, switched_on):
    thread = old_thread(p)
    rule(p)
    for who in ("ops", "doc", "pat"):
        assert p.get(URL, subject=who).status_code == 403, who
        assert erase(p, 1, subject=who).status_code == 403, who
    assert p.client.get(API + URL).status_code == 401
    assert kept(p, thread)


def test_another_organizations_conversations_are_never_counted_or_erased(p, switched_on):
    thread = old_thread(p)
    now = utc_now()
    with tenant_transaction(p.clinic.ctx):
        elsewhere = trust.create_organization(name="Elsewhere").public_id
        assert message_retention.due_for_erasure(now, frozenset(), elsewhere) == 0
        assert message_retention.erase_older(now, frozenset(), elsewhere) == 0
    assert kept(p, thread)


# ------------------------------------------------------------------ pointers to clinical records
REFS = "staff/retention-erasure/clinical_references"


def pointer(p, days_ago=900, person=None, org=None):
    from modules.clinicalref.models import ClinicalReference

    with tenant_transaction(p.clinic.ctx):
        return ClinicalReference.objects.create(
            person_ref=person or p.person, organization_ref=org or p.clinic.org,
            author_ref=p.doc, system="emis", external_id=f"REC-{days_ago}-{utc_now().microsecond}",
            kind="result", refers_to=(utc_now() - dt.timedelta(days=days_ago)).date(),
            cell_id=p.clinic.ctx.cell_id, tenant_id=p.clinic.ctx.tenant_id).public_id


def refs_rule(p):
    _approved(p, days=365, key="clinical_references")


def test_record_pointers_can_be_erased_and_keep_what_and_when(p, switched_on):
    from modules.clinicalref.models import ClinicalReference

    old, recent = pointer(p), pointer(p, days_ago=10)
    refs_rule(p)
    assert row(report(p), "clinical_references")["erasable"] is True
    found = json.loads(p.get(REFS, subject="boss").content)
    assert found["due"] == 1
    done = p.post(REFS, {"confirm": 1}, subject="boss")
    assert done.status_code == 200, done.content
    with tenant_transaction(p.clinic.ctx):
        gone = ClinicalReference.objects.get(public_id=old)
        kept = ClinicalReference.objects.get(public_id=recent)
    assert (gone.external_id, gone.status, gone.withdrawn_reason) == (
        "", "withdrawn", "retention_period")
    assert (gone.kind, gone.system) == ("result", "emis")
    assert kept.external_id and kept.status == "active"
    assert json.loads(p.get(REFS, subject="boss").content)["due"] == 0   # not counted again


def test_a_held_patients_pointer_and_another_clinics_are_left_alone(p, switched_on):
    from modules.clinicalref import retention as ref_retention

    pointer(p)
    with tenant_transaction(p.clinic.ctx):
        elsewhere = trust.create_organization(name="Elsewhere").public_id
    pointer(p, org=elsewhere)
    refs_rule(p)
    with tenant_transaction(p.clinic.ctx):
        RightsCase.objects.create(
            ref_no="DSR-TEST0003", person_ref=p.person, organization_ref=p.clinic.org,
            requested_by_ref=p.patient_actor, request_type="erasure", lawful_hold=True,
            hold_reason="Litigation", cell_id=p.clinic.ctx.cell_id,
            tenant_id=p.clinic.ctx.tenant_id)
        cutoff = utc_now()
        held = frozenset({p.person})
        assert ref_retention.due_for_erasure(cutoff, held, p.clinic.org) == 0
        assert ref_retention.due_for_erasure(cutoff, frozenset(), elsewhere) == 1
    assert json.loads(p.get(REFS, subject="boss").content)["due"] == 0


def test_a_doctor_cannot_pick_the_erased_reason_by_hand(p, settings):
    settings.CLINICAL_REFERENCES_ENABLED = True
    ref = pointer(p, days_ago=5)
    r = p.post(f"clinician/references/{ref}/withdraw", {"reason": "retention_period"},
               subject="doc")
    assert r.status_code in (403, 422)
