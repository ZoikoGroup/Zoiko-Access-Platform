"""Public interface of the retention module (DEV-213). Only `erase` deletes anything, and only
where the deployment has switched erasing on (DEV-351)."""
from .services import MAX_DAYS, approve, erase, erasure_preview, propose, report, withdraw

__all__ = ["MAX_DAYS", "approve", "erase", "erasure_preview", "propose", "report", "withdraw"]
