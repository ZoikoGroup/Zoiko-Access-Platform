"""Public interface of the retention module (DEV-213, DEV-398). Nothing here deletes a row:
records past a period in force are anonymised in batches two administrators approve."""
from .services import (
    MAX_DAYS,
    approve,
    approve_batch,
    prepare_batch,
    propose,
    report,
    withdraw,
    withdraw_batch,
)

__all__ = ["MAX_DAYS", "approve", "approve_batch", "prepare_batch", "propose", "report",
           "withdraw", "withdraw_batch"]
