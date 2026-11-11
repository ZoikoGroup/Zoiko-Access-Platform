"""Public interface of the packs module (GHE-04).

This is the ONLY module other domain modules may import from `modules.packs`.
Everything else (models, services, domain rules) is private. Enforced by import-linter.
"""
from .services import (
    PackRejected,
    active_pack,
    apply_revocation_notice,
    capability_enabled,
    cell_status,
    enabled_capabilities,
    install_pack,
    list_packs,
    require_capability,
)

__all__ = [
    "PackRejected", "active_pack", "apply_revocation_notice", "capability_enabled",
    "cell_status", "enabled_capabilities", "install_pack", "list_packs", "require_capability",
]
