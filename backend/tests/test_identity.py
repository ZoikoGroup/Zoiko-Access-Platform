import datetime as dt
import json

import pytest
from django.db import DatabaseError, transaction

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import OutboxEvent
from foundation.timeutil import UTC, FixedClock
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust.identity_jobs import refresh_all_tenants
from modules.trust.models import MatchCase, MatchDecision, Person, PersonProfile
from tests.conftest import as_app_role
from tests.test_api import API, Web

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


class Reg:
    """A clinic with two receptionists and an administrator, and as many patients as needed."""

    def __init__(self):
        self.web = Web()
        self.ctx = self.web.clinic.ctx
        self.org = self.web.clinic.org
        self.desk = self.web.staff("desk")
        self.desk2 = self.web.login("desk2", "facility_user", "R04")
        self.boss = self.web.login("boss", "facility_user", "R13")
        self.people: dict[str, tuple[str, str]] = {}

    def patient(self, subject, name="", birth="", phone="", at_site=True):
        actor, person = self.web.patient(subject)
        self.people[subject] = (actor, person)
        if at_site:
            with tenant_transaction(self.ctx):
                trust.link_person_to_site(person, self.org, "booking")
        if name:
            self.save(subject, name, birth, phone)
        return person

    def save(self, subject, name, birth="", phone=""):
        person = self.people[subject][1]
        done = self.web.post(f"people/{person}/profile",
                             {"name": name, "birth_date": birth, "phone": phone}, subject=subject)
        assert done.status_code == 200, done.content
        return done

    def pair(self, a=("Asha Rao", "1990-03-12", ""), b=("Asha Rao", "1990-03-12", "")):
        self.patient("pat1", *a)
        self.patient("pat2", *b)

    def board(self, subject="desk"):
        r = self.web.get("staff/matches", subject=subject)
        assert r.status_code == 200, r.content
        return json.loads(r.content)

    def case(self, subject="desk"):
        (only,) = self.board(subject)["cases"]
        return only

    def decide(self, action, subject="desk", note="", case=None):
        case = case or self.case()["public_id"]
        return self.web.post(f"staff/matches/{case}/decide", {"action": action, "note": note},
                             subject=subject)

    def state(self):
        return self.case()["state"]

    def person(self, subject):
        return self.people[subject][1]

    def check(self, subject, subject_of_staff="desk", evidence="passport", outcome="passed",
              **extra):
        return self.web.post(f"people/{self.person(subject)}/identity/check",
                             {"evidence_type": evidence, "outcome": outcome, **extra},
                             subject=subject_of_staff)

    def identity(self, subject, as_subject="desk"):
        r = self.web.get(f"people/{self.person(subject)}/identity", subject=as_subject)
        assert r.status_code == 200, r.content
        return json.loads(r.content)

    def events(self, kind):
        return OutboxEvent.objects.filter(event_type=kind).count()


@pytest.fixture
def reg():
    return Reg()


