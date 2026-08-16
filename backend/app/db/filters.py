"""Query guards that enforce environment rules at the data layer.

`settings.providers_for()` keeps the demo *provider* out of every fetch chain,
but seeded rows already sitting in the database are read straight from it and
never touch a provider. Without this guard a PRODUCTION deployment happily
serves the sample research calls it was seeded with - labelled `DEMO`, but
served all the same, which is exactly what PRODUCTION is supposed to make
impossible.

Applied at the query level rather than by filtering results afterwards, so a
paginated endpoint returns a full page of real rows instead of a short page
with the demo ones removed.
"""

from __future__ import annotations

from typing import Any, TypeVar

from app.core.config import settings

_Stmt = TypeVar("_Stmt")


def hide_demo(stmt: _Stmt, *models: Any) -> _Stmt:
    """Exclude seeded rows unless the environment permits them.

    In DEMO and LOCAL this is a no-op: the rows are visible and every payload
    badges them. In STAGING and PRODUCTION they are excluded outright.
    """
    if settings.demo_data_allowed:
        return stmt
    for model in models:
        stmt = stmt.where(model.is_demo.is_(False))
    return stmt


def demo_rows_permitted() -> bool:
    return settings.demo_data_allowed
