from foundation.stats import summarize


def test_an_empty_list_has_no_median_and_no_average_not_a_made_up_zero():
    assert summarize([]) == {"count": 0, "median": None, "average": None}


def test_one_value_is_its_own_median_and_average():
    assert summarize([2.0]) == {"count": 1, "median": 2.0, "average": 2.0}


def test_the_median_ignores_one_huge_value_but_the_average_does_not():
    out = summarize([1.0, 2.0, 3.0, 100.0])
    assert out["median"] == 2.5 and out["average"] == 26.5 and out["count"] == 4


def test_values_are_rounded_to_one_decimal():
    assert summarize([1.04, 1.06])["average"] == 1.1
