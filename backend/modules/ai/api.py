"""Public interface of the ai module (D18). OFF BY DEFAULT: nothing runs without an authorized
permit.

This is the ONLY module other domain modules may import from `modules.ai`.
"""

from .permits import (
    FORBIDDEN_TOOLS,
    advance,
    check_permit,
    create_permit,
    list_permits,
    permit_dict,
    sign_off,
)
from .providers import REPLY_DRAFT, register_builtin
from .runs import decide, digest, register_provider, run

__all__ = [
    "FORBIDDEN_TOOLS",
    "REPLY_DRAFT",
    "advance",
    "check_permit",
    "create_permit",
    "decide",
    "digest",
    "list_permits",
    "permit_dict",
    "register_builtin",
    "register_provider",
    "run",
    "sign_off",
]
