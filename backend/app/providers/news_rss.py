"""News via Google News RSS.

Free, keyed on a search query, no API key. RSS is a published syndication
format intended for programmatic consumption, so this is a documented,
permitted access path rather than scraping a page.

The adapter returns headline + link + publisher + timestamp only. It does not
fetch or reproduce article bodies - the platform links out to the publisher.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, List, Optional
from urllib.parse import quote_plus

from app.core.cache import cached_call, get_breaker, rate_limit_ok
from app.core.data_quality import SourceReliability, Sourced, freshness
from app.providers.base import (MarketDataProvider, NewsItem, ProviderError)

logger = logging.getLogger(__name__)

try:  # pragma: no cover
    import feedparser
except Exception:  # noqa: BLE001
    feedparser = None

_RSS = "https://news.google.com/rss/search"


class GoogleNewsRssProvider(MarketDataProvider):
    name = "google_news_rss"
    display_name = "Google News RSS (India)"
    base_url = _RSS
    reliability = SourceReliability.LOW
    is_delayed = True
    requires_auth = False
    rate_limit_per_minute = 30
    terms_url = "https://news.google.com/"
    licence_note = (
        "Aggregated headlines and links only. Article text belongs to the "
        "originating publisher and is not stored or reproduced."
    )

    def get_news(
        self,
        symbol: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 25,
        company_name: Optional[str] = None,
        **kw: Any,
    ) -> Sourced[List[NewsItem]]:
        if feedparser is None:
            raise ProviderError("feedparser is not installed")
        breaker = get_breaker("google_news_rss")
        if not breaker.allows():
            raise ProviderError(f"google_news_rss breaker is {breaker.state}")
        if not rate_limit_ok("google_news_rss", self.rate_limit_per_minute):
            raise ProviderError("google news rss budget exhausted")

        search = query or _build_query(symbol, company_name)
        if not search:
            raise ProviderError("news query is empty")

        url = (
            f"{_RSS}?q={quote_plus(search)}&hl=en-IN&gl=IN&ceid=IN:en"
        )

        def _fetch() -> List[dict]:
            try:
                feed = feedparser.parse(url)
            except Exception as exc:  # noqa: BLE001
                breaker.record_failure()
                raise ProviderError(f"rss fetch failed: {exc}") from exc
            if getattr(feed, "bozo", 0) and not feed.entries:
                breaker.record_failure()
                raise ProviderError("rss feed could not be parsed")
            breaker.record_success()
            rows = []
            for entry in feed.entries[: max(limit, 1)]:
                rows.append({
                    "title": html.unescape(getattr(entry, "title", "")).strip(),
                    "link": getattr(entry, "link", ""),
                    "published": getattr(entry, "published", None),
                    "source": _entry_source(entry),
                    "summary": _strip_html(getattr(entry, "summary", "") or ""),
                })
            return rows

        raw = cached_call(f"gnews:{hashlib.sha1(url.encode()).hexdigest()}", 600,
                          _fetch) or []

        items: List[NewsItem] = []
        for row in raw:
            if not row["title"] or not row["link"]:
                continue
            published = _parse_rfc822(row.get("published"))
            items.append(NewsItem(
                headline=_strip_source_suffix(row["title"], row["source"]),
                url=row["link"],
                publisher=row["source"] or "Google News",
                published_at=published,
                summary=row.get("summary") or None,
                primary_symbol=symbol.upper() if symbol else None,
            ))

        newest = max((i.published_at for i in items if i.published_at),
                     default=None)
        return Sourced(
            value=items,
            provider=self.name,
            source_name=self.display_name,
            status=freshness(newest, "news", provider_is_delayed=True),
            observed_at=newest,
            reliability=self.reliability,
            source_url=url,
            license_note=self.licence_note,
            notes=(
                "Third-party aggregator. Relevance is keyword-based and may "
                "include unrelated companies with similar names."
            ),
        )


def _build_query(symbol: Optional[str], company_name: Optional[str]) -> str:
    parts = []
    if company_name:
        parts.append(f'"{company_name}"')
    if symbol:
        parts.append(f'"{symbol}"')
    if not parts:
        return ""
    # Scope to Indian markets so "VOLTAS" does not return HVAC trade press.
    return f"({' OR '.join(parts)}) (NSE OR BSE OR shares OR stock)"


def _entry_source(entry: Any) -> str:
    source = getattr(entry, "source", None)
    if source is not None:
        title = getattr(source, "title", None)
        if title:
            return str(title)
    title = getattr(entry, "title", "") or ""
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return ""


def _strip_source_suffix(title: str, source: str) -> str:
    """Google appends ' - Publisher' to every headline."""
    if source and title.endswith(f" - {source}"):
        return title[: -(len(source) + 3)].strip()
    return title


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:1000]


def _parse_rfc822(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
