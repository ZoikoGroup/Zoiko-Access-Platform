import json
from urllib.parse import urlencode

import pytest

from tests.conftest import publish_service
from tests.test_api import Web

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def web():
    """A clinic that already offers 'General check-up', plus three more services of our own."""
    w = Web()
    w.patient("pat")
    w.staff("desk")
    w.add("Skin clinic", "dermatology", "in_person", "Rashes, acne and moles",
          slots_on="2027-09-02")
    w.add("Heart check", "cardiology", "video", "Blood pressure and heart rhythm",
          slots_on="2027-09-01")
    w.add("Eye test", "ophthalmology", "in_person", "Vision and glasses", slots_on=None)
    return w


def add(self, name, category, mode, description, slots_on):
    made = self.post("services", {
        "organization": self.clinic.org, "name": name, "category": category,
        "duration_minutes": 20, "mode": mode, "description": description}, subject="desk")
    assert made.status_code == 201, made.content
    service = json.loads(made.content)["service"]["id"]
    publish_service(self.clinic.ctx, service)
    if slots_on:
        r = self.post(f"services/{service}/slots", {
            "location": self.clinic.location, "first_local": f"{slots_on}T09:00:00", "count": 1},
            subject="desk")
        assert r.status_code == 201
    return service


Web.add = add


def search(web, **params):
    r = web.get("services" + (f"?{urlencode(params)}" if params else ""), subject="pat")
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def names(body):
    return [s["name"] for s in body["services"]]


# ------------------------------------------------------------------ searching
def test_search_looks_in_the_name_the_description_and_the_category(web):
    assert names(search(web, q="heart")) == ["Heart check"]                  # name
    assert names(search(web, q="acne")) == ["Skin clinic"]                   # description
    assert names(search(web, q="ophthalmology")) == ["Eye test"]             # category
    assert names(search(web, q="Ophthalmology")) == ["Eye test"]             # any case
    assert names(search(web, q="no such thing")) == []


def test_everything_is_listed_by_name_without_a_search(web):
    assert names(search(web)) == ["Eye test", "General check-up", "Heart check", "Skin clinic"]


# ------------------------------------------------------------------ filters
def test_the_category_filter_is_exact_and_forgiving_about_spelling(web):
    assert names(search(web, category="cardiology")) == ["Heart check"]
    assert names(search(web, category="Cardiology")) == ["Heart check"]
    assert names(search(web, category="card")) == []                          # exact, not partial


def test_the_mode_filter_separates_video_from_in_person(web):
    assert names(search(web, mode="video")) == ["Heart check"]
    assert names(search(web, mode="in_person")) == ["Eye test", "General check-up", "Skin clinic"]


def test_an_unknown_mode_or_sort_is_refused(web):
    for params in ({"mode": "telepathy"}, {"sort": "price"}):
        r = web.get("services?" + urlencode(params), subject="pat")
        assert r.status_code == 422, params


def test_only_services_with_a_free_time_can_be_asked_for(web):
    assert names(search(web, available="1")) == ["General check-up", "Heart check", "Skin clinic"]


def test_filters_combine(web):
    both = search(web, available="1", mode="in_person")
    assert names(both) == ["General check-up", "Skin clinic"]
    assert names(search(web, q="check", mode="video")) == ["Heart check"]
    assert names(search(web, category="dermatology", mode="video")) == []


# ------------------------------------------------------------------ order
def test_sorting_by_soonest_puts_the_nearest_free_time_first_and_no_time_last(web):
    ordered = names(search(web, sort="soonest"))
    soonest = [ordered.index(n) for n in ("General check-up", "Heart check", "Skin clinic")]
    assert soonest == sorted(soonest)
    assert ordered[-1] == "Eye test"                                          # nothing free: last


# ------------------------------------------------------------------ the filter menu
def test_the_category_menu_counts_active_services_only(web):
    body = search(web)
    counts = {c["category"]: c["count"] for c in body["categories"]}
    assert counts["cardiology"] == 1 and counts["dermatology"] == 1
    eye = [s for s in body["services"] if s["name"] == "Eye test"][0]["id"]
    off = web.post(f"services/{eye}/status", {"status": "inactive"}, subject="desk")
    assert off.status_code == 200
    body = search(web)
    assert "ophthalmology" not in {c["category"] for c in body["categories"]}
    assert "Eye test" not in names(body)


def test_another_customers_services_never_appear(web):
    other = Web()
    other.patient("pat")
    other.staff("desk")
    other.add("Other clinic only", "neurology", "video", "Brain", slots_on="2027-09-01")
    assert "Other clinic only" not in names(search(web))
    assert "neurology" not in {c["category"] for c in search(web)["categories"]}


def test_a_very_long_search_text_is_cut_not_crashed(web):
    assert search(web, q="x" * 5000)["services"] == []
