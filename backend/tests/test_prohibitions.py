"""The five product prohibitions, in test form.

The Product Orientation document (section 12, "Five product prohibitions in test form") lists five
things the product must never do. Each one has a named test here, so a future change that breaks one
fails by name. They test the real web interface and the real rules, not the code's own lists.
"""

import json
import uuid

import pytest

from api import urls as api_urls
from foundation.cell_context import CellContext, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.ai import api as ai
from tests.test_ai import CLOCK, authorize, propose, walk
from tests.test_api import Web, book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def a_booking_with_insurance(p):
    """A patient with a booking and insurance that they have chosen to share with the clinic."""
    p.add_times(p.doc)
    slot = p.open_slots()[0]["id"]
    booked = json.loads(book(p, slot, subject="pat").content)["booking"]
    insurance = {"insurer": "Star Health", "plan": "Family", "member_id": "STAR-12345-6789"}
    assert p.post("coverage", insurance, subject="pat").status_code == 201
    assert p.post("coverage/sharing", {"share": True}, subject="pat").status_code == 200
    return booked


# ------------------------------------------------------------------ 1. employers
def test_1_never_expose_a_persons_health_record_to_an_employer_because_of_employment_or_funding(p):
    booked = a_booking_with_insurance(p)
    p.login("emp", "employer_user", "R08")
    private = [booked["id"], p.person, "Star Health"]
    for path in (
        "bookings",
        f"people/{p.person}/coverage",
        "coverage",
        "clinician/patients",
        "clinician/schedule",
        "referrals",
        f"bookings/{booked['id']}/history",
    ):
        r = p.get(path, subject="emp")
        body = r.content.decode()
        assert r.status_code in (200, 403, 404), path
        for secret in private:
            assert secret not in body, f"{path} showed {secret} to an employer"
    assert p.post(f"bookings/{booked['id']}/confirm", subject="emp").status_code == 403


# ------------------------------------------------------------------ 2. only the provider confirms
def test_2_never_confirm_a_slot_unless_the_provider_has_confirmed_it(p):
    p.add_times(p.doc)
    slot = p.open_slots()[0]["id"]
    made = book(p, slot, subject="pat")
    booking = json.loads(made.content)["booking"]
    assert booking["status"] == "requested"  # asking is not confirming
    p.login("emp", "employer_user", "R08")
    for who in ("pat", "emp", "doc"):  # not the patient, an employer or a doctor
        assert p.post(f"bookings/{booking['id']}/confirm", subject=who).status_code == 403, who
    mine = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    assert [b["status"] for b in mine] == ["requested"]  # still waiting, shown honestly
    assert p.post(f"bookings/{booking['id']}/confirm", subject="ops").status_code == 200
    mine = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    assert [b["status"] for b in mine] == ["confirmed"]  # only the clinic's own act changed it


# ------------------------------------------------------------------ 3. critical results
def test_3_never_mark_a_critical_result_reviewed_from_a_message_or_an_opened_screen():
    """Results are not built yet (backlog B1), so there is nothing to mark reviewed.

    This is a tripwire: the day any route about results or critical findings appears, this test
    fails and must be replaced by the real rule (a named clinician reviews it; a delivered message
    or an opened screen never counts).
    """
    routes = [str(u.pattern) for u in api_urls.urlpatterns]
    assert [r for r in routes if "result" in r or "critical" in r] == []


# ------------------------------------------------------------------ 4. regions
def test_4_never_route_protected_information_across_regions_because_someone_has_an_account(p):
    booked = a_booking_with_insurance(p)
    # A different cell is refused outright, whoever is asking.
    elsewhere = CellContext("OTHER-99", uuid.uuid4())
    with pytest.raises(ZoikoError) as refused, tenant_transaction(elsewhere):
        pass
    assert refused.value.code == ErrorCode.POLICY_DENIED
    # A person with a perfectly good account at ANOTHER customer still gets nothing.
    outsider = Web()
    outsider.staff("desk")
    for path in ("bookings", f"people/{p.person}/coverage", "coverage", "staff/work"):
        r = outsider.get(path, subject="desk")
        body = r.content.decode()
        for secret in (booked["id"], p.person, "Star Health"):
            assert secret not in body, f"{path} showed {secret} to another customer's staff"


# ------------------------------------------------------------------ 5. clinical AI
def test_5_never_switch_on_clinical_ai_without_its_own_purpose_and_authorization(ctx_a, settings):
    ai.register_builtin()
    for debug in (True, False):  # no general-purpose switch turns AI on
        settings.DEBUG = debug
        with tenant_transaction(ctx_a), pytest.raises(ZoikoError) as refused:
            ai.run(
                ai.REPLY_DRAFT,
                actor_ref="staff",
                purpose_code="reply",
                input_text="hi",
                clock=CLOCK,
            )
        assert refused.value.code == ErrorCode.POLICY_DENIED
    with tenant_transaction(ctx_a):  # a clinical-decision feature cannot be authorized
        permit = propose(feature_id="triage.suggest", lane="C", level="A3")
        walk(permit)
        with pytest.raises(ZoikoError) as blocked:
            authorize(permit)
    assert blocked.value.code == ErrorCode.CLINICAL_ACCEPTANCE_ABSENT
