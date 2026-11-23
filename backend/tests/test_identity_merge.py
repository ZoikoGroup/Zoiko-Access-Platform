"""Merging a confirmed duplicate patient record into the surviving one, and undoing it (DEV-365,
ported from the maruthi branch; the last four tests are for what this branch added since)."""
import json
import uuid

import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.comms.models import Notification
from modules.coverage.models import Coverage, InsuranceSharing
from modules.trust import api as trust
from modules.trust.models import Person, PersonMerge, PersonSite
from tests.test_identity import Reg

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def reg():
    return Reg()


def drop_login(reg, subject):
    """Take away a patient's login, so a merge is not stopped by two logins."""
    with tenant_transaction(reg.ctx):
        Person.objects.filter(public_id=reg.person(subject)).update(linked_actor=None)


def note(reg, subject, title="Reminder"):
    with tenant_transaction(reg.ctx):
        row = Notification.objects.create(
            person_ref=reg.person(subject), kind="booking_requested", title=title, body="x",
            source_event_id=uuid.uuid4(), **_scope(reg))
    return row.public_id


def _scope(reg):
    return {"cell_id": reg.ctx.cell_id, "tenant_id": reg.ctx.tenant_id}


def owner_of(reg, public_id):
    with tenant_transaction(reg.ctx):
        return Notification.objects.get(public_id=public_id).person_ref


def linked_pair(reg, **kw):
    """Two records confirmed to be one person, the second without a login. Returns the case ref."""
    reg.pair(**kw)
    drop_login(reg, "pat2")
    reg.decide("confirm_same")
    case = reg.case()
    assert case["state"] == "linked"
    return case["public_id"]


def merge(reg, case, subject="desk", **body):
    return reg.web.post(f"staff/matches/{case}/merge", body, subject=subject)


def unmerge(reg, case, subject="desk"):
    return reg.web.post(f"staff/matches/{case}/unmerge", {}, subject=subject)


def person(reg, subject):
    with tenant_transaction(reg.ctx):
        return Person.objects.select_related("merged_into").get(public_id=reg.person(subject))


# ------------------------------------------------------------------ merging


def test_a_merge_moves_the_ended_records_work_to_the_survivor(reg):
    case = linked_pair(reg)
    mine, theirs = note(reg, "pat1"), note(reg, "pat2")
    done = merge(reg, case)
    assert done.status_code == 200, done.content
    out = json.loads(done.content)
    assert out["case"]["state"] == "linked"
    assert out["merge"]["kept"] == reg.person("pat1")
    assert out["merge"]["ended"] == reg.person("pat2")
    assert out["merge"]["moved"] >= 1 and out["merge"]["state"] == "merged"
    assert owner_of(reg, mine) == reg.person("pat1")
    assert owner_of(reg, theirs) == reg.person("pat1")               # moved to the survivor
    ended = person(reg, "pat2")
    assert ended.status == "ended" and ended.merged_into.public_id == reg.person("pat1")
    assert person(reg, "pat1").status == "active"


def test_the_history_and_board_show_the_merge(reg):
    case = linked_pair(reg)
    merge(reg, case)
    shown = reg.case()
    assert [s["action"] for s in shown["history"]] == ["opened", "confirm_same", "merge"]
    assert shown["chain_ok"] is True and shown["merge"]["state"] == "merged"
    rows = {p["id"]: p for p in json.loads(
        reg.web.get("staff/patients", subject="desk").content)["patients"]}
    assert rows[reg.person("pat2")]["merged_into"] == reg.person("pat1")
    assert rows[reg.person("pat1")]["merged_into"] == ""


def test_the_older_record_survives_unless_the_reviewer_names_one(reg):
    case = linked_pair(reg)
    done = merge(reg, case, primary=reg.person("pat2"))
    assert json.loads(done.content)["merge"]["kept"] == reg.person("pat2")
    assert person(reg, "pat1").merged_into.public_id == reg.person("pat2")


def test_naming_a_record_that_is_not_in_the_case_is_refused(reg):
    case = linked_pair(reg)
    bad = merge(reg, case, primary="not-in-the-case")
    assert bad.status_code == 422
    assert person(reg, "pat2").status == "active"


def test_only_a_confirmed_link_can_be_merged(reg):
    reg.pair()
    drop_login(reg, "pat2")
    case = reg.case()["public_id"]
    assert merge(reg, case).status_code == 409              # still only a possible match
    assert person(reg, "pat2").status == "active"


def test_a_critical_case_cannot_be_merged_before_the_second_reviewer(reg):
    reg.pair(("Anna Smith", "1995-05-05", "+44 7700 900123"),
             ("Bella Smith", "1995-05-05", "+44 7700 900123"))
    drop_login(reg, "pat2")
    reg.decide("confirm_same", subject="desk")
    case = reg.case()["public_id"]
    assert merge(reg, case).status_code == 409              # one reviewer so far
    reg.decide("confirm_same", subject="desk2")
    assert merge(reg, case).status_code == 200


