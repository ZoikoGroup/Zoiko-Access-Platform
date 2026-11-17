"""The identifier catalogue matches the identifiers the models and events really hold.

A new field that looks like an identifier fails here until it is listed in
foundation/identifiers.py, and a listed one that nothing uses any more fails too, so the catalogue
cannot go stale."""
import re
import uuid

import pytest
from django.apps import apps
from django.db import models

from foundation import cell_context, event_catalog, request_context
from foundation.identifiers import (
    FIELDS,
    IDENTIFIERS,
    MODEL_FIELDS,
    NOT_IDENTIFIERS,
    PUBLIC_ID,
    SCOPES,
    check,
    kind_of,
)
from foundation.ids import new_internal_id, new_public_id

# A field name that says "I point at something".
SHAPE = re.compile(
    r"^(?:id|public_id|ref_no|reference|.+_(?:ref|refs|ref_enc|id|ids|key|token|no))$")
PERSONAL_WORDS = ("email", "phone", "name", "birth", "dob", "nhs", "passport", "address", "ssn")
EVENT_FIELDS = {f for _, always, sometimes in {**event_catalog.EVENTS, **event_catalog.DIRECT}
                .values() for f in (*always, *sometimes)}


def product_models() -> list[type[models.Model]]:
    return [m for m in apps.get_models()
            if m.__module__.startswith(("modules.", "foundation.")) and not m._meta.abstract]


def identifier_fields() -> list[tuple[str, models.Field]]:
    """(app_label.Model, field) for every stored field that looks like an identifier. A foreign
    key is not included: it is a link inside one module, checked separately below."""
    found = []
    for model in product_models():
        for field in model._meta.concrete_fields:
            if field.is_relation or not SHAPE.match(field.name):
                continue
            found.append((model._meta.label, field))
    return found


# ------------------------------------------------------------- (a) every identifier is listed
def test_every_identifier_field_in_the_models_is_catalogued():
    unlisted = sorted({f"{label}.{field.name}" for label, field in identifier_fields()
                       if field.name not in NOT_IDENTIFIERS and kind_of(field.name, label) is None})
    assert unlisted == [], "identifier fields missing from foundation/identifiers.py"


def test_a_foreign_key_stays_inside_its_own_module_and_points_at_an_internal_id():
    """Across modules, rows point at each other by public id (`*_ref`), never by foreign key."""
    for model in product_models():
        for field in model._meta.concrete_fields:
            if not field.is_relation:
                continue
            target = field.related_model._meta
            where = f"{model._meta.label}.{field.name}"
            assert target.app_label == model._meta.app_label, f"{where} crosses modules"
            assert target.pk.name == "id" and field.attname.endswith("_id"), where


# ------------------------------------------------------------- (b) nothing listed is stale
def test_the_catalogue_lists_nothing_that_nobody_uses():
    in_models = {(label, field.name) for label, field in identifier_fields()}
    names_in_models = {name for _, name in in_models}
    stale_names = set(FIELDS) - names_in_models - EVENT_FIELDS
    assert stale_names == set(), "field names listed but in no model and no event"
    assert set(MODEL_FIELDS) - in_models == set(), "per-table entries for fields that are gone"
    assert set(NOT_IDENTIFIERS) - names_in_models == set(), "exclusions for fields that are gone"

    stored_kinds = {kind_of(name, label) for label, name in in_models} - {None}
    for kind, spec in IDENTIFIERS.items():
        if spec.in_models:
            assert kind in stored_kinds, f"{kind} is listed but no model stores it"
        else:
            assert kind not in stored_kinds, f"{kind} is stored after all: set in_models=True"
            assert any(FIELDS.get(f) == kind for f in EVENT_FIELDS), f"{kind} is in no event"
    used = set(FIELDS.values()) | set(MODEL_FIELDS.values())
    assert used == set(IDENTIFIERS), "every field maps to a listed kind and every kind is used"


def test_each_kind_is_fully_described():
    for kind, spec in IDENTIFIERS.items():
        assert spec.names and spec.format, kind
        assert spec.scope in SCOPES, kind


