import json
from datetime import timedelta

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.trust.models import Actor, Credential
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def settings_of(p, subject="ops"):
    r = p.get("staff/settings", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def verified(p, actor_ref, address, when=None):
    with tenant_transaction(p.clinic.ctx):
        Credential.objects.create(
            actor=Actor.objects.get(public_id=actor_ref), login=address, password_hash="x",
            email=address, email_verified_at=when)


def test_it_shows_the_rules_in_force(p, settings):
    settings.RESPONSE_TARGET_HOURS = 12
    settings.REMINDER_LEAD_HOURS = 6
    settings.WAITLIST_OFFER_HOURS = 3
    got = settings_of(p)
    assert (got["response_target_hours"], got["reminder_lead_hours"],
            got["waitlist_offer_hours"]) == (12, 6, 3)


def test_it_counts_who_would_get_the_late_email(p):
    assert settings_of(p)["late_email_recipients"] == 0
    verified(p, p.desk, "desk@clinic.test", utc_now() - timedelta(days=1))
    assert settings_of(p)["late_email_recipients"] == 1


def test_an_unverified_address_or_a_doctor_is_not_counted(p):
    verified(p, p.desk, "desk@clinic.test", None)
    verified(p, p.doc, "doc@clinic.test", utc_now())
    assert settings_of(p)["late_email_recipients"] == 0


def test_no_address_is_ever_shown(p):
    verified(p, p.desk, "desk@clinic.test", utc_now())
    body = p.get("staff/settings", subject="ops").content.decode()
    assert "desk@clinic.test" not in body and "@" not in body


def test_only_staff_may_see_it(p):
    for who in ("pat", "doc"):
        assert p.get("staff/settings", subject=who).status_code == 403
