"""Matching a patient between separate customers, by fingerprint only (DEV-363)."""
import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ZoikoError
from modules.consent.services import PURPOSES
from modules.crossmatch import api as crossmatch
from modules.crossmatch.models import CaseState, CrossCase, SharedKey

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

A, B, C = "custA", "custB", "custC"
ANNA = {"name": "Anna Maria Smith", "birth_date": "1980-04-02", "phone": "+44 7700 900123"}


@pytest.fixture
def space(ctx_a):
    """Zoiko's platform space, with customers A and B taking part."""
    with tenant_transaction(ctx_a):
        crossmatch.set_participation(customer_ref=A, customer_name="Clinic A", active=True, by="u1")
        crossmatch.set_participation(customer_ref=B, customer_name="Clinic B", active=True, by="u2")
    return ctx_a


def share(customer, person, **details):
    return crossmatch.share(customer_ref=customer, person_ref=person, **{**ANNA, **details})


def test_same_birth_date_and_surname_opens_one_case_and_no_details_are_kept(space):
    with tenant_transaction(space):
        assert share(A, "p1") == []
        opened = share(B, "p9", name="ANNA SMÍTH", phone="07700 900123")
        assert len(opened) == 1
        assert share(B, "p9") == []                              # known already: not again
        assert CrossCase.objects.count() == 1
        keys = list(SharedKey.objects.values_list("match_key", flat=True))
        assert keys and not any("Smith" in k or "1980" in k or "7700" in k for k in keys)
        case = crossmatch.list_for(A)[0]
        assert case["factors"] == ["birth_date", "surname", "phone"]
        assert case["person"] == "p1" and case["other_organization"] is None
        assert crossmatch.list_for(B)[0]["person"] == "p9"


def test_a_phone_alone_or_a_different_surname_opens_nothing(space):
    with tenant_transaction(space):
        share(A, "p1")
        assert share(B, "p9", name="Anna Jones") == []
        assert share(B, "p8", birth_date="1980-04-03") == []
        assert CrossCase.objects.count() == 0


def test_a_customer_not_taking_part_neither_shares_nor_is_found(space):
    with tenant_transaction(space):
        share(A, "p1")
        assert share(C, "p5") == []
        assert not SharedKey.objects.filter(customer_ref=C).exists()


def test_leaving_removes_every_fingerprint_and_matches_stop(space):
    with tenant_transaction(space):
        share(A, "p1")
        crossmatch.set_participation(customer_ref=A, customer_name="Clinic A", active=False,
                                     by="u1")
        assert not SharedKey.objects.filter(customer_ref=A).exists()
        assert share(B, "p9") == []


def test_a_patient_forgotten_is_no_longer_found(space):
    with tenant_transaction(space):
        share(A, "p1")
        crossmatch.forget(A, "p1")
        assert share(B, "p9") == []


def test_both_must_say_same_before_either_learns_the_other(space):
    with tenant_transaction(space):
        share(A, "p1")
        ref = share(B, "p9")[0]
        mine = crossmatch.answer(case_ref=ref, customer_ref=A, answer="same", by="u1")
        assert mine["state"] == CaseState.OPEN and mine["other_organization"] is None
        assert crossmatch.list_for(B)[0]["their_answer_given"] is True
        done = crossmatch.answer(case_ref=ref, customer_ref=B, answer="same", by="u2")
        assert done["state"] == CaseState.CONFIRMED and done["other_organization"] == "Clinic A"
        assert crossmatch.list_for(A)[0]["other_organization"] == "Clinic B"
        with pytest.raises(ZoikoError):                          # decided: no second answer
            crossmatch.answer(case_ref=ref, customer_ref=A, answer="not_same", by="u1")


def test_not_the_same_closes_it_without_naming_anyone(space):
    with tenant_transaction(space):
        share(A, "p1")
        ref = share(B, "p9")[0]
        done = crossmatch.answer(case_ref=ref, customer_ref=B, answer="not_same", by="u2")
        assert done["state"] == CaseState.NOT_SAME and done["other_organization"] is None


def test_a_third_customer_can_neither_see_nor_answer(space):
    with tenant_transaction(space):
        share(A, "p1")
        ref = share(B, "p9")[0]
        assert crossmatch.list_for(C) == []
        with pytest.raises(ZoikoError):
            crossmatch.get_case(ref, C)
        with pytest.raises(ZoikoError):
            crossmatch.answer(case_ref=ref, customer_ref=C, answer="same", by="u3")
        with pytest.raises(ZoikoError):
            crossmatch.answer(case_ref=ref, customer_ref=A, answer="maybe", by="u1")


def test_another_space_sees_none_of_it(space, ctx_b):
    with tenant_transaction(space):
        share(A, "p1")
        share(B, "p9")
    with tenant_transaction(ctx_b):
        assert not crossmatch.participating(A)
        assert crossmatch.list_for(A) == []


def test_the_patient_can_say_no():
    choice = PURPOSES["cross_matching"]
    assert choice["resource"] == "person" and choice.get("default") is True
