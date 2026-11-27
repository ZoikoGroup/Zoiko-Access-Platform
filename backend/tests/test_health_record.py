"""The patient's health record in one place (DEV-362): results, visit notes and records held by
clinics, the same rights and delays as their own screens, and the download."""
import json

import pytest

from tests import test_exchange as exchange
from tests.test_api import API
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def health(p, who="pat"):
    r = p.get("my/health", subject=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def test_a_new_patient_has_an_empty_record():
    got = health(Practice())
    assert got["counts"] == {"notes": 0, "results": 0, "values_ready": 0, "references": 0}
    assert got["notes"] == [] and got["results"] == []


def test_only_patients_and_guardians_use_it():
    p = Practice()
    for who in ("ops", "doc"):
        assert p.get("my/health", subject=who).status_code == 403, who
    assert p.client.get(API + "my/health").status_code == 401
    assert p.post("my/health", {}, subject="pat").status_code == 405


def test_a_result_appears_once_the_service_delivers_it_and_never_before(world):
    _, clinic, lab = world
    exchange.give_profile(clinic)
    request_id = exchange.made(clinic, lab)
    assert health(clinic)["results"] == []                    # asked, nothing back yet
    exchange.deliver_all_the_way(lab, request_id)
    (row,) = health(clinic)["results"]
    assert row["id"] == request_id and row["state"] == "result_available"
    assert row["values_ready"] is False                       # a doctor has not reviewed it
    counts = health(clinic)["counts"]
    assert (counts["results"], counts["values_ready"]) == (1, 0)


def test_another_patient_never_sees_this_record(world):
    _, clinic, lab = world
    exchange.give_profile(clinic)
    exchange.deliver_all_the_way(lab, exchange.made(clinic, lab))
    clinic.patient("other")
    assert health(clinic, "other")["results"] == []


def test_the_download_now_holds_the_record_sections():
    p = Practice()
    data = json.loads(p.get("my/export", subject="pat").content)
    assert {"visit_notes", "tests_and_results", "records_held_by_clinics"} <= set(data)


world = exchange.world
