"""The circuit breaker for the text provider (DEV-200; GHE-16 TC42)."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.models import ProviderCircuit, QueuedText
from foundation.texts import BREAK_AFTER, BREAK_FOR, circuit_state, queue_text, send_due_texts
from foundation.timeutil import FixedClock, utc_now
from tests.test_clinician import Practice
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def texts(n):
    p = Practice()
    with tenant_transaction(p.clinic.ctx):
        for _ in range(n):
            queue_text(kind="test", to_number="+447700900123", template="update")


def broken(number, body):
    raise ConnectionError("provider down")


def working(number, body):
    return "SM1"


def test_the_breaker_opens_after_failures_in_a_row_and_the_rest_wait_untouched():
    texts(8)
    assert send_due_texts(send=broken, limit=20) == 0
    state = circuit_state()
    assert state["breaker"] == "open" and state["failures_in_a_row"] == BREAK_AFTER
    tried = QueuedText.objects.filter(attempts=1).count()
    untouched = QueuedText.objects.filter(attempts=0, state="queued").count()
    assert tried == BREAK_AFTER and untouched == 8 - BREAK_AFTER   # the rest kept their tries


def test_while_open_nothing_is_sent_at_all():
    texts(6)
    send_due_texts(send=broken)
    calls: list[str] = []

    def send(number: str, body: str) -> str:
        calls.append(number)
        return "SM1"

    assert send_due_texts(send=send) == 0
    assert calls == []


def test_after_the_pause_sending_resumes_and_a_success_closes_it():
    texts(6)
    send_due_texts(send=broken)
    later = FixedClock(utc_now() + BREAK_FOR + dt.timedelta(minutes=1))
    QueuedText.objects.update(next_attempt_at=utc_now())
    assert send_due_texts(send=working, clock=later) >= 1
    row = ProviderCircuit.objects.get(name="texts")
    assert row.failures == 0 and row.open_until is None and row.opened_count == 1


def test_a_success_between_failures_resets_the_count():
    texts(1)
    send_due_texts(send=broken)
    QueuedText.objects.update(next_attempt_at=utc_now())
    send_due_texts(send=working)
    assert circuit_state()["failures_in_a_row"] == 0 and circuit_state()["breaker"] == "closed"


def test_the_platform_sees_the_breaker_and_the_counts_but_no_numbers():
    texts(2)
    send_due_texts(send=working)
    world = Platform()
    world.staff("sam", "R11")
    r = world.client.get("/api/v1/platform/slo", **world.header(world.tenant_public_id, "sam"))
    assert r.status_code == 200
    got = json.loads(r.content)["texts"]
    assert got["breaker"] == "closed" and got["queue"]["sent"] == 2
    assert "7700" not in r.content.decode()
