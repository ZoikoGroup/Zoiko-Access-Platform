import datetime as dt
import json

import pytest
from django.db import connection

from foundation import crypto
from foundation.cell_context import tenant_transaction
from modules.coverage.models import Coverage
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MEMBER = "STAR-12345-6789"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):   # staff deal only with their own site's patients
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def add(web, subject="pat", person=None, **over):
    body = {"insurer": "Star Health", "plan": "Family Floater", "member_id": MEMBER,
            "valid_from": "2026-01-01", "valid_until": "2027-12-31", **over}
    if person:
        body["person"] = person
    return web.post("coverage", body, subject=subject)


def mine(web, subject="pat"):
    r = web.get("coverage", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)["coverage"]


def of(web, person, subject="ops", ended=False):
    r = web.get(f"people/{person}/coverage" + ("?ended=1" if ended else ""), subject=subject)
    return r.status_code, (json.loads(r.content)["coverage"] if r.status_code == 200 else None)


# ------------------------------------------------------------------ the patient's own insurance
def test_a_patient_records_their_insurance_and_sees_it_with_a_masked_card_number(p):
    r = add(p)
    assert r.status_code == 201
    row = json.loads(r.content)["coverage"]
    assert row["insurer"] == "Star Health" and row["member_last4"] == "6789"
    assert row["in_force"] is True and row["status"] == "active"
    listed = mine(p)
    assert [c["id"] for c in listed] == [row["id"]]
    assert MEMBER not in json.dumps(listed) and "12345" not in json.dumps(listed)   # never in full


def test_the_member_number_is_stored_encrypted_and_bound_to_its_row(p):
    add(p)
    with connection.cursor() as cur:
        cur.execute("select member_ref_enc from coverage_coverage")
        (sealed,) = cur.fetchone()
    assert sealed.startswith("zf1:") and "12345" not in sealed and MEMBER not in sealed
    with tenant_transaction(p.clinic.ctx):
        row = Coverage.objects.get()
        ctx = p.clinic.ctx
        plain = crypto.decrypt(row.member_ref_enc, tenant_id=ctx.tenant_id,
                               context=f"coverage:{row.public_id}:member_ref")
        assert plain == MEMBER
        with pytest.raises(crypto.CryptoError):                   # not valid for any other row
            crypto.decrypt(row.member_ref_enc, tenant_id=ctx.tenant_id,
                           context="coverage:another:member_ref")


def test_a_coverage_that_has_run_out_is_listed_but_not_in_force(p):
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    row = json.loads(add(p, valid_from="2020-01-01", valid_until=yesterday).content)["coverage"]
    assert row["in_force"] is False and row["status"] == "active"


def test_several_policies_can_be_held_up_to_a_limit(p):
    for i in range(5):
        assert add(p, insurer=f"Insurer {i}").status_code == 201
    assert add(p, insurer="One too many").status_code == 409
    assert len(mine(p)) == 5


# ------------------------------------------------------------------ input rules
@pytest.mark.parametrize("fields", [
    {"insurer": ""}, {"insurer": "X"}, {"insurer": "x" * 81}, {"insurer": "Bad\x00Name"},
    {"member_id": ""}, {"member_id": "ab"}, {"member_id": "bad!id"}, {"member_id": "x" * 41},
    {"valid_from": "01/01/2026"}, {"valid_until": "2026-13-45"},
    {"valid_from": "2027-06-01", "valid_until": "2027-01-01"}, {"valid_from": "1800-01-01"},
])
def test_bad_input_is_refused_and_nothing_is_stored(p, fields):
    assert add(p, **fields).status_code == 422
    assert mine(p) == []


def test_dates_and_the_plan_are_optional(p):
    r = add(p, plan="", valid_from="", valid_until="")
    assert r.status_code == 201
    row = json.loads(r.content)["coverage"]
    assert row["plan"] == "" and row["valid_from"] is None and row["in_force"] is True


# ------------------------------------------------------------------ ending one
def test_a_patient_ends_a_coverage_and_it_leaves_the_list(p):
    row = json.loads(add(p).content)["coverage"]
    assert p.post(f"coverage/{row['id']}/end", subject="pat").status_code == 200
    assert p.post(f"coverage/{row['id']}/end", subject="pat").status_code == 200   # harmless again
    assert mine(p) == []
    assert of(p, p.person, "pat", ended=True)[1][0]["status"] == "ended"


def test_ending_one_makes_room_under_the_limit(p):
    first = json.loads(add(p, insurer="Insurer 0").content)["coverage"]["id"]
    for i in range(1, 5):
        add(p, insurer=f"Insurer {i}")
    assert add(p, insurer="Extra").status_code == 409
    p.post(f"coverage/{first}/end", subject="pat")
    assert add(p, insurer="Extra").status_code == 201


# ------------------------------------------------------------------ who may do what
def test_the_front_desk_can_record_and_end_insurance_for_a_patient(p):
    shared = p.post("coverage/sharing", {"share": True}, subject="pat")      # the patient shares
    assert shared.status_code == 200
    r = add(p, subject="ops", person=p.person, insurer="HDFC Ergo")
    assert r.status_code == 201
    status, rows = of(p, p.person)
    assert status == 200 and [c["insurer"] for c in rows] == ["HDFC Ergo"]
    assert p.post(f"coverage/{rows[0]['id']}/end", subject="ops").status_code == 200
    assert of(p, p.person)[1] == []


def test_staff_must_say_which_patient(p):
    assert add(p, subject="ops").status_code == 422


def test_a_doctor_cannot_see_or_change_insurance(p):
    row = json.loads(add(p).content)["coverage"]
    assert of(p, p.person, "doc")[0] == 403
    assert add(p, subject="doc", person=p.person).status_code == 403
    assert p.post(f"coverage/{row['id']}/end", subject="doc").status_code == 403
    assert p.get("coverage", subject="doc").status_code == 200
    assert mine(p, "doc") == []


def test_nobody_can_touch_another_patients_insurance(p):
    _, other = p.patient("pat2")
    row = json.loads(add(p).content)["coverage"]
    assert add(p, subject="pat", person=other).status_code == 403
    assert of(p, p.person, "pat2")[0] == 403
    assert p.post(f"coverage/{row['id']}/end", subject="pat2").status_code == 403
    assert mine(p, "pat2") == []
    assert len(mine(p)) == 1


def test_another_customers_staff_cannot_touch_it(p):
    row = json.loads(add(p).content)["coverage"]
    outsider = Web()
    outsider.staff("desk")
    assert add(outsider, subject="desk", person=p.person).status_code in (403, 404)
    assert outsider.post(f"coverage/{row['id']}/end", subject="desk").status_code == 404
    assert len(mine(p)) == 1


def test_an_unknown_patient_id_is_refused(p):
    assert add(p, subject="ops", person="nosuchperson000000000").status_code in (403, 404)


# ------------------------------------------------------------------ the record
def test_the_audit_trail_says_who_added_and_ended_it_but_not_what_it_contains(p):
    row = json.loads(add(p).content)["coverage"]
    p.post(f"coverage/{row['id']}/end", subject="ops")
    with tenant_transaction(p.clinic.ctx):
        events = list(AuditEvent.objects.filter(object_ref=row["id"]).order_by("chain_seq"))
    assert [e.action for e in events] == ["coverage.add", "coverage.end"]
    assert events[0].actor_ref == p.patient_actor and events[1].actor_ref == p.desk
    text = " ".join(str(e.details) for e in events)
    assert "Star" not in text and "6789" not in text
