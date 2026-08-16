"""Alert engine and delivery.

Conditions are declarative JSON so they can be created in the UI, stored, and
audited. Evaluation is pure: `evaluate_condition` takes a context dict and
returns (fired, value, explanation) - the explanation is what the notification
body shows, so a user always knows why they were pinged.

Delivery channels are all optional and all free: in-app always works; email
needs SMTP credentials; Telegram needs a bot token. A channel that is not
configured reports NOT_CONFIGURED rather than failing silently.
"""

from __future__ import annotations

import json
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.models.user_data import Alert, AlertEvent

logger = logging.getLogger(__name__)

ALERT_TYPES: Dict[str, Dict[str, Any]] = {
    "PRICE_ABOVE": {"label": "Price crosses above", "fields": ["threshold"]},
    "PRICE_BELOW": {"label": "Price falls below", "fields": ["threshold"]},
    "PCT_MOVE": {"label": "Day move exceeds", "fields": ["threshold"]},
    "ENTERS_ENTRY_RANGE": {"label": "Enters a research entry range",
                           "fields": ["research_call_id"]},
    "TARGET_REACHED": {"label": "Research target reached",
                       "fields": ["research_call_id"]},
    "STOP_LOSS_REACHED": {"label": "Research stop reached",
                          "fields": ["research_call_id"]},
    "RSI_ABOVE": {"label": "RSI crosses above", "fields": ["threshold"]},
    "RSI_BELOW": {"label": "RSI falls below", "fields": ["threshold"]},
    "EMA_CROSS": {"label": "Moving-average cross",
                  "fields": ["fast", "slow", "direction"]},
    "VOLUME_MULTIPLE": {"label": "Volume exceeds a multiple of average",
                        "fields": ["threshold"]},
    "OI_CHANGE": {"label": "Open interest change exceeds",
                  "fields": ["threshold"]},
    "IV_CHANGE": {"label": "Implied volatility crosses", "fields": ["threshold"]},
    "VIX_ABOVE": {"label": "India VIX crosses above", "fields": ["threshold"]},
    "NEWS_IMPACT": {"label": "News impact score exceeds",
                    "fields": ["threshold"]},
    "NEWS_KEYWORD": {"label": "Headline contains a keyword",
                     "fields": ["keyword"]},
    "IPO_GMP_CHANGE": {"label": "IPO grey market quote moves by",
                       "fields": ["threshold"]},
    "IPO_SUBSCRIPTION": {"label": "IPO subscription reaches",
                         "fields": ["threshold"]},
    "EARNINGS_ANNOUNCED": {"label": "Results announced", "fields": []},
    "FII_FLOW": {"label": "FII net flow exceeds", "fields": ["threshold"]},
}


@dataclass
class AlertOutcome:
    fired: bool
    value: Optional[float]
    title: str
    body: str
    evidence: Dict[str, Any]