# ------------------------------------------------------------------ finding possible duplicates
def test_two_records_that_look_like_one_person_are_held_for_review(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    assert reg.board()["cases"] == []
    reg.patient("pat2", "Asha Rao", "1990-03-12")
    board = reg.board()
    (case,) = board["cases"]
    assert (case["state"], case["confidence"], case["critical"], case["ref_no"]) == (
        "possible_match", "high", False, "M-0001")
    assert case["factors"] == {"name": "same", "birth_date": "same", "phone": "missing"}
    assert case["flags"] == [] and board["can_decide"] is True
    assert [p["at_this_clinic"] for p in case["people"]] == [True, True]
    assert reg.events("patient.link.review_required.v1") == 1


def test_the_review_screen_shows_only_masked_details(reg):
    reg.pair(("Asha Rao", "1990-03-12", "+44 7700 900123"),
             ("Asha Rao", "1990-03-12", "+44 7700 900123"))
    (first, second) = reg.board()["cases"][0]["people"]
    for masked in (first, second):                        # nothing here could hold a full name
        assert set(masked) == {"id", "at_this_clinic", "initials", "birth_year", "phone_end"}
    assert (first["initials"], first["birth_year"], first["phone_end"]) == ("A. R.", "1990", "23")
    assert second["initials"] == "A. R."


def test_people_who_are_not_alike_are_never_held(reg):
    reg.pair(("Asha Rao", "1990-03-12", "+44 7700 900123"),
             ("Priya Menon", "1985-07-30", "+44 7700 111222"))
    assert reg.board()["cases"] == []
    assert reg.events("patient.link.review_required.v1") == 0


def test_a_single_shared_detail_does_not_hold_anyone(reg):
    reg.pair(("Asha Rao", "1990-03-12", "+44 7700 900123"),
             ("Asha Rao", "1985-07-30", "+44 7700 111222"))      # only the name
    assert reg.board()["cases"] == []


def test_twins_are_flagged_and_marked_critical(reg):
    reg.pair(("Anna Smith", "1995-05-05", "+44 7700 900123"),
             ("Bella Smith", "1995-05-05", "+44 7700 900123"))
    case = reg.case()
    assert case["flags"] == ["twin"] and case["critical"] is True


def test_a_slipped_birth_date_is_flagged_and_critical(reg):
    reg.pair(("Asha Rao", "1990-03-12", ""), ("Asha Rao", "1990-12-03", ""))
    case = reg.case()
    assert case["flags"] == ["transposition"] and case["critical"] is True
    assert case["confidence"] == "medium"


def test_saving_the_same_details_again_does_not_open_another_case(reg):
    reg.pair()
    reg.save("pat2", "Asha Rao", "1990-03-12")
    reg.save("pat1", "Asha Rao", "1990-03-12")
    assert len(reg.board()["cases"]) == 1 and reg.events("patient.link.review_required.v1") == 1


def test_three_records_that_look_alike_give_three_pairs(reg):
    for subject in ("pat1", "pat2", "pat3"):
        reg.patient(subject, "Asha Rao", "1990-03-12")
    assert sorted(c["ref_no"] for c in reg.board()["cases"]) == ["M-0001", "M-0002", "M-0003"]


def test_changing_a_record_so_it_no_longer_matches_does_not_close_or_open_anything(reg):
    reg.pair()
    reg.save("pat2", "Priya Menon", "1985-07-30")
    assert reg.state() == "possible_match"                     # a person still decides


# ------------------------------------------------------------------ deciding
def test_one_reviewer_can_confirm_a_case_that_is_not_critical(reg):
    reg.pair()
    done = reg.decide("confirm_same", note="same_details")
    assert done.status_code == 200 and json.loads(done.content)["case"]["state"] == "linked"
    case = reg.case()
    assert [s["action"] for s in case["history"]] == ["opened", "confirm_same"]
    assert case["history"][1]["note"] == "same_details" and case["chain_ok"] is True
    flags = {p["id"]: p for p in json.loads(reg.web.get("staff/patients", subject="desk").content)[
        "patients"]}
    assert flags[reg.person("pat1")]["same_as_another"] is True
    assert reg.events("patient.link.corrected.v1") == 1


def test_a_critical_case_needs_two_different_reviewers(reg):
    reg.pair(("Anna Smith", "1995-05-05", "+44 7700 900123"),
             ("Bella Smith", "1995-05-05", "+44 7700 900123"))
    first = reg.decide("confirm_same", subject="desk")
    assert json.loads(first.content)["case"]["state"] == "matched_pending_auth"
    again = reg.decide("confirm_same", subject="desk")           # the same person cannot second it
    assert again.status_code == 403 and reg.state() == "matched_pending_auth"
    case = reg.case()
    assert case["you_agreed_first"] is True and case["first_reviewer"]
    other = reg.board("desk2")["cases"][0]
    assert other["you_agreed_first"] is False
    second = reg.decide("confirm_same", subject="desk2")
    assert json.loads(second.content)["case"]["state"] == "linked"
    assert [s["action"] for s in reg.case()["history"]] == [
        "opened", "confirm_same", "confirm_same"]


def test_a_second_reviewer_can_overrule_the_first(reg):
    reg.pair(("Anna Smith", "1995-05-05", "+44 7700 900123"),
             ("Bella Smith", "1995-05-05", "+44 7700 900123"))
    reg.decide("confirm_same", subject="desk")
    closed = reg.decide("mark_not_same", subject="desk2", note="twins")
    assert json.loads(closed.content)["case"]["state"] == "closed_not_same"


def test_not_the_same_is_remembered_until_new_evidence_appears(reg):
    reg.pair()
    reg.decide("mark_not_same", note="different_people")
    assert reg.state() == "closed_not_same"
    reg.save("pat2", "Asha Rao", "1990-03-12")                    # nothing new: stays closed
    reg.save("pat1", "Asha Rao", "1990-03-12")
    assert reg.state() == "closed_not_same" and len(reg.board()["cases"]) == 1
    reg.save("pat2", "Asha Rau", "1990-03-12")                    # the details changed: look again
    case = reg.case()
    assert case["state"] == "possible_match"
    assert [s["action"] for s in case["history"]] == ["opened", "mark_not_same", "reopened"]
    assert reg.events("patient.link.review_required.v1") == 2


def test_a_confirmed_link_can_be_sent_back_and_split(reg):
    reg.pair()
    reg.decide("confirm_same")
    split = reg.decide("split", subject="desk2")
    assert json.loads(split.content)["case"]["state"] == "split_review"
    undone = reg.decide("mark_not_same", subject="desk2", note="data_entry_error")
    assert json.loads(undone.content)["case"]["state"] == "closed_not_same"
    case = reg.case()
    assert [s["action"] for s in case["history"]] == [
        "opened", "confirm_same", "split", "mark_not_same"]
    assert case["chain_ok"] is True
    flags = {p["id"]: p for p in json.loads(reg.web.get("staff/patients", subject="desk").content)[
        "patients"]}
    assert flags[reg.person("pat1")]["same_as_another"] is False


def test_asking_for_a_recheck_changes_nothing_but_the_flag(reg):
    reg.pair()
    done = reg.decide("request_recheck")
    assert done.status_code == 200
    case = reg.case()
    assert (case["state"], case["recheck_requested"]) == ("possible_match", True)


def test_decisions_are_refused_that_the_state_does_not_allow(reg):
    reg.pair()
    assert reg.decide("split").status_code == 409                # nothing to split yet
    reg.decide("mark_not_same")
    for action in ("confirm_same", "mark_not_same", "request_recheck", "split"):
        assert reg.decide(action).status_code == 409, action     # closed: nothing more to decide


def test_bad_requests_are_refused(reg):
    reg.pair()
    assert reg.decide("merge").status_code == 422                # there is no merge
    assert reg.decide("confirm_same", note="because I said so").status_code == 422
    assert reg.decide("confirm_same", case="nope").status_code == 404
    assert reg.state() == "possible_match"


def test_only_clinic_staff_may_see_or_decide_cases(reg):
    reg.pair()
    case = reg.case()["public_id"]
    assert reg.web.get("staff/matches", subject="pat1").status_code == 403
    assert reg.decide("confirm_same", subject="pat1", case=case).status_code == 403
    assert reg.web.client.get(API + "staff/matches").status_code == 401
    assert reg.state() == "possible_match"


def test_another_clinic_cannot_see_or_decide_these_cases(reg):
    reg.pair()
    with tenant_transaction(reg.ctx):
        other_org = trust.create_organization(name="Other Clinic").public_id
        rival = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="rival")
        trust.assign_role(actor_public_id=rival.public_id, organization_public_id=other_org,
                          role_code="R13", by="system")
    assert reg.board("rival")["cases"] == []
    case = reg.case()["public_id"]
    assert reg.decide("confirm_same", subject="rival", case=case).status_code == 404
    assert reg.state() == "possible_match"


