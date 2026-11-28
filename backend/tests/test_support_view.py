"""What a person looking at delivery attempts can and cannot read (GHE-13 T-54, DEV-174)."""

import json

import pytest

from tests.test_api import API
from tests.test_platform import Platform
from tests.test_webhooks import (  # noqa: F401 - fixtures and helpers
    active,
    deliveries,
    dev_login,
    happen,
    p,
    receiver,
    send,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

ALLOWED = {
    "public_id",
    "endpoint",
    "event_type",
    "delivery_id",
    "state",
    "attempts",
    "next_attempt_at",
    "last_status",
    "last_error",
    "created_at",
    "owner",
    "policy_digest",
    "body_digest",
    "delivered_at",
    "owner_name",
}


def test_the_attempt_list_shows_status_and_a_fingerprint_but_never_the_message(p, receiver):  # noqa: F811
    active(p)
    happen(p, ref="secretref77")
    send(p, receiver)
    sent_body = receiver.calls[-1][2].decode()
    raw = p.get("staff/integrations/deliveries", subject="boss").content.decode()
    rows = json.loads(raw)["deliveries"]
    assert rows and [set(r) ^ ALLOWED for r in rows] == [set()] * len(rows)
    assert "body" not in raw.replace("body_digest", "")
    assert "secretref77" not in raw and sent_body not in raw
    assert rows[0]["body_digest"] and rows[0]["attempts"] == 1


def test_support_staff_of_the_platform_cannot_open_a_clinics_attempts(p):  # noqa: F811
    platform = Platform()
    platform.staff("sam", "R11")
    for path in ("staff/integrations/deliveries", "staff/integrations/inbound"):
        r = platform.client.get(API + path, **platform.header(platform.tenant_public_id, "sam"))
        assert r.status_code == 403, path
    assert deliveries(p) == []  # the clinic's own list is untouched



# ------------------------------------------------ regional support's own view (DEV-328)
SUPPORT_ALLOWED = {
    "public_id", "event_type", "state", "attempts", "next_attempt_at", "last_status",
    "last_error", "created_at", "delivered_at",
}


def _support(subject="sam", role="R11"):
    platform = Platform()
    platform.staff(subject, role)
    return platform


def _look(platform, subject="sam"):
    return platform.client.get(API + "platform/delivery-attempts",
                               **platform.header(platform.tenant_public_id, subject))


def test_regional_support_sees_attempts_across_customers_as_status_only(p, receiver):  # noqa: F811
    active(p)
    happen(p, ref="secretref88")
    receiver.status = 500  # the receiver is failing, so the attempt is still open
    send(p, receiver)
    platform = _support()
    r = _look(platform)
    assert r.status_code == 200, r.content
    [row] = [c for c in r.json()["customers"] if c["id"] == p.clinic.tenant_public_id]
    assert row["attempts"] and [set(a) ^ SUPPORT_ALLOWED for a in row["attempts"]] == [set()]
    raw = r.content.decode()
    assert "secretref88" not in raw and "body" not in raw and "owner" not in raw
    assert "http" not in raw  # never the receiving address
    assert sum(row["counts"].values()) == 1 and "fhir_held" in row


def test_only_zoikos_own_staff_may_look_and_each_look_is_recorded(p):  # noqa: F811
    from foundation.cell_context import tenant_transaction
    from modules.evidence.models import AuditEvent

    platform = _support()
    assert _look(platform).status_code == 200
    with tenant_transaction(platform.ctx):
        assert AuditEvent.objects.filter(action="platform.delivery_attempts_viewed").count() == 1
    assert p.get("platform/delivery-attempts", subject="boss").status_code == 403  # a customer
