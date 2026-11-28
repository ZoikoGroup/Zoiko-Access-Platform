"""Lists that used to stop silently at 200 rows now page on request (DEV-337)."""
import json

import pytest

from tests.test_fhir_api import dev_login, p, partner, send_fhir  # noqa: F401 - fixtures
from tests.test_webhooks import active, happen, receiver, send  # noqa: F401 - fixtures

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def held(p, n):  # noqa: F811
    c = partner(p)
    for i in range(n):
        raw = json.dumps({"resourceType": "ServiceRequest", "status": "active", "n": i}).encode()
        assert send_fhir(c, raw, f"fhir-page-{i:04d}").status_code == 202


def test_held_fhir_messages_page_to_the_end_and_old_screens_still_get_everything(p):  # noqa: F811
    held(p, 3)
    whole = p.get("staff/fhir/quarantine?state=held", subject="boss").json()
    assert len(whole["items"]) == 3 and "next_cursor" not in whole  # as before
    first = p.get("staff/fhir/quarantine?state=held&limit=2", subject="boss").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = p.get("staff/fhir/quarantine?state=held&limit=2&cursor=" + first["next_cursor"],
                 subject="boss").json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    seen = [i["public_id"] for i in first["items"] + rest["items"]]
    assert sorted(seen) == sorted(i["public_id"] for i in whole["items"])  # nothing twice


def test_a_cursor_works_only_for_its_own_list_and_its_own_login(p):  # noqa: F811
    held(p, 3)
    cursor = p.get("staff/fhir/quarantine?state=held&limit=1", subject="boss").json()["next_cursor"]
    other_list = p.get(f"staff/fhir/quarantine?state=discarded&limit=1&cursor={cursor}",
                       subject="boss")
    assert other_list.status_code == 422
    forged = p.get(f"staff/fhir/quarantine?limit=1&cursor={cursor}x", subject="boss")
    assert forged.status_code == 422
    assert p.get("staff/fhir/quarantine?limit=500", subject="boss").status_code == 422


def test_webhook_deliveries_page_too(p, receiver):  # noqa: F811
    active(p)
    for i in range(3):
        happen(p, ref=f"pageref{i}")
    send(p, receiver)
    first = p.get("staff/integrations/deliveries?limit=2", subject="boss").json()
    assert len(first["deliveries"]) == 2 and first["next_cursor"]
    rest = p.get(f"staff/integrations/deliveries?limit=2&cursor={first['next_cursor']}",
                 subject="boss").json()
    assert len(rest["deliveries"]) == 1 and rest["next_cursor"] is None

