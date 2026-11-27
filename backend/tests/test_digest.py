import datetime as dt

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import QueuedEmail
from foundation.timeutil import utc_now
from modules.comms import digest
from modules.messaging.models import Thread
from modules.scheduling.models import Booking
from modules.trust.models import Actor, Credential
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import started

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.STAFF_APP_URL = "http://staff.test"


@pytest.fixture
def p():
    return Practice()


def give_email(p, actor_ref, address, verified=True, login=None):
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.create(
            actor=Actor.objects.get(public_id=actor_ref), login=login or address,
            password_hash="x", email=address, email_verified_at=utc_now() if verified else None)


def go_late(p, model, public_id, field, hours=30):
    with tenant_transaction(p.clinic.ctx):
        model.objects.filter(public_id=public_id).update(
            **{field: utc_now() - dt.timedelta(hours=hours)})


def run(p):
    with tenant_transaction(p.clinic.ctx):
        return digest.send_late_digest()


def queued():
    return list(QueuedEmail.objects.filter(kind=digest.KIND))


def test_nothing_is_sent_when_nothing_is_late(p):
    give_email(p, p.desk, "desk@clinic.test")
    booked(p, confirm=False)                                       # fresh: not late
    assert run(p) == 0 and queued() == []


def test_late_work_emails_the_verified_operations_staff(p):
    give_email(p, p.desk, "desk@clinic.test")
    go_late(p, Booking, booked(p, confirm=False), "created_at")
    thread = started(p)
    go_late(p, Thread, thread, "last_message_at")
    assert run(p) == 1
    (mail,) = queued()
    assert mail.subject.startswith("Running late at ")
    assert "1 appointment waiting for confirmation" in mail.body
    assert "1 conversation with an unopened patient message" in mail.body
    assert "http://staff.test" in mail.body


def test_the_email_holds_counts_only(p):
    give_email(p, p.desk, "desk@clinic.test")
    go_late(p, Booking, booked(p, confirm=False), "created_at")
    started(p)
    run(p)
    (mail,) = queued()
    assert p.person not in mail.body and p.patient_actor not in mail.body
    assert mail.body == (                              # the fixed counts-and-link text only
        "North has work waiting longer than 24 hours:\n\n"
        "- 1 appointment waiting for confirmation\n\nOpen the staff app: http://staff.test\n")


def test_it_is_sent_once_a_day_not_every_hour(p):
    give_email(p, p.desk, "desk@clinic.test")
    go_late(p, Booking, booked(p, confirm=False), "created_at")
    assert run(p) == 1
    assert run(p) == 0 and len(queued()) == 1


def test_an_unverified_address_gets_nothing(p):
    give_email(p, p.desk, "desk@clinic.test", verified=False)
    go_late(p, Booking, booked(p, confirm=False), "created_at")
    assert run(p) == 0 and queued() == []


def test_only_operations_staff_receive_it_not_doctors_or_patients(p):
    give_email(p, p.doc, "doctor@clinic.test")
    give_email(p, p.patient_actor, "patient@clinic.test")
    go_late(p, Booking, booked(p, confirm=False), "created_at")
    assert run(p) == 0 and queued() == []


def test_every_verified_operations_person_gets_one(p):
    give_email(p, p.desk, "desk@clinic.test")
    second = p.staff("ops2")
    give_email(p, second, "second@clinic.test")
    go_late(p, Booking, booked(p, confirm=False), "created_at")
    assert run(p) == 2 and len(queued()) == 2


def test_another_customers_late_work_is_not_counted_here(p):
    other = Practice()
    go_late(other, Booking, booked(other, confirm=False), "created_at")
    give_email(p, p.desk, "desk@clinic.test")
    booked(p, confirm=False)                                       # fresh here
    assert run(p) == 0 and queued() == []
