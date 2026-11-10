"""A service reaches patients only after it is requested, reviewed by a second person and published
(GHE-14 WF-04: DRAFT, EVIDENCE_PENDING, REVIEWED, PUBLISHED; a provider cannot approve its own
listing). DEV-115."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    practice.login("boss2", trust.ActorType.FACILITY_USER, "R13")
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def make(p, name="Dermatology", subject="ops"):
    r = p.post("services", {"organization": p.clinic.org, "name": name, "category": "dermatology",
                            "duration_minutes": 20}, subject=subject)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["service"]["id"]


def listing(p, service, action, subject, note=""):
    return p.post(f"services/{service}/listing", {"action": action, "note": note}, subject=subject)


def state_of(p, service):
    rows = json.loads(p.get("services?all=1", subject="boss").content)["services"]
    return next(s for s in rows if s["id"] == service)["listing_state"]


def offered_names(p):
    return [s["name"] for s in json.loads(p.get("services", subject="pat").content)["services"]]


def add_times(p, service):
    r = p.post(f"services/{service}/slots", {
        "location": p.clinic.location, "first_local": "2027-09-01T09:00:00", "count": 2},
        subject="ops")
    assert r.status_code == 201, r.content


def first_slot(p, service):
    r = p.get(f"services/{service}/slots", subject="pat")
    return json.loads(r.content)["slots"][0]["id"]


def publish_fully(p, service):
    assert listing(p, service, "request", "ops", "Registration no. 12345").status_code == 200
    assert listing(p, service, "review", "boss").status_code == 200
    assert listing(p, service, "publish", "boss2").status_code == 200


# ------------------------------------------------------------------ the starting point
def test_a_service_made_in_the_app_starts_as_a_draft_nobody_can_book(p):
    service = make(p)
    add_times(p, service)
    assert state_of(p, service) == "draft"
    assert "Dermatology" not in offered_names(p)
    assert book(p, first_slot(p, service), subject="pat").status_code == 422


def test_existing_services_stay_offered(p):
    assert "General check-up" in offered_names(p)           # made before listing states existed


# ------------------------------------------------------------------ the path
def test_request_review_publish_makes_it_bookable(p):
    service = make(p)
    add_times(p, service)
    publish_fully(p, service)
    assert state_of(p, service) == "published" and "Dermatology" in offered_names(p)
    assert book(p, first_slot(p, service), subject="pat").status_code == 201


def test_each_step_needs_the_one_before_it(p):
    service = make(p)
    assert listing(p, service, "review", "boss").status_code == 409        # nothing requested
    assert listing(p, service, "publish", "boss").status_code == 409       # nothing reviewed
    listing(p, service, "request", "ops", "Registration no. 12345")
    assert listing(p, service, "request", "ops", "Registration no. 12345").status_code == 409
    assert listing(p, service, "publish", "boss").status_code == 409       # not reviewed yet


def test_a_request_needs_a_real_evidence_reference(p):
    service = make(p)
    for note in ("", "no", "x" * 201):
        assert listing(p, service, "request", "ops", note).status_code == 422
    assert state_of(p, service) == "draft"


# ------------------------------------------------------------------ nobody approves their own
def test_the_person_who_asked_cannot_review_or_publish_it(p):
    service = make(p)
    listing(p, service, "request", "boss", "Registration no. 12345")        # an administrator asks
    assert listing(p, service, "review", "boss").status_code == 403         # but not for themselves
    assert listing(p, service, "review", "boss2").status_code == 200
    assert listing(p, service, "publish", "boss").status_code == 403
    assert listing(p, service, "publish", "boss2").status_code == 200


def test_only_a_clinic_administrator_can_review_publish_or_suspend(p):
    service = make(p)
    listing(p, service, "request", "ops", "Registration no. 12345")
    for action in ("review", "publish", "suspend"):
        assert listing(p, service, action, "ops", "a reason").status_code == 403
    for action in ("request", "review"):
        assert listing(p, service, action, "pat", "Registration no. 1").status_code == 403


def test_sending_it_back_needs_a_reason_and_returns_it_to_draft(p):
    service = make(p)
    listing(p, service, "request", "ops", "Registration no. 12345")
    assert listing(p, service, "return", "boss").status_code == 422
    assert listing(p, service, "return", "boss", "The licence number is missing").status_code == 200
    assert state_of(p, service) == "draft"


# ------------------------------------------------------------------ withdrawing
def test_a_suspended_service_takes_no_new_bookings_and_can_start_again(p):
    service = make(p)
    add_times(p, service)
    publish_fully(p, service)
    slot = first_slot(p, service)
    assert listing(p, service, "suspend", "boss").status_code == 422        # a reason is required
    assert listing(p, service, "suspend", "boss", "Licence under review").status_code == 200
    assert "Dermatology" not in offered_names(p)
    assert book(p, slot, subject="pat").status_code == 422
    assert listing(p, service, "restart", "boss").status_code == 200
    assert state_of(p, service) == "draft"
    assert listing(p, service, "publish", "boss").status_code == 409    # a full new pass is needed


def test_the_clinics_own_switch_still_hides_a_published_service(p):
    service = make(p)
    publish_fully(p, service)
    assert "Dermatology" in offered_names(p)
    p.post(f"services/{service}/status", {"status": "inactive"}, subject="ops")
    assert "Dermatology" not in offered_names(p)


# ------------------------------------------------------------------ other doors
def test_a_doctor_cannot_refer_to_an_unpublished_service(p):
    p.add_times(p.doc)
    booking = book(p, p.slot_of("doc"), subject="pat").json()["booking"]
    p.post(f"bookings/{booking['id']}/confirm", subject="ops")
    service = make(p, "Cardiology")
    body = {"patient": p.person, "service": service, "reason": "Chest pain"}
    assert p.post("clinician/referrals", body, subject="doc").status_code == 422
    publish_fully(p, service)
    assert p.post("clinician/referrals", body, subject="doc").status_code == 201


def test_every_listing_step_is_audited(p):
    service = make(p)
    publish_fully(p, service)
    with tenant_transaction(p.clinic.ctx):
        actions = list(AuditEvent.objects.filter(object_ref=service).order_by("chain_seq")
                       .values_list("action", flat=True))
    assert actions == ["service.create", "service.listing_request", "service.listing_review",
                       "service.listing_publish"]