def test_a_merge_cannot_be_done_twice(reg):
    case = linked_pair(reg)
    assert merge(reg, case).status_code == 200
    assert merge(reg, case).status_code == 409


def test_two_records_that_both_have_a_login_are_not_merged(reg):
    reg.pair()
    reg.decide("confirm_same")
    case = reg.case()["public_id"]
    stopped = merge(reg, case)
    assert stopped.status_code == 422
    assert person(reg, "pat2").status == "active" and person(reg, "pat1").status == "active"


def test_the_ended_records_login_opens_the_survivor(reg):
    reg.pair()
    drop_login(reg, "pat1")                  # the older record (the survivor) has no login
    reg.decide("confirm_same")
    case = reg.case()["public_id"]
    login = reg.people["pat2"][0]
    assert merge(reg, case).status_code == 200
    with tenant_transaction(reg.ctx):
        assert trust.own_person_of(login) == reg.person("pat1")
    assert person(reg, "pat2").status == "ended"


def test_the_clinic_connection_moves_to_the_survivor(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12", at_site=False)
    reg.patient("pat2", "Asha Rao", "1990-03-12")        # only the second is known to the clinic
    drop_login(reg, "pat2")
    reg.decide("confirm_same")
    case = reg.case()["public_id"]
    assert merge(reg, case).status_code == 200
    with tenant_transaction(reg.ctx):
        sites = PersonSite.objects.filter(person__public_id=reg.person("pat1"))
        assert [s.organization_ref for s in sites] == [reg.org]


def test_a_row_that_would_clash_is_left_where_it_was_and_counted(reg):
    case = linked_pair(reg)                  # both are already connected to the same clinic
    out = json.loads(merge(reg, case).content)
    assert out["merge"]["left_behind"] >= 1


def test_access_is_not_carried_to_the_survivor(reg):
    case = linked_pair(reg)
    with tenant_transaction(reg.ctx):
        doctor = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="doc")
        trust.assign_role(actor_public_id=doctor.public_id, organization_public_id=reg.org,
                          role_code="R03", by="system")
        trust.start_care_relationship(
            actor_public_id=doctor.public_id, person_public_id=reg.person("pat2"),
            basis=trust.CareBasis.CARE_TEAM, organization_ref=reg.org, by="system")
    out = json.loads(merge(reg, case).content)
    assert out["merge"]["access_not_carried"] == 1
    with tenant_transaction(reg.ctx):
        assert trust.care_team_of(reg.person("pat1")) == []


# ------------------------------------------------------------------ safety


def test_only_clinic_staff_of_this_clinic_may_merge(reg):
    case = linked_pair(reg)
    assert merge(reg, case, subject="pat1").status_code == 403
    with tenant_transaction(reg.ctx):
        other_org = trust.create_organization(name="Other Clinic").public_id
        rival = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="rival")
        trust.assign_role(actor_public_id=rival.public_id, organization_public_id=other_org,
                          role_code="R13", by="system")
    assert merge(reg, case, subject="rival").status_code == 404
    assert person(reg, "pat2").status == "active"


def test_nobody_merges_a_case_about_themselves(reg):
    case = linked_pair(reg)
    with tenant_transaction(reg.ctx):
        with pytest.raises(ZoikoError) as caught:
            trust.merge_people(case_ref=case, actor_public_id=reg.people["pat1"][0],
                               organization_ref=reg.org)
    assert caught.value.code == ErrorCode.POLICY_DENIED


def test_a_merged_link_cannot_be_split_until_it_is_undone(reg):
    case = linked_pair(reg)
    merge(reg, case)
    assert reg.decide("split", subject="desk2", case=case).status_code == 409
    assert unmerge(reg, case).status_code == 200
    assert reg.decide("mark_not_same", subject="desk2", case=case).status_code == 200


# ------------------------------------------------------------------ unmerge


def test_unmerge_moves_back_exactly_what_moved(reg):
    case = linked_pair(reg)
    mine, theirs = note(reg, "pat1", "Mine"), note(reg, "pat2", "Theirs")
    merge(reg, case)
    later = note(reg, "pat1", "Made after the merge")
    done = unmerge(reg, case)
    assert done.status_code == 200, done.content
    assert json.loads(done.content)["case"]["state"] == "split_review"
    assert owner_of(reg, theirs) == reg.person("pat2")        # back where it came from
    assert owner_of(reg, mine) == reg.person("pat1")          # never moved
    assert owner_of(reg, later) == reg.person("pat1")         # made after: stays with the survivor
    again = person(reg, "pat2")
    assert again.status == "active" and again.merged_into is None


def test_unmerge_puts_a_moved_login_back(reg):
    reg.pair()
    drop_login(reg, "pat1")
    reg.decide("confirm_same")
    case = reg.case()["public_id"]
    login = reg.people["pat2"][0]
    merge(reg, case)
    unmerge(reg, case)
    with tenant_transaction(reg.ctx):
        assert trust.own_person_of(login) == reg.person("pat2")
        assert Person.objects.get(public_id=reg.person("pat1")).linked_actor_id is None


