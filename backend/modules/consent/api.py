"""Public interface of the consent module: a patient's choices about who may see what.

This is the ONLY module other domain modules may import from `modules.consent`.
"""
from .services import (
    AUDIENCES,
    PURPOSES,
    allowed_for,
    history,
    is_granted,
    list_for_person,
    purpose_resource,
    set_consent,
    states,
)

__all__ = ["AUDIENCES", "PURPOSES", "allowed_for", "history", "is_granted", "list_for_person",
           "purpose_resource", "set_consent", "states"]
