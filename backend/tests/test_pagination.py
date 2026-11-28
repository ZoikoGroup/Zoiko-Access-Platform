"""Cursor paging cannot leak records when a right is taken away between pages (DEV-171)."""

import pytest

from foundation import pagination
from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests.test_exchange import body, dev_login, made, world  # noqa: F401 - fixtures

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def page(clinic, subject="ops", **query):
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    return clinic.get("exchange/requests?" + qs, subject=subject)


def fill(clinic, lab, n=5):
    return [made(clinic, lab, token=f"ORD-{i:04d}") for i in range(n)]


def test_pages_cover_every_row_once_newest_first(world):  # noqa: F811
    _, clinic, lab = world
    ids = fill(clinic, lab, 5)
    seen, cursor = [], None
    for _ in range(5):
        r = page(clinic, limit=2, **({"cursor": cursor} if cursor else {}))
        assert r.status_code == 200
        data = body(r)
        seen += [x["id"] for x in data["requests"]]
        cursor = data.get("next_cursor")
        if not cursor:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen)) == 5
    assert seen == list(reversed(ids))


def test_without_a_limit_the_old_shape_is_unchanged(world):  # noqa: F811
    _, clinic, lab = world
    fill(clinic, lab, 3)
    data = body(clinic.get("exchange/requests", subject="ops"))
    assert len(data["requests"]) == 3 and "next_cursor" not in data


def test_a_bad_limit_or_a_forged_cursor_is_refused(world):  # noqa: F811
    _, clinic, lab = world
    fill(clinic, lab, 3)
    for bad in ("0", "101", "abc"):
        assert page(clinic, limit=bad).status_code == 422
    assert page(clinic, limit=2, cursor="not-a-cursor").status_code == 422
    good = body(page(clinic, limit=2))["next_cursor"]
    assert page(clinic, limit=2, cursor=good[:-3] + "xxx").status_code == 422


def test_a_cursor_given_to_one_login_does_not_work_for_another(world):  # noqa: F811
    _, clinic, lab = world
    fill(clinic, lab, 3)
    cursor = body(page(clinic, subject="ops", limit=1))["next_cursor"]
    assert page(clinic, subject="doc2", limit=1, cursor=cursor).status_code == 422


def test_an_old_cursor_expires(world, monkeypatch):  # noqa: F811
    _, clinic, lab = world
    fill(clinic, lab, 3)
    cursor = body(page(clinic, limit=1))["next_cursor"]
    real = pagination.signing.loads
    # the same check, as if the cursor were older than its lifetime
    monkeypatch.setattr(
        pagination.signing, "loads", lambda t, **kw: real(t, **{**kw, "max_age": -1})
    )
    assert page(clinic, limit=1, cursor=cursor).status_code == 422


def test_a_right_taken_away_between_pages_stops_the_next_page(world):  # noqa: F811
    _, clinic, lab = world
    fill(clinic, lab, 4)
    first = page(clinic, subject="doc", limit=2)
    assert first.status_code == 200 and len(body(first)["requests"]) == 2
    cursor = body(first)["next_cursor"]
    with tenant_transaction(clinic.clinic.ctx):
        trust.end_care_links(
            actor_public_id=clinic.doc,
            person_public_id=clinic.person,
            basis=trust.CareBasis.CARE_TEAM,
            organization_ref=clinic.clinic.org,
            by="system",
        )
    second = page(clinic, subject="doc", limit=2, cursor=cursor)
    assert second.status_code == 200 and body(second)["requests"] == []
    assert body(second).get("next_cursor") is None


def test_the_partner_list_pages_too_and_its_cursor_is_its_own(world):  # noqa: F811
    _, clinic, lab = world
    ids = fill(clinic, lab, 3)
    first = body(lab.call("get", "exchange/incoming?limit=2"))
    assert len(first["requests"]) == 2 and first["next_cursor"]
    rest = body(lab.call("get", f"exchange/incoming?limit=2&cursor={first['next_cursor']}"))
    got = [r["id"] for r in first["requests"] + rest["requests"]]
    assert got == list(reversed(ids)) and rest.get("next_cursor") is None
    # a cursor from the clinic's list is not valid for the partner's list
    mine = body(page(clinic, limit=1))["next_cursor"]
    assert lab.call("get", f"exchange/incoming?limit=1&cursor={mine}").status_code == 422
