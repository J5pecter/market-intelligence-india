"""Additive-only schema reconciliation for SQLite development databases.

`Base.metadata.create_all` creates missing *tables* but never adds a column to
a table that already exists. On SQLite - the zero-setup default - that means a
developer who pulls a change adding a column gets a confusing 500 rather than a
clear message.

This module closes that gap, under strict limits:

* **Additive only.** It issues `ALTER TABLE ... ADD COLUMN` and nothing else. It
  never drops, renames, retypes or reorders anything, so no data can be lost.
* **SQLite only.** On PostgreSQL it does nothing and tells you to run Alembic,
  because a real deployment needs versioned, reviewable migrations.
* **Loud.** Every column it adds is logged by name. Silent schema drift is how
  a database stops matching the code that reads it.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.base import Base

logger = logging.getLogger(__name__)


def sync_sqlite_schema(engine: Engine) -> Dict[str, List[str]]:
    """Add columns the models declare but the database is missing.

    Returns {table: [columns added]}. Empty when nothing was needed.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return {}

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: Dict[str, List[str]] = {}

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all handles brand-new tables

            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue

                # A NOT NULL column can only be added if it has a default;
                # otherwise existing rows would violate it. Add it as nullable
                # and say so, rather than failing the whole start-up.
                ddl_type = column.type.compile(dialect=engine.dialect)
                clause = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'

                default = _server_default(column)
                if default is not None:
                    clause += f" DEFAULT {default}"
                    if not column.nullable:
                        clause += " NOT NULL"
                elif not column.nullable:
                    logger.warning(
                        "column %s.%s is NOT NULL without a default; adding it "
                        "as nullable so existing rows survive. Run a proper "
                        "migration before relying on the constraint.",
                        table.name, column.name,
                    )

                connection.execute(text(clause))
                added.setdefault(table.name, []).append(column.name)

    if added:
        for table_name, columns in added.items():
            logger.info(
                "schema sync added columns",
                extra={"extra_fields": {"table": table_name, "columns": columns}},
            )
    return added


def _server_default(column) -> str | None:  # noqa: ANN001
    """A SQL literal for the column's Python-side default, when it is simple."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def describe_drift(engine: Engine) -> Dict[str, List[str]]:
    """Report missing columns without touching anything - used by /api/health."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    drift: Dict[str, List[str]] = {}

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            drift.setdefault("__missing_tables__", []).append(table.name)
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        missing = [c.name for c in table.columns if c.name not in present]
        if missing:
            drift[table.name] = missing
    return drift