def test_unmerge_needs_a_merge_in_force(reg):
    case = linked_pair(reg)
    assert unmerge(reg, case).status_code == 409
    merge(reg, case)
    assert unmerge(reg, case).status_code == 200
    assert unmerge(reg, case).status_code == 409


def test_after_an_unmerge_the_link_can_be_confirmed_and_merged_again(reg):
    case = linked_pair(reg)
    theirs = note(reg, "pat2")
    merge(reg, case)
    unmerge(reg, case)
    reg.decide("confirm_same", subject="desk2", case=case)
    assert reg.case()["state"] == "linked"
    assert merge(reg, case).status_code == 200
    assert owner_of(reg, theirs) == reg.person("pat1")
    assert [s["action"] for s in reg.case()["history"]] == [
        "opened", "confirm_same", "merge", "unmerge", "confirm_same", "merge"]
    assert reg.case()["chain_ok"] is True
    with tenant_transaction(reg.ctx):
        states = sorted(PersonMerge.objects.values_list("state", flat=True))
    assert states == ["merged", "unmerged"]


def test_a_merge_and_an_unmerge_are_audited(reg):
    from modules.evidence.models import AuditEvent
    case = linked_pair(reg)
    merge(reg, case)
    unmerge(reg, case)
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"match.merge", "match.unmerge"} <= actions
    assert reg.events("patient.link.merged.v1") == 1 and reg.events("patient.link.unmerged.v1") == 1


def test_a_patient_is_found_under_the_record_it_was_merged_into(reg):
    case = linked_pair(reg)
    with tenant_transaction(reg.ctx):
        assert trust.canonical_person_ref(reg.person("pat2")) == reg.person("pat2")
    merge(reg, case)
    with tenant_transaction(reg.ctx):
        assert trust.canonical_person_ref(reg.person("pat2")) == reg.person("pat1")
        assert trust.canonical_person_ref(reg.person("pat1")) == reg.person("pat1")
        assert trust.canonical_person_ref("unknown") == "unknown"


def test_insurance_records_move_too(reg):
    case = linked_pair(reg)
    with tenant_transaction(reg.ctx):
        row = Coverage.objects.create(
            person_ref=reg.person("pat2"), insurer_name="Acme", member_ref_enc="x",
            added_by_ref="system", **_scope(reg))
    merge(reg, case)
    with tenant_transaction(reg.ctx):
        assert Coverage.objects.get(pk=row.pk).person_ref == reg.person("pat1")
    unmerge(reg, case)
    with tenant_transaction(reg.ctx):
        assert Coverage.objects.get(pk=row.pk).person_ref == reg.person("pat2")


# ------------------------------------------------------------------ added on this branch (DEV-365)


def test_a_record_with_a_death_recorded_is_not_merged(reg):
    case = linked_pair(reg)
    with tenant_transaction(reg.ctx):
        Person.objects.filter(public_id=reg.person("pat2")).update(died_on="2026-11-01")
    res = merge(reg, case)
    assert res.status_code == 422 and "died_on" in res.content.decode()
    with tenant_transaction(reg.ctx):
        assert not PersonMerge.objects.exists()


def test_records_a_merge_never_moves_stay_and_are_counted(reg):
    """A sharing choice (like clinical notes, bills, claims, data requests and lab exchanges) stays
    on the ended record; the merge says how many such rows it left there."""
    case = linked_pair(reg)
    with tenant_transaction(reg.ctx):
        row = InsuranceSharing.objects.create(
            person_ref=reg.person("pat2"), share_with_staff=True, changed_by_ref="system",
            **_scope(reg))
    res = merge(reg, case)
    assert res.status_code == 200 and json.loads(res.content)["merge"]["not_moved"] == 1
    with tenant_transaction(reg.ctx):
        assert InsuranceSharing.objects.get(pk=row.pk).person_ref == reg.person("pat2")


def test_every_table_with_a_patient_reference_says_whether_it_moves():
    """A new table holding a patient reference must be classified, or a merge would silently leave
    its rows on the ended record without counting them."""
    from django.apps import apps

    from foundation import person_refs
    classified = {*person_refs.holders().values(), *person_refs.kept().values()}
    holding = {m for m in apps.get_models()
               if any(f.name == "person_ref" for f in m._meta.get_fields())
               and m._meta.app_label != "tests"}
    assert holding - classified == set()
    assert not set(person_refs.holders()) & set(person_refs.kept())       # never both


def test_clinical_records_are_never_moved_by_a_merge():
    from foundation import person_refs
    kept = set(person_refs.kept())
    assert {"clinicalnote.ClinicalNote", "clinicalref.ClinicalReference", "billing.Invoice",
            "claim.Claim", "rights.RightsCase", "exchange.ExchangeRequest"} <= kept
