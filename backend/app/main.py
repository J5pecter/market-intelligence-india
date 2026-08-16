"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from app.api.routes import (admin, auth, derivatives, documents, exchange,
                            ipo, market, research, stocks, system, user_data)
from app.core.compliance import load_compliance
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.jobs.scheduler import scheduler_status, start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    # The app refuses to start without a compliance configuration - it is the
    # thing that decides how the platform is allowed to describe itself.
    load_compliance()

    import app.models  # noqa: F401 - registers every table on Base.metadata

    Base.metadata.create_all(bind=engine)

    # create_all adds tables but never columns. On SQLite - the zero-setup
    # default - reconcile additively so a pulled change does not surface as
    # a confusing 500. PostgreSQL deployments use Alembic instead.
    from app.db.schema_sync import describe_drift, sync_sqlite_schema

    added = sync_sqlite_schema(engine)
    drift = describe_drift(engine)
    if drift:
        logger.warning(
            "the database is missing columns the models declare; run a "
            "migration before trusting affected endpoints",
            extra={"extra_fields": {"drift": drift}},
        )
    logger.info("database schema ensured",
                extra={"extra_fields": {"columns_added": added}})

    db = SessionLocal()
    try:
        from app.db.seed import bootstrap_admin, seed_all

        bootstrap_admin(db)
        if settings.demo_data_allowed:
            counts = seed_all(db)
            logger.info("demo dataset ensured: %s", counts)
    except Exception:  # noqa: BLE001 - a seed failure must not stop the API
        logger.exception("seeding failed")
        db.rollback()
    finally:
        db.close()

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Evidence-first research and market-intelligence API for Indian "
        "markets. Every value returned carries its source, its timestamp and "
        "its data status. Nothing here is investment advice."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=()"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """Return a readable message instead of a raw pydantic dump."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "The request could not be validated.",
            "details": jsonable_encoder(exc.errors()),
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    """Never leak a stack trace to a client; log it instead."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Something went wrong on our side.",
            "detail": "The failure has been logged. Data panels will show "
                      "'temporarily unavailable' rather than stale values.",
        },
    )


for router in (auth.router, market.router, stocks.router, derivatives.router,
               ipo.router, research.router, documents.router, exchange.router,
               user_data.router, system.router, admin.router):
    app.include_router(router, prefix=settings.api_prefix)


@app.get("/")
def root() -> Dict[str, Any]:
    from app.core.compliance import platform_descriptor, verification_badge

    return {
        "name": settings.app_name,
        "descriptor": platform_descriptor(),
        "verified": verification_badge() is not None,
        "app_env": settings.app_env.value,
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
        "scheduler": scheduler_status(),
        "notice": (
            "Informational and educational research platform. Not investment "
            "advice, not a broker, and not a guarantee of any outcome."
        ),
    }