# ------------------------------------------------------------- (c) events carry only listed refs
def test_every_reference_in_an_event_is_catalogued_and_allowed_out_of_the_cell():
    refs = {f for f in EVENT_FIELDS if f.endswith(("_ref", "_refs", "_id"))}
    assert refs, "the event catalogue carries references"
    for name in refs:
        kind = kind_of(name)
        assert kind is not None, f"{name} is in an event but not in the identifier catalogue"
        assert IDENTIFIERS[kind].leaves_cell, f"{name} ({kind}) must never leave the cell"


def test_ids_that_must_stay_in_the_cell_never_appear_in_an_event():
    staying = {f for f, k in FIELDS.items() if not IDENTIFIERS[k].leaves_cell}
    assert staying & EVENT_FIELDS == set()
    assert {"id", "tenant_id", "cell_id"} <= staying


# ------------------------------------------------------------- (d) fixed formats
def test_what_the_generators_make_always_passes_the_check():
    for _ in range(2000):
        assert check("public_id", new_public_id())
        assert check("id", new_internal_id()) and check("id", str(new_internal_id()))
        assert check("event_id", uuid.uuid4())
        assert check("request_id", request_context.new_request_id())
    assert check("actor_ref", new_public_id()) and check("actor_ref", "system")
    assert check("cell_id", "UK-1") and check("pack_ref", "gb-care-navigation")


@pytest.mark.parametrize("bad", [
    "", " ", "a" * 21, "a" * 23, "abc+def/ghi=jkl+mno/pq", "jane.doe@example.com",
    "../../etc/passwd", "a" * 21 + "\n", None, 12345, b"a" * 22,
])
def test_obvious_bad_values_are_refused(bad):
    for kind, spec in IDENTIFIERS.items():
        if spec.pattern is not None and kind not in ("request_id", "pack_ref", "cell_id"):
            assert not check(kind, bad), (kind, bad)
    assert not check("cell_id", "uk-1") and not check("cell_id", "-UK") and not check("cell_id", "")
    assert not check("pack_ref", "GB Care") and not check("pack_ref", "")
    assert not check("request_id", "short") and not check("request_id", "has space in it")
    assert not check("actor_ref", "System") and not check("id", "NOT-A-UUID")


def test_kinds_without_a_fixed_format_cannot_be_checked():
    with pytest.raises(ValueError):
        check("external_ref", "anything")


def test_the_catalogue_patterns_agree_with_the_code_that_makes_and_accepts_the_values():
    assert IDENTIFIERS["cell_id"].pattern.pattern == cell_context._CELL_ID.pattern
    assert IDENTIFIERS["request_id"].pattern.pattern == request_context._SAFE_ID.pattern


def test_every_column_holding_a_fixed_format_kind_is_wide_enough():
    for label, field in identifier_fields():
        kind = kind_of(field.name, label)
        if kind is None or IDENTIFIERS[kind].pattern is not PUBLIC_ID:
            continue
        if isinstance(field, models.CharField):
            assert field.max_length >= 22, f"{label}.{field.name}"


# ------------------------------------------------------------- (e) never made from personal data
def test_no_identifier_is_made_from_personal_data():
    assert [k for k, spec in IDENTIFIERS.items() if spec.from_personal_data] == []
    # `email_ref` is the public id of a queued email message, not an email address.
    for name in {*IDENTIFIERS, *FIELDS} - {"email_ref"}:
        assert not any(word in name for word in PERSONAL_WORDS), name


def test_values_that_pick_out_a_person_never_leave_the_cell():
    for kind, spec in IDENTIFIERS.items():
        if spec.personal:
            assert not spec.leaves_cell, kind


def test_fingerprints_of_personal_data_are_not_identifiers_and_never_travel():
    fingerprints = {"birth_key", "phone_key"}
    assert fingerprints <= set(NOT_IDENTIFIERS)
    assert fingerprints.isdisjoint(FIELDS) and fingerprints.isdisjoint(EVENT_FIELDS)


@pytest.mark.parametrize("text,found", [
    ("9434765919", True), ("943 476 5919", True), ("ORD-943-476-5919", True),
    ("9434765918", False),                # wrong check digit
    ("ORD-0001", False), ("12345", False), ("94347659190", False),   # eleven digits
    ("", False), (None, False),
])
def test_an_nhs_number_is_recognised_wherever_it_hides(text, found):
    """DEV-222: keeps a national number out of values sent to partners."""
    from foundation.identifiers import contains_nhs_number
    assert contains_nhs_number(text) is found
