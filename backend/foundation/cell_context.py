"""Who is this request for? One cell + one tenant, carried to the database.

CellContext        the cell and tenant of the current request.
build_context      makes one from a verified token's tenant (cell comes from deployment config).
tenant_transaction opens a database transaction and tells PostgreSQL which cell/tenant it is for.
                   Row-level security policies (see rls.py) then hide every other tenant's rows.

The values are set with set_config(..., is_local=true): they last for ONE transaction only, so a
pooled connection can never carry one request's tenant into the next request.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction

from .errors import ErrorCode, ZoikoError

_CELL_ID = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")


class MissingContextError(RuntimeError):
    """Code tried to touch tenant data with no cell/tenant bound. Always a bug: fail closed."""


@dataclass(frozen=True)
class CellContext:
    cell_id: str
    tenant_id: uuid.UUID

    def __post_init__(self) -> None:
        if not _CELL_ID.match(self.cell_id):
            raise ValueError("invalid cell id")
        if not isinstance(self.tenant_id, uuid.UUID):
            raise ValueError("tenant_id must be a UUID")


_current: ContextVar[CellContext | None] = ContextVar("cell_context", default=None)


def get_context() -> CellContext | None:
    return _current.get()


def require_context() -> CellContext:
    ctx = _current.get()
    if ctx is None:
        raise MissingContextError("no cell/tenant context is bound")
    return ctx


def build_context(tenant_id: uuid.UUID, claimed_cell_id: str | None = None) -> CellContext:
    """The cell is whatever THIS deployment is. A token claiming another cell is refused."""
    cell_id: str = settings.ZOIKO_CELL_ID
    if claimed_cell_id is not None and claimed_cell_id != cell_id:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return CellContext(cell_id=cell_id, tenant_id=tenant_id)


@contextmanager
def bind_context(ctx: CellContext) -> Iterator[CellContext]:
    """Set the context for Python code only (no database call). Used by request middleware."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


_APPLY = "SELECT set_config('app.cell_id', %s, true), set_config('app.tenant_id', %s, true)"


def _apply_to_database(cell_id: str, tenant_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(_APPLY, [cell_id, tenant_id])


@contextmanager
def tenant_transaction(ctx: CellContext | None = None) -> Iterator[CellContext]:
    """Run a block inside ONE transaction that is scoped to the given (or bound) tenant."""
    ctx = ctx or require_context()
    if ctx.cell_id != settings.ZOIKO_CELL_ID:
        raise ZoikoError(ErrorCode.POLICY_DENIED)  # never act for a cell we are not
    previous = _current.get()
    token = _current.set(ctx)
    try:
        with transaction.atomic():
            _apply_to_database(ctx.cell_id, str(ctx.tenant_id))
            yield ctx
            # Normal exit only: put the outer block's tenant back (or clear). If the block
            # raised, the rollback undoes the settings by itself, and running SQL in an aborted
            # transaction would hide the real error.
            if previous is not None:
                _apply_to_database(previous.cell_id, str(previous.tenant_id))
            else:
                _apply_to_database("", "")
    finally:
        _current.reset(token)
