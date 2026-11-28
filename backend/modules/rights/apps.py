from django.apps import AppConfig


class RightsConfig(AppConfig):
    """D14: privacy and data rights. A patient's formal requests about their own data."""

    name = "modules.rights"
    label = "rights"

    def ready(self) -> None:
        # `manage.py rotate_field_keys` moves the sealed request notes onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("rights", rotate)

        from foundation import retention

        from .retention import persons_on_hold

        retention.register_holds(persons_on_hold)

        # A patient-record merge (DEV-365): these rows never move, they are only counted.
        from foundation import person_refs

        from .models import RightsCase

        person_refs.keep("rights.RightsCase", RightsCase)