def test_nobody_decides_a_case_about_themselves(reg):
    reg.pair()
    case = reg.case()["public_id"]
    patient_login = reg.people["pat1"][0]
    with tenant_transaction(reg.ctx):
        with pytest.raises(ZoikoError) as caught:
            trust.decide_match(case_ref=case, action="mark_not_same",
                               actor_public_id=patient_login, organization_ref=reg.org)
    assert caught.value.code == ErrorCode.POLICY_DENIED
    assert reg.state() == "possible_match"


def test_the_decision_history_cannot_be_rewritten(reg):
    reg.pair()
    reg.decide("request_recheck")
    with pytest.raises(DatabaseError), transaction.atomic():
        MatchDecision.objects.update(actor_ref="someone_else")
    with pytest.raises(DatabaseError), transaction.atomic():
        MatchDecision.objects.all().delete()
    assert reg.case()["chain_ok"] is True


def test_one_customer_never_sees_anothers_cases(reg):
    reg.pair()
    other = Reg()
    other.patient("solo", "Priya Menon", "1985-07-30")
    assert other.board()["cases"] == []
    with tenant_transaction(other.ctx), as_app_role():
        assert MatchCase.objects.count() == 0 and MatchDecision.objects.count() == 0
    with tenant_transaction(reg.ctx), as_app_role():
        assert MatchCase.objects.count() == 1


