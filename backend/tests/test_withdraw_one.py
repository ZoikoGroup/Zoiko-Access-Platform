import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MONDAY = "2027-09-06T09:00:00"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.before = {s["id"] for s in practice.open_slots()}
    return practice


def mine(p):
    new = (s for s in p.open_slots() if s["id"] not in p.before)
    return sorted(new, key=lambda s: s["local_start"])


def make(p, count=3, first=MONDAY):
    p.post(f"services/{p.clinic.service}/slots",
           {"location": p.clinic.location, "first_local": first, "count": count, "days": 1,
            "provider": p.doc}, subject="ops")


def remove(p, slot, subject="ops"):
    return p.post(f"slots/{slot}/withdraw", {}, subject=subject)


def test_one_time_goes_and_the_others_stay(p):
    make(p)
    first, second, third = mine(p)
    r = remove(p, second["id"])
    assert r.status_code == 200 and json.loads(r.content) == {"withdrawn": True}
    assert [s["id"] for s in mine(p)] == [first["id"], third["id"]]


def test_a_patient_can_no_longer_book_it(p):
    make(p, count=1)
    slot = mine(p)[0]["id"]
    remove(p, slot)
    assert book(p, slot, subject="pat").status_code in (404, 409, 422)


@pytest.mark.parametrize("confirm", [False, True])
def test_a_time_with_a_booking_is_refused_and_the_booking_stays(p, confirm):
    make(p, count=1)
    slot = mine(p)[0]["id"]
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    if confirm:
        p.post(f"bookings/{booking}/confirm", subject="ops")
    assert remove(p, slot).status_code == 409
    rows = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    expected = "confirmed" if confirm else "requested"
    assert [b["status"] for b in rows if b["id"] == booking] == [expected]


def test_a_time_whose_booking_was_cancelled_can_be_removed(p):
    make(p, count=1)
    slot = mine(p)[0]["id"]
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    p.post(f"bookings/{booking}/cancel", subject="pat")
    assert remove(p, slot).status_code == 200


def test_removing_the_same_time_twice_is_refused_the_second_time(p):
    make(p, count=1)
    slot = mine(p)[0]["id"]
    assert remove(p, slot).status_code == 200
    assert remove(p, slot).status_code == 409


def test_a_time_in_the_past_is_refused(p):
    import datetime as dt

    from foundation.timeutil import utc_now
    from modules.scheduling.models import Slot
    start = utc_now() - dt.timedelta(hours=3)
    with tenant_transaction(p.clinic.ctx):
        old = Slot.objects.create(
            service_ref=p.clinic.service, location_ref=p.clinic.location,
            organization_ref=p.clinic.org, starts_at=start,
            ends_at=start + dt.timedelta(minutes=30), local_zone="Asia/Kolkata", provider_ref=p.doc)
    assert remove(p, old.public_id).status_code == 409


def test_an_unknown_time_is_not_found(p):
    assert remove(p, "slot_does_not_exist").status_code == 404


def test_a_removed_time_comes_back_when_created_again(p):
    make(p, count=1)
    remove(p, mine(p)[0]["id"])
    assert mine(p) == []
    make(p, count=1)
    assert len(mine(p)) == 1


def test_only_clinic_staff_may_do_it(p):
    make(p, count=1)
    slot = mine(p)[0]["id"]
    for who in ("pat", "doc"):
        assert remove(p, slot, subject=who).status_code == 403
    assert len(mine(p)) == 1


def test_another_customer_cannot_remove_it(p):
    make(p, count=1)
    slot = mine(p)[0]["id"]
    other = Practice()
    assert other.post(f"slots/{slot}/withdraw", {}, subject="ops").status_code in (403, 404)
    assert len(mine(p)) == 1


def test_a_refusal_is_not_audited_but_a_removal_is_once(p):
    make(p, count=2)
    first, second = mine(p)
    remove(p, first["id"])
    remove(p, first["id"])                                         # refused
    with tenant_transaction(p.clinic.ctx):
        assert AuditEvent.objects.filter(action="slot.withdraw_one").count() == 1
    assert [s["id"] for s in mine(p)] == [second["id"]]


def test_the_service_itself_refuses_an_unknown_time(p):
    from foundation.errors import ErrorCode, ZoikoError
    from modules.scheduling import api as scheduling
    with tenant_transaction(p.clinic.ctx), pytest.raises(ZoikoError) as caught:
        scheduling.withdraw_slot(slot_ref="slot_missing", actor_public_id="x", decision=None)  # type: ignore[arg-type]
    assert caught.value.code == ErrorCode.NOT_FOUND
