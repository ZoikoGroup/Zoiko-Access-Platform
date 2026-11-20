"""Staff deal only with patients connected to their own clinic site (audit item 1, DEV-107)."""
import json

import pytest

from foundation.cell_context import tenant_transaction
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
    return Practice()


def staff_reads(p, person):
    return p.get(f"people/{person}/coverage", subject="ops").status_code


def connected(p, person):
    with tenant_transaction(p.clinic.ctx):
        return trust.person_at_site(person, p.clinic.org)


def test_staff_cannot_reach_a_patient_who_has_no_link_to_their_site(p):
    assert not connected(p, p.person)
    assert staff_reads(p, p.person) == 403


def test_a_booking_connects_the_patient_to_that_site_and_staff_can_then_reach_them(p):
    p.add_times(p.doc)
    assert book(p, p.slot_of("doc"), subject="pat").status_code == 201
    assert connected(p, p.person)
    assert staff_reads(p, p.person) == 200


def test_the_link_is_per_site_not_per_customer(p):
    p.add_times(p.doc)
    book(p, p.slot_of("doc"), subject="pat")
    with tenant_transaction(p.clinic.ctx):
        other_site = next(o for o in trust.organization_names() if o != p.clinic.org)
        assert not trust.person_at_site(p.person, other_site)


def test_recording_the_same_link_twice_is_harmless(p):
    with tenant_transaction(p.clinic.ctx):
        trust.link_person_to_site(p.person, p.clinic.org, "booking")
        trust.link_person_to_site(p.person, p.clinic.org, "message")
    assert connected(p, p.person)
    assert json.dumps(staff_reads(p, p.person)) == "200"


def test_an_unknown_reason_is_refused(p):
    from foundation.errors import ZoikoError
    with tenant_transaction(p.clinic.ctx), pytest.raises(ZoikoError):
        trust.link_person_to_site(p.person, p.clinic.org, "curiosity")
