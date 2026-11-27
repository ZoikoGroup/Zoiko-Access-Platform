"""Diagnostics, clinical AI and record exchange switched by the country pack (DEV-365); privileges
that a clinic can enforce (DEV-366); a clinic resetting a patient's two-step sign-in (DEV-367);
patients by region (DEV-368); and a notice when a clinic erases record pointers (DEV-369)."""
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from foundation.outbox import relay_once
from foundation.timeutil import utc_now
from modules.ai import permits as ai_permits
from modules.comms import api as comms
from modules.comms.models import Notification
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.models import Actor, Credential, SecondFactor
from tests.test_api import book
from tests.test_clinician import Practice
from tests.test_identity import Reg
from tests.test_packs_in_use import Market

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
PASSWORD = "Long-Enough-Pass-2026!"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def body(r):
    return json.loads(r.content)


# ------------------------------------------------------------------ DEV-365: the three capabilities
def test_lab_orders_need_diagnostics_and_other_partner_requests_need_record_exchange(settings):
    m = Market(settings)
    m.sign("referrals")
    lab = m.web.post("exchange/requests", {"kind": "lab_order"}, subject="doc")
    other = m.web.post("exchange/requests", {"kind": "pharmacy_request"}, subject="doc")
    assert m.code(lab) == "JURISDICTION_INACTIVE" and m.code(other) == "JURISDICTION_INACTIVE"


def test_with_diagnostics_on_a_lab_order_gets_past_the_gate(settings):
    m = Market(settings)
    m.sign("diagnostics")
    lab = m.web.post("exchange/requests", {"kind": "lab_order"}, subject="doc")
    assert m.code(lab) != "JURISDICTION_INACTIVE"            # refused later, for the empty body
    other = m.web.post("exchange/requests", {"kind": "homecare_referral"}, subject="doc")
    assert m.code(other) == "JURISDICTION_INACTIVE"


def test_a_new_clinic_record_reference_needs_record_exchange_but_reading_does_not(settings):
    settings.CLINICAL_REFERENCES_ENABLED = True
    m = Market(settings)
    m.sign("booking")
    person = m.web.clinic.patient("pat2")[1]
    path = f"clinician/patients/{person}/references"
    assert m.code(m.web.post(path, {}, subject="doc")) == "JURISDICTION_INACTIVE"
    assert m.code(m.web.get(path, subject="doc")) != "JURISDICTION_INACTIVE"


def test_clinical_ai_is_gated_but_admin_assist_never_is(settings):
    m = Market(settings)
    with tenant_transaction(m.web.clinic.ctx):
        ai_permits.market_allows("A")                          # admin assist: always fine
        with pytest.raises(ZoikoError):
            ai_permits.market_allows("B")
    m.sign("clinical_ai", "booking")
    with tenant_transaction(m.web.clinic.ctx):
        ai_permits.market_allows("B")
        ai_permits.market_allows("C")


def test_with_packs_not_enforced_nothing_is_gated(settings):
    m = Market(settings, enforced=False)
    with tenant_transaction(m.web.clinic.ctx):
        ai_permits.market_allows("D")
    lab = m.web.post("exchange/requests", {"kind": "lab_order"}, subject="doc")
    assert m.code(lab) != "JURISDICTION_INACTIVE"


def test_staff_settings_say_what_the_country_pack_switches_on(settings):
    m = Market(settings)
    m.sign("booking", "diagnostics")
    m.web.login("boss", trust.ActorType.FACILITY_USER, "R13")
    got = body(m.web.get("staff/settings", subject="boss"))["market"]
    assert got["enforced"] is True and got["country"] == "GB"
    assert got["on"] == ["booking", "diagnostics"] and "clinical_ai" in got["all"]


# ------------------------------------------------------------------ DEV-366: privileges enforced
@pytest.fixture
def p():
    practice = Practice()
    practice.boss = practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    assert practice.add_times(practice.doc).status_code == 201
    return practice


def doctors_slot(p):
    return next(s["id"] for s in p.open_slots() if s["provider"])


def enforce(p, on=True, who="boss"):
    return p.post("staff/settings", {"privileges_enforced": on}, subject=who)


def test_only_the_administrator_switches_enforcement_and_it_is_off_by_default(p):
    assert body(p.get("staff/settings", subject="boss"))["privileges_enforced"] is False
    assert enforce(p, who="ops").status_code == 403
    assert enforce(p).status_code == 200
    assert body(p.get("staff/settings", subject="boss"))["privileges_enforced"] is True
    assert enforce(p, on="yes").status_code == 422


def test_while_off_a_doctor_without_a_privilege_works_as_before(p):
    assert book(p, doctors_slot(p)).status_code == 201


