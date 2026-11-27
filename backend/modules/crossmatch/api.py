"""Public interface of the crossmatch module (DEV-363): matching a patient between separate
customers by fingerprint, in Zoiko's platform space.

This is the ONLY module other domain modules may import from `modules.crossmatch`.
"""
from .services import (
    ANSWERS,
    answer,
    forget,
    get_case,
    list_for,
    participating,
    set_participation,
    share,
)

__all__ = ["ANSWERS", "answer", "forget", "get_case", "list_for", "participating",
           "set_participation", "share"]
