"""Public ids never carry a patient identifier (GHE-13 T-06, DEV-173)."""

import pytest
from django.apps import apps

from foundation.cell_context import tenant_transaction
from foundation.ids import new_public_id
from modules.trust import api as trust
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def _with_public_id():
    return [m for m in apps.get_models() if any(f.name == "public_id" for f in m._meta.fields)]


def test_every_public_id_column_is_filled_with_the_random_generator():
    models = _with_public_id()
    assert len(models) > 50
    for model in models:
        field = model._meta.get_field("public_id")
        assert field.default is new_public_id, model.__name__
        assert field.editable is False, model.__name__


def test_the_generator_is_random_url_safe_and_unrelated_to_anything_given():
    ids = {new_public_id() for _ in range(2000)}
    assert len(ids) == 2000
    assert all(len(i) == 22 and i.replace("-", "").replace("_", "").isalnum() for i in ids)


def test_no_stored_id_contains_a_patient_identifier():
    world = World()
    subject, email = "zaraquill19750302", "zaraquill19750302@example.org"
    with tenant_transaction(world.ctx):
        actor = trust.register_actor(
            actor_type=trust.ActorType.PATIENT, issuer="idp", subject=subject
        )
        person = trust.create_person(by="system")
        trust.link_person_to_actor(person.public_id, actor.public_id, by="system")
        trust.set_password(
            actor.public_id, login=email, password="Correct-Horse-9", by="system", email=email
        )
    seen: list[str] = []
    with tenant_transaction(world.ctx):
        for model in _with_public_id():
            seen += list(model.objects.values_list("public_id", flat=True))
    assert len(seen) > 5
    for ident in (subject, "zaraquill", "19750302", email.split("@")[0]):
        assert not [p for p in seen if ident in p.lower()], ident
