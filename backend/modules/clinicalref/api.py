"""Public interface of the clinical references module (DEV-041). Pointers only, never content;
switched off unless CLINICAL_REFERENCES_ENABLED is True."""
from .services import OPERATION, add, enabled, get, list_for_person, withdraw

__all__ = ["OPERATION", "add", "enabled", "get", "list_for_person", "withdraw"]