def test_while_on_a_doctor_needs_a_privilege_for_new_times_and_for_bookings(p):
    slot = doctors_slot(p)
    assert enforce(p).status_code == 200
    assert book(p, slot).status_code == 403
    assert p.add_times(p.doc, first="2027-10-01T09:00:00").status_code == 422
    granted = p.post(f"staff/providers/{p.doc}/privileges", {"service": p.clinic.service},
                     subject="boss")
    assert granted.status_code == 201, granted.content
    assert p.add_times(p.doc, first="2027-10-01T09:00:00").status_code == 201
    assert book(p, slot).status_code == 201


def test_a_withdrawn_privilege_stops_bookings_again(p):
    granted = body(p.post(f"staff/providers/{p.doc}/privileges", {"service": p.clinic.service},
                          subject="boss"))["privileges"][0]
    enforce(p)
    path = f"staff/providers/{p.doc}/privileges/{granted['id']}/withdraw"
    assert p.post(path, {"reason": "Left the service"}, subject="boss").status_code == 200
    assert book(p, doctors_slot(p)).status_code == 403


# ------------------------------------------------------------------ DEV-367: patient two-step reset
def with_two_step(reg, subject):
    actor, person = reg.people[subject]
    with tenant_transaction(reg.ctx):
        trust.set_password(actor, login=f"{subject}@example.org", password=PASSWORD, by="system")
        SecondFactor.objects.create(
            actor=Actor.objects.get(public_id=actor), secret_enc="x", confirmed_at=utc_now(),
            cell_id=reg.ctx.cell_id, tenant_id=reg.ctx.tenant_id)
    return person


def test_staff_reset_a_patients_two_step_only_once_their_identity_is_verified():
    reg = Reg()
    reg.patient("pat", "Asha Rao", "1990-03-12")
    person = with_two_step(reg, "pat")
    seen = body(reg.web.get(f"people/{person}/identity", subject="desk"))["second_factor"]
    assert seen == {"has_login": True, "on": True}
    path = f"people/{person}/second-factor/reset"
    assert reg.web.post(path, subject="desk").status_code == 403       # identity not verified
    assert reg.check("pat").status_code == 201
    assert reg.web.post(path, subject="pat").status_code == 403        # not the patient's own
    with tenant_transaction(reg.ctx):
        epoch = Credential.objects.get(actor__public_id=reg.people["pat"][0]).session_epoch
    done = reg.web.post(path, subject="desk")
    assert done.status_code == 200, done.content
    assert body(done)["second_factor"] == {"has_login": True, "on": False}
    with tenant_transaction(reg.ctx):
        assert SecondFactor.objects.count() == 0
        assert Credential.objects.get(
            actor__public_id=reg.people["pat"][0]).session_epoch == epoch + 1   # signed out
        assert AuditEvent.objects.filter(action="auth.second_factor_reset_patient").count() == 1
    assert reg.web.post(path, subject="desk").status_code == 409      # nothing left to reset


def test_a_patient_of_another_site_cannot_be_reset_from_here():
    reg = Reg()
    reg.patient("far", "Ravi Kumar", "1985-01-02", at_site=False)
    person = with_two_step(reg, "far")
    assert reg.web.post(f"people/{person}/second-factor/reset", subject="desk").status_code in (
        403, 404)


# ------------------------------------------------------------------ DEV-368: patients by region
def test_the_administrator_sees_patients_by_region_and_nobody_else_does(p):
    with tenant_transaction(p.clinic.ctx):
        trust.link_person_to_site(p.person, p.clinic.org, "booking")
        north = trust.create_organization(name="North Site").public_id
        trust.link_person_to_site(p.person, north, "booking")
        trust.set_region(north, "North", by="system")
    named = p.post("staff/settings", {"region": "  City   Centre "}, subject="boss")
    assert named.status_code == 200
    assert body(p.get("staff/settings", subject="boss"))["region"] == "City Centre"
    assert p.post("staff/settings", {"region": "x" * 61}, subject="boss").status_code == 422
    regions = body(p.get("staff/dashboard", subject="boss"))["patients_by_region"]
    by_name = {r["region"]: r for r in regions}
    assert by_name["City Centre"]["patients"] == 1 and by_name["North"]["patients"] == 1
    assert by_name["North"]["sites"] == [{"name": "North Site", "patients": 1}]
    assert body(p.get("staff/dashboard", subject="ops"))["patients_by_region"] is None


# ------------------------------------------------------------------ DEV-369: erase notification
def test_each_patient_is_told_once_when_a_clinic_erases_their_record_pointers(p, settings):
    from modules.clinicalref import retention as ref_retention
    from tests.test_retention_erase import pointer

    comms.register_subscribers()
    pointer(p)
    pointer(p, days_ago=950)
    with tenant_transaction(p.clinic.ctx):
        assert ref_retention.erase_older(utc_now(), frozenset(), p.clinic.org) == 2
    for _ in range(3):
        relay_once(dispatch.dispatch)
    with tenant_transaction(p.clinic.ctx):
        (notice,) = Notification.objects.filter(kind="reference_erased")
    assert notice.person_ref == p.person and "keeping period" in notice.body
    assert "REC-" not in notice.body                            # never which record
