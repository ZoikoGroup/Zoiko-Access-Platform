import pytest

from foundation.payloads import MAX_STRING, PayloadError, check_reference_payload


def test_flat_reference_payload_is_accepted():
    payload = {"booking_ref": "bk_123", "status": "ok", "version": 3, "urgent": False, "x": None}
    assert check_reference_payload(payload) == payload


@pytest.mark.parametrize(
    "bad",
    [
        {"note": {"nested": "no"}},          # nested object
        {"ids": ["a", "b"]},                 # list
        {"note": "x" * (MAX_STRING + 1)},    # free text is too long to be a reference
        {"Name": "x"},                       # key not lowercase
        {"patient name": "x"},               # key with a space
        {"a.b": "x"},                        # key with a dot
        {"": "x"},                           # empty key
    ],
)
def test_unsafe_shapes_are_refused(bad):
    with pytest.raises(PayloadError):
        check_reference_payload(bad)


def test_many_small_fields_still_hit_the_size_limit():
    payload = {f"k{i}": "v" * 60 for i in range(60)}
    with pytest.raises(PayloadError):
        check_reference_payload(payload)


def test_non_mapping_is_refused():
    with pytest.raises(PayloadError):
        check_reference_payload(["a"])  # type: ignore[arg-type]