def test_a_case_is_audited(reg):
    reg.pair()
    reg.decide("confirm_same")
    with tenant_transaction(reg.ctx):
        actions = sorted(AuditEvent.objects.filter(action__startswith="match.").values_list(
            "action", flat=True))
    assert actions == ["match.confirm_same", "match.open"]


# ------------------------------------------------------------------ identity checks
def test_staff_can_record_that_they_checked_a_patients_identity(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    assert reg.identity("pat1")["identity"]["status"] == "unverified"
    done = reg.check("pat1")
    assert done.status_code == 201
    info = json.loads(done.content)["identity"]
    assert info["status"] == "verified" and info["clinic"] and info["checked_on"]
    assert info["valid_until"] and info["under_review"] is False
    assert reg.events("identity.subject.proofed.v1") == 1
    with tenant_transaction(reg.ctx):
        assert AuditEvent.objects.filter(action="identity.check").count() == 1
        assert Person.objects.get(public_id=reg.person("pat1")).identity_status == "verified"


def test_the_patient_sees_that_they_are_verified_but_never_a_duplicate_review(reg):
    reg.pair()
    reg.web.post(f"people/{reg.person('pat1')}/identity/withdraw", {"reason": "mistake"},
                 subject="desk")                              # (nothing to withdraw yet: harmless)
    own = json.loads(reg.web.get("profile", subject="pat1").content)["profiles"][0]
    assert own["identity"]["status"] == "unverified"
    assert "under_review" not in own["identity"]
    mine = reg.web.get(f"people/{reg.person('pat1')}/identity", subject="pat1")
    assert mine.status_code == 200 and "under_review" not in json.loads(mine.content)["identity"]
    assert "history" not in json.loads(mine.content)          # patients never see staff history
    assert reg.identity("pat1")["identity"]["under_review"] is True    # staff do


def test_a_verified_patient_sees_which_clinic_checked_and_until_when(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    reg.check("pat1")
    own = json.loads(reg.web.get("profile", subject="pat1").content)["profiles"][0]["identity"]
    assert own["status"] == "verified" and own["clinic"] and own["valid_until"]


def test_nothing_can_be_checked_without_a_name_and_birth_date(reg):
    reg.patient("pat1", "Asha Rao", "")
    assert reg.check("pat1").status_code == 422                # no birth date
    reg.patient("pat2")                                        # no details at all
    assert reg.check("pat2").status_code == 422


def test_a_failed_check_does_not_verify_anyone(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    done = reg.check("pat1", outcome="failed")
    info = json.loads(done.content)["identity"]
    assert (info["status"], info["why_not"]) == ("unverified", "check_failed")


@pytest.mark.parametrize("extra", [
    {"evidence_type": "library_card"}, {"outcome": "maybe"}, {"valid_days": 0},
    {"valid_days": 4000}, {"valid_days": True}, {"valid_days": "30"},
])
def test_a_check_needs_a_known_document_type_outcome_and_validity(reg, extra):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    assert reg.check("pat1", **extra).status_code == 422


def test_a_check_can_be_given_its_own_validity(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    short = json.loads(reg.check("pat1", valid_days=30).content)["identity"]
    until = dt.date.fromisoformat(short["valid_until"])
    assert 29 <= (until - dt.datetime.now(UTC).date()).days <= 31


def test_patients_and_other_clinics_cannot_record_checks(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    assert reg.check("pat1", subject_of_staff="pat1").status_code == 403    # not for yourself
    with tenant_transaction(reg.ctx):
        other_org = trust.create_organization(name="Other Clinic").public_id
        rival = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="rival")
        trust.assign_role(actor_public_id=rival.public_id, organization_public_id=other_org,
                          role_code="R13", by="system")
    assert reg.check("pat1", subject_of_staff="rival").status_code == 403   # not their patient
    assert reg.identity("pat1")["identity"]["status"] == "unverified"


def test_a_patient_cannot_vouch_for_their_own_identity_even_through_the_service(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    with tenant_transaction(reg.ctx):
        with pytest.raises(ZoikoError) as caught:
            trust.record_identity_check(
                person_public_id=reg.person("pat1"), evidence_type="passport", outcome="passed",
                organization_ref=reg.org, actor_public_id=reg.people["pat1"][0])
    assert caught.value.code == ErrorCode.POLICY_DENIED


def test_a_possible_duplicate_must_be_settled_before_a_check(reg):
    reg.pair()
    held = reg.check("pat1")
    assert held.status_code == 423 and json.loads(held.content)["code"] == "HOLD_ACTIVE"
    reg.decide("mark_not_same")
    assert reg.check("pat1").status_code == 201                 # settled: now it can be checked


def test_changing_the_checked_details_lapses_the_check_but_a_new_phone_does_not(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12", "+44 7700 900123")
    reg.check("pat1")
    reg.save("pat1", "asha RAO", "1990-03-12", "+44 7700 111222")      # same person, new phone
    assert reg.identity("pat1")["identity"]["status"] == "verified"
    reg.save("pat1", "Asha Rao", "1990-03-21", "+44 7700 111222")      # the birth date changed
    info = reg.identity("pat1")["identity"]
    assert (info["status"], info["why_not"]) == ("unverified", "details_changed")
    reg.save("pat1", "Priya Rao", "1990-03-12", "+44 7700 111222")
    assert reg.identity("pat1")["identity"]["status"] == "unverified"


def test_a_check_can_be_withdrawn_and_the_history_keeps_every_step(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    reg.check("pat1", evidence="passport")
    assert reg.web.post(f"people/{reg.person('pat1')}/identity/withdraw", {"reason": "nonsense"},
                        subject="desk").status_code == 422
    gone = reg.web.post(f"people/{reg.person('pat1')}/identity/withdraw",
                        {"reason": "fraud_concern"}, subject="desk2")
    info = json.loads(gone.content)["identity"]
    assert (info["status"], info["why_not"]) == ("unverified", "withdrawn")
    reg.check("pat1", evidence="driving_licence")
    past = reg.identity("pat1")["history"]
    assert [(h["evidence_type"], h["withdrawn"]) for h in past] == [
        ("driving_licence", False), ("passport", True)]
    assert past[1]["withdraw_reason"] == "fraud_concern" and past[0]["by"] and past[0]["clinic"]


def test_an_expired_check_stops_counting_and_the_worker_corrects_the_status(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    now = dt.datetime.now(UTC)
    with tenant_transaction(reg.ctx):
        trust.record_identity_check(
            person_public_id=reg.person("pat1"), evidence_type="passport", outcome="passed",
            organization_ref=reg.org, actor_public_id=reg.desk, valid_days=1,
            clock=FixedClock(now))
    later = FixedClock(now + dt.timedelta(days=2))
    with tenant_transaction(reg.ctx):
        assert Person.objects.get(public_id=reg.person("pat1")).identity_status == "verified"
        assert trust.refresh_expired_identities(clock=later) == 1
        assert Person.objects.get(public_id=reg.person("pat1")).identity_status == "unverified"
        info = trust.identity_status_of(reg.person("pat1"), clock=later)
    assert (info["status"], info["why_not"]) == ("unverified", "expired")


def test_the_worker_job_runs_for_every_customer(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    assert refresh_all_tenants() == 0                           # nothing has expired


def test_the_patient_list_shows_who_is_verified_and_who_is_under_review(reg):
    reg.pair()
    reg.patient("pat3", "Priya Menon", "1985-07-30")
    reg.check("pat3")
    rows = {p["id"]: p for p in json.loads(reg.web.get("staff/patients", subject="desk").content)[
        "patients"]}
    assert rows[reg.person("pat3")]["identity_status"] == "verified"
    assert rows[reg.person("pat1")]["under_review"] is True
    assert rows[reg.person("pat3")]["under_review"] is False


# ------------------------------------------------------------------ search fingerprints
def test_details_saved_before_the_fingerprints_existed_can_be_found_after_a_rebuild(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12")
    with tenant_transaction(reg.ctx):                            # as if saved by an older version
        PersonProfile.objects.update(birth_key="", phone_key="")
    reg.patient("pat2", "Asha Rao", "1990-03-12")
    assert reg.board()["cases"] == []                            # pat1 cannot be found yet
    with tenant_transaction(reg.ctx):
        assert trust.rebuild_match_keys() == 2
    reg.save("pat2", "Asha Rao", "1990-03-12")
    assert len(reg.board()["cases"]) == 1


def test_the_fingerprints_hold_no_readable_details(reg):
    reg.patient("pat1", "Asha Rao", "1990-03-12", "+44 7700 900123")
    with tenant_transaction(reg.ctx):
        row = PersonProfile.objects.get()
        assert len(row.birth_key) == 64 and len(row.phone_key) == 64
        assert "1990" not in row.birth_key and "900123" not in row.phone_key
        assert "Asha" not in row.legal_name_enc            # and the details themselves stay sealed
