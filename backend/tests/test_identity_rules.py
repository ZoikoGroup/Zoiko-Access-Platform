import uuid

import pytest

from foundation.crypto import blind_index
from modules.trust.identity_match import (
    compare_birth,
    compare_names,
    compare_phone,
    evaluate,
    name_tokens,
    same_surname,
    transposed_variants,
)


def person(name="Asha Rao", birth="1990-03-12", phone="+44 7700 900123"):
    return {"name": name, "birth_date": birth, "phone": phone}


# ------------------------------------------------------------------ names
@pytest.mark.parametrize("a,b,expected", [
    ("Asha Rao", "Asha Rao", "same"),
    ("Asha Rao", "  asha   RAO ", "same"),
    ("Rao, Asha", "Asha Rao", "same"),                    # the same words in another order
    ("José Álvarez", "Jose Alvarez", "same"),              # accents do not matter
    ("O'Brien Mary", "OBrien Mary", "close"),               # an apostrophe: nearly the same
    ("Asha Rao", "Asha Rau", "close"),                     # a typo
    ("Anna Smith", "Anya Smith", "close"),
    ("Asha Rao", "Priya Menon", "differ"),
    ("Anna Smith", "Bella Smith", "differ"),               # same family name is not the same person
    ("", "Asha Rao", "missing"),
    ("Asha Rao", "   ", "missing"),
])
def test_names_are_compared_by_their_words(a, b, expected):
    assert compare_names(a, b) == expected


def test_a_name_is_reduced_to_plain_lower_case_words():
    assert name_tokens("  Dr. José  O'Brien-Smith ") == ["dr", "jose", "o", "brien", "smith"]
    assert name_tokens("") == [] and name_tokens("***") == []


def test_the_family_name_is_the_last_word():
    assert same_surname("Anna Smith", "Bella Smith")
    assert not same_surname("Anna Smith", "Anna Jones")
    assert not same_surname("", "Anna Smith")


# ------------------------------------------------------------------ birth dates
@pytest.mark.parametrize("a,b,expected", [
    ("1990-03-12", "1990-03-12", "same"),
    ("1990-03-12", "1990-12-03", "transposed"),            # day and month swapped
    ("1990-03-12", "1990-03-21", "transposed"),            # two neighbouring digits swapped
    ("1990-03-12", "1909-03-12", "transposed"),
    ("1990-03-12", "1991-03-12", "differ"),
    ("1990-03-12", "1985-07-30", "differ"),
    ("", "1990-03-12", "missing"),
    ("1990-03-12", "", "missing"),
])
def test_birth_dates_are_compared_including_the_slips_people_make(a, b, expected):
    assert compare_birth(a, b) == expected


def test_a_birth_date_never_counts_as_its_own_slip():
    for date in ("1990-03-12", "2000-11-11", "1985-01-01"):
        assert date not in transposed_variants(date)
    assert transposed_variants("not a date") == set()
    assert "1990-12-03" in transposed_variants("1990-03-12")


# ------------------------------------------------------------------ phone numbers
@pytest.mark.parametrize("a,b,expected", [
    ("+44 7700 900123", "07700 900123", "same"),           # the same number written two ways
    ("(020) 7946-0018", "+44 20 7946 0018", "same"),
    ("+44 7700 900123", "+44 7700 900999", "differ"),
    ("", "+44 7700 900123", "missing"),
    ("12", "+44 7700 900123", "missing"),                  # too short to mean anything
])
def test_phone_numbers_are_compared_by_their_last_digits(a, b, expected):
    assert compare_phone(a, b) == expected


# ------------------------------------------------------------------ the decision rule
def test_the_same_name_and_birth_date_is_a_high_confidence_candidate():
    result = evaluate(person(), person(phone=""))
    assert result is not None
    assert result.factors == {"name": "same", "birth_date": "same", "phone": "missing"}
    assert (result.confidence, result.flags) == ("high", [])


def test_a_typo_in_the_name_with_the_same_birth_date_is_a_medium_candidate():
    result = evaluate(person("Asha Rao"), person("Asha Rau", phone=""))
    assert result is not None and result.confidence == "medium"


def test_the_same_birth_date_and_phone_is_a_candidate_whatever_the_names():
    result = evaluate(person("Asha Rao"), person("Priya Menon"))
    assert result is not None and "name_change" in result.flags     # e.g. a changed name


def test_the_same_name_and_phone_is_a_candidate_even_if_the_birth_date_differs():
    result = evaluate(person(birth="1990-03-12"), person(birth="1975-06-01"))
    assert result is not None and result.factors["birth_date"] == "differ"


@pytest.mark.parametrize("other", [
    person("Priya Menon", "1985-07-30", "+44 7700 111222"),     # nothing in common
    person("Asha Rao", "1985-07-30", "+44 7700 111222"),        # only the name
    person("Priya Menon", "1990-03-12", "+44 7700 111222"),     # only the birth date
    person("Priya Menon", "1985-07-30", "+44 7700 900123"),     # only the phone
    person("", "", ""),                                         # nothing recorded
])
def test_a_single_shared_detail_is_not_enough(other):
    assert evaluate(person(), other) is None


def test_same_birth_date_and_family_name_but_different_first_names_is_flagged_as_twins():
    result = evaluate(person("Anna Smith", "1995-05-05", "+44 7700 900123"),
                      person("Bella Smith", "1995-05-05", "+44 7700 900123"))
    assert result is not None and result.flags == ["twin"]


def test_a_slipped_birth_date_is_flagged():
    result = evaluate(person(birth="1990-03-12"), person(birth="1990-12-03"))
    assert result is not None and result.flags == ["transposition"]


def test_a_shared_phone_with_a_different_birth_date_is_flagged():
    result = evaluate(person("Asha Rao", "1990-03-12"), person("Asha Rao", "1960-01-01"))
    assert result is not None and "shared_phone" in result.flags


def test_the_rule_is_symmetric():
    for a, b in ((person(), person("Asha Rau", phone="")),
                 (person("Anna Smith", "1995-05-05"), person("Bella Smith", "1995-05-05"))):
        one, two = evaluate(a, b), evaluate(b, a)
        assert one is not None and two is not None
        assert (one.factors["name"], one.factors["birth_date"], sorted(one.flags)) == (
            two.factors["name"], two.factors["birth_date"], sorted(two.flags))


# ------------------------------------------------------------------ the fingerprint
def test_a_blind_index_is_stable_keyed_and_reveals_nothing_else():
    tenant, other = uuid.uuid4(), uuid.uuid4()
    one = blind_index("1990-03-12", tenant_id=tenant, context="match:birth")
    assert one == blind_index("1990-03-12", tenant_id=tenant, context="match:birth")
    assert one != blind_index("1990-03-13", tenant_id=tenant, context="match:birth")
    assert one != blind_index("1990-03-12", tenant_id=other, context="match:birth")   # per customer
    assert one != blind_index("1990-03-12", tenant_id=tenant, context="match:phone")  # per purpose
    assert len(one) == 64
