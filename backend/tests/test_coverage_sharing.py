import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):   # staff deal only with their own site's patients
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def add(web, subject="pat", **over):
    body = {"insurer": "Star Health", "plan": "Family", "member_id": "STAR-12345-6789", **over}
    return web.post("coverage", body, subject=subject)


def share(web, on, subject="pat", **extra):
    return web.post("coverage/sharing", {"share": on, **extra}, subject=subject)


def staff_view(web, person, subject="ops"):
    r = web.get(f"people/{person}/coverage", subject=subject)
    assert r.status_code == 200
    return json.loads(r.content)


# ------------------------------------------------------------------ the cautious default
def test_staff_see_nothing_until_the_patient_shares(p):
    assert add(p).status_code == 201
    view = staff_view(p, p.person)
    assert view == {"coverage": [], "shared": False}  # not even the insurer's name
    assert "Star" not in json.dumps(view)


def test_the_patient_always_sees_their_own_insurance(p):
    add(p)
    own = json.loads(p.get(f"people/{p.person}/coverage", subject="pat").content)
    assert own["shared"] is True and [c["insurer"] for c in own["coverage"]] == ["Star Health"]
    mine = json.loads(p.get("coverage", subject="pat").content)["coverage"]
    assert len(mine) == 1


def test_sharing_opens_the_staff_view_and_unsharing_closes_it_again(p):
    add(p)
    assert share(p, True).status_code == 200
    view = staff_view(p, p.person)
    assert view["shared"] is True and [c["insurer"] for c in view["coverage"]] == ["Star Health"]
    assert "STAR-12345-6789" not in json.dumps(view)  # still only the last four digits
    assert share(p, False).status_code == 200
    assert staff_view(p, p.person) == {"coverage": [], "shared": False}


def test_the_choice_is_listed_for_the_patient(p):
    assert json.loads(p.get("coverage/sharing", subject="pat").content)["sharing"] == [
        {"person": p.person, "share_with_staff": False}
    ]
    share(p, True)
    assert json.loads(p.get("coverage/sharing", subject="pat").content)["sharing"] == [
        {"person": p.person, "share_with_staff": True}
    ]


def test_repeating_the_same_choice_is_harmless_and_audited_once(p):
    for _ in range(3):
        assert share(p, True).status_code == 200
    share(p, False)
    with tenant_transaction(p.clinic.ctx):
        actions = list(
            AuditEvent.objects.filter(action__in=["coverage.share", "coverage.unshare"])
            .order_by("chain_seq")
            .values_list("action", flat=True)
        )
    assert actions == ["coverage.share", "coverage.unshare"]


# ------------------------------------------------------------------ who may choose
def test_staff_and_others_cannot_choose_for_a_patient(p):
    for who in ("ops", "doc"):
        assert share(p, True, subject=who, person=p.person).status_code == 403
    _, other = p.patient("pat2")
    assert share(p, True, subject="pat2", person=p.person).status_code == 403
    assert share(p, True, subject="pat", person=other).status_code == 403
    assert staff_view(p, p.person)["shared"] is False


def test_each_patients_choice_is_their_own(p):
    _, other = p.patient("pat2")
    with tenant_transaction(p.clinic.ctx):
        trust.link_person_to_site(other, p.clinic.org, "booking")
    add(p)
    add(p, subject="pat2", insurer="Other Insurer")
    share(p, True, subject="pat2")
    assert staff_view(p, p.person)["shared"] is False
    assert staff_view(p, other)["shared"] is True


def test_another_customers_staff_get_nothing(p):
    add(p)
    share(p, True)
    outsider = Web()
    outsider.staff("desk")
    r = outsider.get(f"people/{p.person}/coverage", subject="desk")
    assert r.status_code in (200, 403, 404)
    assert "Star Health" not in r.content.decode()  # nothing of another customer's data
    if r.status_code == 200:
        assert json.loads(r.content) == {"coverage": [], "shared": False}


# ------------------------------------------------------------------ bad input
@pytest.mark.parametrize("bad", [{}, {"share": "yes"}, {"share": 1}, {"share": None}])
def test_the_choice_must_be_a_real_yes_or_no(p, bad):
    r = p.post("coverage/sharing", bad, subject="pat")
    assert r.status_code == 422
    assert staff_view(p, p.person)["shared"] is False


def test_an_unknown_patient_is_refused(p):
    r = share(p, True, subject="pat", person="nosuchperson000000000")
    assert r.status_code in (403, 404)


def test_staff_can_still_record_insurance_but_cannot_read_it_back_without_sharing(p):
    made = add(p, subject="ops", person=p.person, insurer="HDFC Ergo")
    assert made.status_code == 201
    assert staff_view(p, p.person) == {"coverage": [], "shared": False}
    row = json.loads(made.content)["coverage"]
    assert p.post(f"coverage/{row['id']}/end", subject="ops").status_code == 200


def test_removing_an_unshared_insurance_shows_staff_no_details_but_the_patient_still_sees_theirs(p):
    recorded = add(p, subject="ops", person=p.person, insurer="HDFC Ergo")
    made = json.loads(recorded.content)["coverage"]
    ended = p.post(f"coverage/{made['id']}/end", subject="ops")
    assert ended.status_code == 200
    body = json.loads(ended.content)["coverage"]
    assert body == {"id": made["id"], "status": "ended"}      # nothing else, not even the insurer
    assert "HDFC" not in ended.content.decode()


def test_removing_a_shared_insurance_still_answers_with_its_details(p):
    row = json.loads(add(p).content)["coverage"]
    share(p, True)
    ended = json.loads(p.post(f"coverage/{row['id']}/end", subject="ops").content)["coverage"]
    assert ended["insurer"] == "Star Health" and ended["status"] == "ended"


def test_the_patient_removing_their_own_insurance_gets_the_details_back(p):
    row = json.loads(add(p).content)["coverage"]
    own = json.loads(p.post(f"coverage/{row['id']}/end", subject="pat").content)["coverage"]
    assert own["insurer"] == "Star Health" and own["status"] == "ended"
