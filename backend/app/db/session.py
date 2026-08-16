"""Engine + session factory."""

from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

engine: Engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    future=True,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            class_=Session, future=True)


@event.listens_for(Session, "do_orm_execute")
def _hide_demo_rows(execute_state) -> None:  # noqa: ANN001 - SQLAlchemy event
    """Refuse to load seeded rows outside DEMO and LOCAL.

    Filtering at each call site is whack-a-mole: `providers_for()` keeps the
    demo *provider* out of fetch chains, but seeded rows already in the
    database are read straight from it, and every new endpoint is one more
    chance to forget. Applying the criterion to the ORM itself means a
    PRODUCTION deployment cannot serve a demo row even from a query nobody
    thought to guard.

    Scoped to `Provenance`, so it reaches every table carrying `is_demo` and no
    others. `include_aliases` covers joined and aliased loads.
    """
    from app.core.config import settings

    if settings.demo_data_allowed:
        return
    if not execute_state.is_select or execute_state.is_column_load \
            or execute_state.is_relationship_load:
        return

    from sqlalchemy.orm import with_loader_criteria

    from app.db.base import Provenance

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            Provenance,
            lambda cls: cls.is_demo.is_(False),
            include_aliases=True,
        )
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def db_healthy() -> bool:
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
