"""Bookings' and referrals' lifecycle stages, written down as they are seen (DEV-360)."""
import json

import pytest
from django.db import DatabaseError, connection

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from modules.stages import api as stages
from modules.stages.models import StageStep
from tests.test_api import API, Web, book
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    stages.register_subscribers()


def deliver(passes=4):
    for _ in range(passes):
        relay_once(dispatch.dispatch)


@pytest.fixture
def web():
    w = Web()
    w.patient("pat")
    w.staff("desk")
    return w


def booking(web):
    r = book(web, web.slots[0]["public_id"], subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["booking"]["id"]


def seen(web, ref, who="desk", kind="bookings"):
    r = web.get(f"{kind}/{ref}/stages", subject=who)
    assert r.status_code == 200, r.content
    return [s["state"] for s in json.loads(r.content)["stages"]]


def test_each_stage_of_a_booking_is_written_down_once_and_in_order(web):
    ref = booking(web)
    deliver()
    assert web.post(f"bookings/{ref}/confirm", subject="desk").status_code == 200
    deliver()
    deliver()                                                   # nothing new: nothing added
    assert seen(web, ref) == ["REQUEST_SUBMITTED", "CONFIRMED"]
    assert seen(web, ref) == ["REQUEST_SUBMITTED", "CONFIRMED"]  # a look adds nothing either
    with tenant_transaction(web.clinic.ctx):
        causes = list(StageStep.objects.order_by("seq").values_list("cause", flat=True))
    assert causes[0].startswith("booking.") and causes[1].startswith("booking.")


def test_a_look_writes_down_a_stage_no_event_announced(web):
    ref = booking(web)                                          # no event delivered yet
    assert seen(web, ref) == ["REQUEST_SUBMITTED"]
    with tenant_transaction(web.clinic.ctx):
        assert StageStep.objects.get().cause == "observed"


def test_the_patient_sees_their_own_and_nobody_else_does(web):
    ref = booking(web)
    assert seen(web, ref, who="pat") == ["REQUEST_SUBMITTED"]
    web.patient("pat2")
    assert web.get(f"bookings/{ref}/stages", subject="pat2").status_code in (403, 404)
    assert web.client.get(API + f"bookings/{ref}/stages").status_code == 401
    other = Web()
    # Another customer's login is not even a session here: refused like any stranger.
    assert other.get(f"bookings/{ref}/stages", subject="ops").status_code in (401, 403, 404)


def test_a_referral_is_written_down_through_receipt_and_decision():
    c = Clinic2()
    made = issued(c)
    deliver()
    assert c.post(f"referrals/{made['id']}/acknowledge", subject="ops").status_code == 200
    deliver()
    assert c.decide(made["id"], "accepted").status_code == 200
    deliver()
    assert seen(c, made["id"], who="ops", kind="referrals") == [
        "SENT", "INTAKE_PENDING", "ACCEPTED"]
    assert seen(c, made["id"], who="doc", kind="referrals")[-1] == "ACCEPTED"
    assert seen(c, made["id"], who="pat", kind="referrals")[-1] == "ACCEPTED"


def test_a_stranger_cannot_read_a_referrals_stages():
    c = Clinic2()
    made = issued(c)
    c.patient("other")
    assert c.get(f"referrals/{made['id']}/stages", subject="other").status_code == 404
    assert c.get("referrals/nosuchreferral00000000/stages", subject="ops").status_code == 404


def test_stages_can_never_be_changed_or_removed(web):
    ref = booking(web)
    seen(web, ref)
    with tenant_transaction(web.clinic.ctx):
        row = StageStep.objects.get()
        with pytest.raises(PermissionError):
            row.save()
        with pytest.raises(PermissionError):
            row.delete()
    for sql in ("UPDATE stages_stagestep SET state = 'CONFIRMED'", "DELETE FROM stages_stagestep"):
        with pytest.raises(DatabaseError), tenant_transaction(web.clinic.ctx):
            with connection.cursor() as cursor:
                cursor.execute(sql)
    assert seen(web, ref) == ["REQUEST_SUBMITTED"]