class AlertService:

    # -- evaluation --------------------------------------------------------

    def evaluate(self, alert: Alert, context: Dict[str, Any]) -> AlertOutcome:
        """`context` carries whatever the caller could gather: ltp, previous
        close, indicators, option data, news, ipo figures."""
        condition = _load(alert.condition_json)
        kind = alert.alert_type
        symbol = alert.symbol or condition.get("symbol") or "instrument"

        handler = getattr(self, f"_eval_{kind.lower()}", None)
        if handler is None:
            return AlertOutcome(
                False, None, "Unsupported alert",
                f"Alert type {kind} has no evaluator.", {"alert_type": kind},
            )
        try:
            return handler(alert, condition, context, symbol)
        except Exception as exc:  # noqa: BLE001 - one bad alert must not stop the job
            logger.exception("alert evaluation failed for %s", alert.id)
            return AlertOutcome(
                False, None, "Evaluation failed",
                f"{type(exc).__name__}: {exc}", {"alert_id": alert.id},
            )

    # -- individual evaluators --------------------------------------------

    def _eval_price_above(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("ltp"), condition.get("threshold"), ">", symbol,
            "price", "Rs ", context,
        )

    def _eval_price_below(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("ltp"), condition.get("threshold"), "<", symbol,
            "price", "Rs ", context,
        )

    def _eval_pct_move(self, alert, condition, context, symbol) -> AlertOutcome:
        value = context.get("change_pct")
        threshold = condition.get("threshold")
        if value is None or threshold is None:
            return _not_ready(symbol, "day change")
        fired = abs(value) >= abs(threshold)
        return AlertOutcome(
            fired, value,
            f"{symbol} moved {value:+.2f}% today",
            f"The day change of {value:+.2f}% reached the {abs(threshold):.2f}% "
            f"threshold you set.",
            {"change_pct": value, "threshold": threshold},
        )

    def _eval_rsi_above(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("rsi_14"), condition.get("threshold"), ">", symbol,
            "RSI(14)", "", context,
        )

    def _eval_rsi_below(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("rsi_14"), condition.get("threshold"), "<", symbol,
            "RSI(14)", "", context,
        )

    def _eval_volume_multiple(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("volume_ratio_20"), condition.get("threshold"), ">=",
            symbol, "relative volume", "", context, suffix="x",
        )

    def _eval_vix_above(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("india_vix"), condition.get("threshold"), ">",
            "India VIX", "level", "", context,
        )

    def _eval_ema_cross(self, alert, condition, context, symbol) -> AlertOutcome:
        fast_key = f"ema_{condition.get('fast', 50)}"
        slow_key = f"ema_{condition.get('slow', 200)}"
        fast, slow = context.get(fast_key), context.get(slow_key)
        prev_fast = context.get(f"prev_{fast_key}")
        prev_slow = context.get(f"prev_{slow_key}")
        if None in (fast, slow, prev_fast, prev_slow):
            return _not_ready(symbol, "moving averages including the previous bar")
        direction = (condition.get("direction") or "above").lower()
        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow
        fired = crossed_up if direction == "above" else crossed_down
        return AlertOutcome(
            fired, fast,
            f"{symbol}: {fast_key} crossed {direction} {slow_key}",
            f"{fast_key} moved from {prev_fast:.2f} to {fast:.2f} while "
            f"{slow_key} moved from {prev_slow:.2f} to {slow:.2f}.",
            {"fast": fast, "slow": slow, "prev_fast": prev_fast,
             "prev_slow": prev_slow, "direction": direction},
        )

    def _eval_oi_change(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("oi_change_pct"), condition.get("threshold"), ">=",
            symbol, "open-interest change", "", context, suffix="%", absolute=True,
        )

    def _eval_iv_change(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("implied_volatility"), condition.get("threshold"), ">=",
            symbol, "implied volatility", "", context, suffix="%",
        )

    def _eval_news_impact(self, alert, condition, context, symbol) -> AlertOutcome:
        articles = context.get("news") or []
        threshold = condition.get("threshold", 60)
        hits = [a for a in articles if (a.get("impact_score") or 0) >= threshold]
        if not hits:
            return AlertOutcome(False, None, "", "", {"checked": len(articles)})
        top = max(hits, key=lambda a: a["impact_score"])
        return AlertOutcome(
            True, top["impact_score"],
            f"{symbol}: {top['sentiment'].lower()} news, impact "
            f"{top['impact_score']}/100",
            f"{top['headline']} ({top.get('publisher') or 'unknown source'}). "
            f"{top.get('explanation', '')}",
            {"article": top, "threshold": threshold},
        )

    def _eval_news_keyword(self, alert, condition, context, symbol) -> AlertOutcome:
        keyword = (condition.get("keyword") or "").lower().strip()
        articles = context.get("news") or []
        if not keyword:
            return AlertOutcome(False, None, "", "", {})
        hits = [a for a in articles if keyword in (a.get("headline") or "").lower()]
        if not hits:
            return AlertOutcome(False, None, "", "", {"checked": len(articles)})
        return AlertOutcome(
            True, float(len(hits)),
            f"{symbol}: '{keyword}' appeared in {len(hits)} headline(s)",
            hits[0]["headline"],
            {"keyword": keyword, "matches": [h["headline"] for h in hits[:5]]},
        )

    def _eval_enters_entry_range(self, alert, condition, context,
                                 symbol) -> AlertOutcome:
        status = context.get("call_status")
        fired = status == "WITHIN_ENTRY"
        return AlertOutcome(
            fired, context.get("ltp"),
            f"{symbol} entered the published entry range",
            context.get("call_status_reason") or "",
            {"status": status},
        )

    def _eval_target_reached(self, alert, condition, context,
                             symbol) -> AlertOutcome:
        status = context.get("call_status")
        return AlertOutcome(
            status == "TARGET_ACHIEVED", context.get("ltp"),
            f"{symbol}: research target reached",
            context.get("call_status_reason") or "",
            {"status": status},
        )

    def _eval_stop_loss_reached(self, alert, condition, context,
                                symbol) -> AlertOutcome:
        status = context.get("call_status")
        return AlertOutcome(
            status == "STOP_LOSS_TRIGGERED", context.get("ltp"),
            f"{symbol}: research stop reached",
            context.get("call_status_reason") or "",
            {"status": status},
        )

    def _eval_ipo_gmp_change(self, alert, condition, context,
                             symbol) -> AlertOutcome:
        current, previous = context.get("gmp"), context.get("previous_gmp")
        threshold = condition.get("threshold", 5)
        if current is None or previous is None:
            return _not_ready(symbol, "two grey-market observations")
        change = current - previous
        fired = abs(change) >= abs(threshold)
        return AlertOutcome(
            fired, current,
            f"{symbol}: grey market quote moved {change:+.1f}",
            f"The unofficial grey-market quote moved from {previous} to "
            f"{current}. This is a dealer quote, not an exchange price.",
            {"gmp": current, "previous_gmp": previous, "change": change},
        )

    def _eval_ipo_subscription(self, alert, condition, context,
                               symbol) -> AlertOutcome:
        return self._threshold(
            context.get("total_times"), condition.get("threshold"), ">=",
            symbol, "total subscription", "", context, suffix="x",
        )

    def _eval_earnings_announced(self, alert, condition, context,
                                 symbol) -> AlertOutcome:
        reported = context.get("earnings_reported")
        return AlertOutcome(
            bool(reported), None,
            f"{symbol}: results reported",
            context.get("earnings_summary") or "Results have been reported.",
            {"reported": reported},
        )

    def _eval_fii_flow(self, alert, condition, context, symbol) -> AlertOutcome:
        return self._threshold(
            context.get("fii_net"), condition.get("threshold"), "<=",
            "FII cash flow", "net flow", "Rs ", context, suffix=" crore",
        )

    # -- shared helper -----------------------------------------------------

    @staticmethod
    def _threshold(value, threshold, op, subject, metric, prefix, context,
                   suffix: str = "", absolute: bool = False) -> AlertOutcome:
        if value is None or threshold is None:
            return _not_ready(subject, metric)
        compare = abs(value) if absolute else value
        fired = {
            ">": compare > threshold, ">=": compare >= threshold,
            "<": compare < threshold, "<=": compare <= threshold,
        }[op]
        word = {">": "above", ">=": "at or above", "<": "below",
                "<=": "at or below"}[op]
        return AlertOutcome(
            fired, value,
            f"{subject}: {metric} is {word} {prefix}{threshold}{suffix}",
            f"{metric.capitalize()} is {prefix}{value}{suffix} against your "
            f"threshold of {prefix}{threshold}{suffix}. "
            f"Observed at {context.get('observed_at', 'unknown time')} from "
            f"{context.get('source', 'the configured provider')}.",
            {"value": value, "threshold": threshold, "operator": op,
             "source": context.get("source"),
             "data_status": context.get("data_status")},
        )

    # -- firing ------------------------------------------------------------

    def should_fire(self, alert: Alert, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(tz=timezone.utc)
        if not alert.is_active:
            return False
        if alert.trigger_once and alert.trigger_count > 0:
            return False
        if alert.last_triggered_at:
            last = alert.last_triggered_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(minutes=alert.cooldown_minutes):
                return False
        return True

    def fire(self, db: Session, alert: Alert, outcome: AlertOutcome) -> AlertEvent:
        event = AlertEvent(
            alert_id=alert.id, user_id=alert.user_id, title=outcome.title,
            body=outcome.body, triggered_value=outcome.value,
            evidence_json=json.dumps(outcome.evidence, default=str),
        )
        db.add(event)
        alert.last_triggered_at = datetime.now(tz=timezone.utc)
        alert.trigger_count += 1
        if alert.trigger_once:
            alert.is_active = False
        db.flush()

        user = db.execute(
            select(User).where(User.id == alert.user_id)
        ).scalars().first()
        event.delivery_status = json.dumps(
            self.deliver(alert, event, user)
        )
        db.flush()
        return event

    # -- delivery ----------------------------------------------------------

    def deliver(self, alert: Alert, event: AlertEvent,
                user: Optional[User]) -> Dict[str, str]:
        channels = [c.strip() for c in (alert.channels or "in_app").split(",")
                    if c.strip()]
        status: Dict[str, str] = {}
        for channel in channels:
            if channel == "in_app":
                status["in_app"] = "DELIVERED"
            elif channel == "browser_push":
                # The browser polls /api/alerts/events; nothing to push server-side.
                status["browser_push"] = "QUEUED_FOR_CLIENT_POLL"
            elif channel == "email":
                status["email"] = self._email(event, user)
            elif channel == "telegram":
                status["telegram"] = self._telegram(event, user)
            else:
                status[channel] = "UNKNOWN_CHANNEL"
        return status

    @staticmethod
    def _email(event: AlertEvent, user: Optional[User]) -> str:
        if not settings.smtp_host or not user or not user.email:
            return "NOT_CONFIGURED"
        if not user.notify_email:
            return "DISABLED_BY_USER"
        try:
            message = EmailMessage()
            message["Subject"] = f"[{settings.app_name}] {event.title}"
            message["From"] = settings.smtp_from or settings.smtp_user
            message["To"] = user.email
            message.set_content(
                f"{event.title}\n\n{event.body}\n\n"
                f"This is an automated alert from an informational research "
                f"platform. It is not investment advice."
            )
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                              timeout=15) as server:
                server.starttls()
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
            return "DELIVERED"
        except Exception as exc:  # noqa: BLE001
            logger.warning("email delivery failed: %s", exc)
            return f"FAILED: {type(exc).__name__}"

    @staticmethod
    def _telegram(event: AlertEvent, user: Optional[User]) -> str:
        token = settings.telegram_bot_token
        chat_id = (user.telegram_chat_id if user else None) or settings.telegram_chat_id
        if not token or not chat_id:
            return "NOT_CONFIGURED"
        if user and not user.notify_telegram:
            return "DISABLED_BY_USER"
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"*{event.title}*\n\n{event.body}\n\n"
                            f"_Informational only. Not investment advice._",
                    "parse_mode": "Markdown",
                },
                timeout=12,
            )
            return "DELIVERED" if response.ok else f"FAILED: HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram delivery failed: %s", exc)
            return f"FAILED: {type(exc).__name__}"

    # -- introspection -----------------------------------------------------

    @staticmethod
    def available_channels() -> Dict[str, Dict[str, Any]]:
        return {
            "in_app": {"available": True, "requires": None},
            "browser_push": {"available": True,
                             "requires": "browser notification permission"},
            "email": {"available": bool(settings.smtp_host),
                      "requires": "SMTP_HOST / SMTP_USER / SMTP_PASSWORD"},
            "telegram": {"available": bool(settings.telegram_bot_token),
                         "requires": "TELEGRAM_BOT_TOKEN and a chat id"},
        }


def _load(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _not_ready(subject: str, what: str) -> AlertOutcome:
    return AlertOutcome(
        False, None, "",
        f"Could not evaluate: {what} was unavailable for {subject}.",
        {"reason": f"missing {what}"},
    )


alert_service = AlertService()
