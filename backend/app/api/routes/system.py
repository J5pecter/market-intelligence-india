"""Configuration, compliance, methodology, system health and news feed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, rate_limit
from app.core.cache import BACKEND_KIND, breaker_snapshot, cache_backend_healthy
from app.core.compliance import compliance_snapshot, load_compliance
from app.core.config import settings
from app.core.market_calendar import market_state
from app.db.schema_sync import describe_drift
from app.db.session import db_healthy, engine
from app.models.news import NewsArticle, NewsScore
from app.models.system import (ComplianceDocument, DataProviderStatus,
                               JobRunLog)
from app.providers.registry import registry

router = APIRouter(tags=["system"])

BRANDING_PATH = Path(__file__).resolve().parents[3] / "config" / "branding.json"
METHODOLOGY_DIR = Path(__file__).resolve().parents[3] / "docs" / "methodology"


@router.get("/config/branding")
def branding() -> Dict[str, Any]:
    if not BRANDING_PATH.exists():
        raise HTTPException(500, "Branding configuration is missing.")
    payload = json.loads(BRANDING_PATH.read_text(encoding="utf-8"))
    payload.pop("_readme", None)
    return payload


@router.get("/config/compliance")
def compliance() -> Dict[str, Any]:
    return compliance_snapshot(settings.app_env.value)


@router.get("/config/environment")
def environment() -> Dict[str, Any]:
    """What this deployment can and cannot do. No secrets are returned."""
    return {
        "app_env": settings.app_env.value,
        "demo_data_visible": settings.demo_data_allowed,
        "cache_backend": BACKEND_KIND,
        "personal_use_mode": settings.personal_use_mode,
        "provider_chains": {
            capability: settings.providers_for(capability)
            for capability in ("quote", "history", "option_chain", "news",
                               "ipo", "eod", "macro")
        },
        "nse_provider_enabled": settings.enable_nse_provider,
        "exchange_archives_enabled": settings.enable_exchange_archives,
        "scheduler_enabled": settings.enable_scheduler,
        # This endpoint is public, so it reports only whether a broker is
        # wired up and how many fields are outstanding. Naming the fields
        # would put credential identifiers on an unauthenticated response for
        # no benefit; the admin provider-health view carries that detail.
        "brokers": {
            name: {
                "configured": settings.broker_is_configured(name),
                "fields_outstanding": len(_BROKER_FIELDS[name])
                - len(settings.broker_credentials(name)),
            }
            for name in ("angelone", "dhan", "kite", "upstox")
        },
        "realtime_source": (
            settings.configured_brokers[0] if settings.configured_brokers else None
        ),
        "optional_integrations": {
            "telegram": bool(settings.telegram_bot_token),
            "email_smtp": bool(settings.smtp_host),
            "news_api_key": bool(settings.news_api_key),
        },
        "limitations": _limitations(),
    }


_BROKER_FIELDS = {
    "angelone": ("api_key", "client_code", "password", "totp_secret"),
    "dhan": ("client_id", "access_token"),
    "kite": ("api_key", "access_token"),
    "upstox": ("access_token",),
}


def _limitations() -> List[str]:
    notes: List[str] = []
    if settings.configured_brokers:
        notes.append(
            f"Real-time quotes come from your {settings.configured_brokers[0]} "
            "account. They are licensed to you personally and must not be "
            "redistributed."
        )
    else:
        notes.append(
            "No broker is configured, so NOTHING here is real-time. Yahoo "
            "quotes are delayed roughly 15 minutes and exchange archives are "
            "end-of-day. Configure a broker for a live feed."
        )
    notes.append(
        "Intraday history is capped by the provider: roughly 7 days at 1-minute "
        "resolution and 60 days at 5-30 minute resolution."
    )
    if settings.enable_exchange_archives:
        notes.append(
            "Exchange bhavcopy closes are settled prices but are NOT adjusted "
            "for splits or bonuses - apply corporate actions before computing "
            "long-horizon returns from them."
        )
    if not settings.enable_nse_provider:
        notes.append(
            "The NSE adapter is disabled, so option chains, futures and "
            "index breadth have no live source unless entered manually."
        )
    if settings.demo_data_allowed:
        notes.append(
            f"APP_ENV is {settings.app_env.value}, so seeded demonstration rows "
            f"are served alongside live data. Every one of them is badged DEMO."
        )
    if not settings.redis_url:
        notes.append(
            "No Redis is configured; the cache and rate limiter are in-process "
            "and therefore per-worker."
        )
    return notes


@router.get("/methodology")
def methodology() -> Dict[str, Any]:
    """Serves the methodology documents so nothing is a black box."""
    documents = []
    if METHODOLOGY_DIR.exists():
        for path in sorted(METHODOLOGY_DIR.glob("*.md")):
            documents.append({
                "slug": path.stem,
                "title": _first_heading(path),
                "content": path.read_text(encoding="utf-8"),
            })
    return {
        "documents": documents,
        "engine_versions": {
            "technical_analysis": "1.0.0",
            "options_analysis": "1.0.0",
            "greeks": "1.0.0 (Black-Scholes-Merton)",
            "risk": "1.0.0",
            "confidence": "1.0.0",
            "news_scoring": "1.0.0",
            "ipo_scoring": "1.0.0",
            "backtest": "1.0.0",
        },
        "source_files": {
            "indicators": "backend/app/services/indicators.py",
            "technical": "backend/app/services/technical_analysis.py",
            "greeks": "backend/app/services/greeks.py",
            "options": "backend/app/services/options_analysis.py",
            "risk": "backend/app/services/risk.py",
            "confidence": "backend/app/services/confidence.py",
            "fundamentals": "backend/app/services/fundamental_analysis.py",
            "news": "backend/app/services/news_analysis.py",
            "ipo": "backend/app/services/ipo_analysis.py",
            "backtest": "backend/app/services/backtest.py",
            "trade_status": "backend/app/services/trade_status.py",
            "historical_analogues": "backend/app/services/historical_analogue.py",
        },
        "note": "Every formula used by this platform lives in the files above "
                "and is documented inline. Nothing is computed anywhere else.",
    }


def _first_heading(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return path.stem.replace("-", " ").title()


@router.get("/health")
def health(db: Session = Depends(db_session)) -> Dict[str, Any]:
    provider_health = registry.health_report()
    recent_jobs = db.execute(
        select(JobRunLog).order_by(JobRunLog.started_at.desc()).limit(20)
    ).scalars().all()

    failed_recently = db.execute(
        select(func.count(JobRunLog.id))
        .where(JobRunLog.status == "FAILED")
        .where(JobRunLog.started_at >= datetime.now(tz=timezone.utc)
               - timedelta(hours=24))
    ).scalar_one()

    database_ok = db_healthy()
    schema_drift = describe_drift(engine) if database_ok else {}
    cache_ok = cache_backend_healthy()
    degraded = [p["name"] for p in provider_health
                if p["status"] in ("DOWN", "DEGRADED")]

    overall = "OK"
    if not database_ok:
        overall = "DOWN"
    elif schema_drift or degraded or failed_recently:
        overall = "DEGRADED"

    return {
        "status": overall,
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
        "database": {
            "status": "OK" if database_ok else "DOWN",
            "url_scheme": settings.database_url.split(":")[0],
            "schema_drift": schema_drift,
            "schema_note": (
                "Columns the models declare but the database lacks. Run a "
                "migration before trusting the affected endpoints."
                if schema_drift else "Schema matches the models."
            ),
        },
        "cache": {"status": "OK" if cache_ok else "DOWN",
                  "backend": BACKEND_KIND},
        "market": {"status": market_state().status.value},
        "providers": provider_health,
        "circuit_breakers": breaker_snapshot(),
        "jobs": {
            "failed_last_24h": failed_recently,
            "recent": [
                {
                    "job": j.job_name, "status": j.status,
                    "started_at": j.started_at.isoformat(),
                    "duration_ms": j.duration_ms,
                    "records_saved": j.records_saved,
                    "records_rejected": j.records_rejected,
                    "provider": j.provider, "error": j.error,
                }
                for j in recent_jobs
            ],
        },
        "app_env": settings.app_env.value,
        "scheduler_enabled": settings.enable_scheduler,
    }


@router.get("/compliance/documents")
def compliance_documents(db: Session = Depends(db_session)) -> Dict[str, Any]:
    rows = db.execute(
        select(ComplianceDocument).order_by(ComplianceDocument.regulator,
                                            ComplianceDocument.name)
    ).scalars().all()
    snapshot = compliance_snapshot(settings.app_env.value)
    return {
        "configuration": snapshot,
        "documents": [
            {
                "name": d.name, "url": d.url, "regulator": d.regulator,
                "document_type": d.document_type,
                "published_date": d.published_date,
                "effective_date": d.effective_date,
                "version": d.version, "status": d.status,
                "last_checked_at": d.last_checked_at.isoformat()
                if d.last_checked_at else None,
                "checked_by": d.checked_by, "summary": d.summary,
                "applies_to": d.applies_to,
            }
            for d in rows
        ],
        "governance_note": (
            "Regulatory requirements are tracked as data, not hard-coded. An "
            "administrator or legal reviewer records the source, the version "
            "and the date it was last verified. A document with status "
            "UNVERIFIED has not been checked against the regulator's site by a "
            "human in this deployment."
        ),
        "review_overdue": snapshot.get("review_overdue"),
    }


# --------------------------------------------------------------------------
# News feed
# --------------------------------------------------------------------------


@router.get("/news", dependencies=[Depends(rate_limit("news", 120))])
def news_feed(
    symbol: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
    min_impact: float = Query(default=0, ge=0, le=100),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(db_session),
) -> Dict[str, Any]:
    stmt = (
        select(NewsArticle, NewsScore)
        .outerjoin(NewsScore, NewsScore.article_id == NewsArticle.id)
        .where(NewsArticle.is_suppressed.is_(False))
        .order_by(NewsArticle.published_at.desc())
        .limit(limit)
    )
    if symbol:
        stmt = stmt.where(NewsArticle.primary_symbol == symbol.upper())
    if category:
        stmt = stmt.where(NewsArticle.event_category == category.upper())
    if sentiment:
        stmt = stmt.where(NewsScore.sentiment == sentiment.upper())
    if min_impact:
        stmt = stmt.where(NewsScore.impact_score >= min_impact)

    rows = db.execute(stmt).all()
    return {
        "count": len(rows),
        "articles": [
            {
                "id": article.id,
                "headline": article.headline,
                "summary": article.summary,
                "url": article.url,
                "publisher": article.publisher,
                "published_at": article.published_at.isoformat()
                if article.published_at else None,
                "symbol": article.primary_symbol,
                "related_symbols": json.loads(article.related_symbols or "[]"),
                "sector": article.sector,
                "category": article.event_category,
                "sentiment": score.sentiment if score else None,
                "sentiment_score": score.sentiment_score if score else None,
                "impact_score": score.impact_score if score else None,
                "explanation": json.loads(score.explanation)
                if score and score.explanation else None,
                "source": article.source_name,
                "is_demo": article.is_demo,
            }
            for article, score in rows
        ],
        "categories": [
            "EARNINGS", "MANAGEMENT", "REGULATORY", "GOVERNMENT", "ORDER_WIN",
            "ORDER_LOSS", "ACQUISITION", "MERGER", "FUNDRAISING", "INSIDER",
            "PROMOTER", "DIVIDEND", "BUYBACK", "RATING", "LITIGATION",
            "GOVERNANCE", "PRODUCT", "CAPEX", "MACRO", "SECTOR", "OTHER",
        ],
        "note": "Headlines link to the publisher. Article text is never copied "
                "or stored by this platform.",
    }
