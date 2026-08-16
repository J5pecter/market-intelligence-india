"""Compliance configuration loader and guard rails.

Two jobs:

1. Serve `config/compliance.json` to the API so branding, disclaimers and
   registration claims are data, not hard-coded strings.
2. Refuse to emit a claim the configuration does not support. `assert_no_
   prohibited_claim` is called on admin-supplied text; `verification_badge`
   returns None unless an actual registration number is configured.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "compliance.json"

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_loaded_at: Optional[datetime] = None


class ComplianceViolation(ValueError):
    """Raised when text or configuration would make an unsupported claim."""


def load_compliance(force: bool = False) -> Dict[str, Any]:
    global _cache, _loaded_at
    with _lock:
        if _cache is None or force:
            if not CONFIG_PATH.exists():
                raise FileNotFoundError(
                    f"compliance configuration missing at {CONFIG_PATH}. "
                    "The application refuses to start without it."
                )
            _cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _loaded_at = datetime.now(tz=timezone.utc)
        return _cache


def save_compliance(new_config: Dict[str, Any]) -> Dict[str, Any]:
    """Admin-side write. Validates before touching disk."""
    validate_compliance(new_config)
    CONFIG_PATH.write_text(
        json.dumps(new_config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return load_compliance(force=True)


def validate_compliance(config: Dict[str, Any]) -> None:
    """A registration *status* may not claim more than the *number* supports."""
    pairs = [
        ("sebi_registration_status", "sebi_registration_number"),
        ("research_analyst_status", "research_analyst_number"),
        ("investment_adviser_status", "investment_adviser_number"),
        ("broker_status", "broker_registration_number"),
    ]
    for status_key, number_key in pairs:
        status = (config.get(status_key) or "").upper()
        number = config.get(number_key)
        if status == "REGISTERED" and not number:
            raise ComplianceViolation(
                f"{status_key} is REGISTERED but {number_key} is empty. "
                "The platform will not display a registration claim without a number."
            )
    if config.get("sebi_registration_number") and not config.get("legal_reviewer"):
        raise ComplianceViolation(
            "A registration number was supplied without a `legal_reviewer`. "
            "Record who verified it before publishing."
        )


# --------------------------------------------------------------------------
# Derived, UI-facing views
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrationClaim:
    kind: str
    status: str
    number: Optional[str]
    display: str
    verified: bool


def registration_claims() -> List[RegistrationClaim]:
    cfg = load_compliance()
    out: List[RegistrationClaim] = []
    for kind, status_key, number_key in [
        ("Research Analyst", "research_analyst_status", "research_analyst_number"),
        ("Investment Adviser", "investment_adviser_status", "investment_adviser_number"),
        ("Stock Broker", "broker_status", "broker_registration_number"),
    ]:
        status = (cfg.get(status_key) or "NOT_REGISTERED").upper()
        number = cfg.get(number_key)
        verified = status == "REGISTERED" and bool(number)
        display = (
            f"{kind}: {number}"
            if verified
            else f"{kind}: not registered"
            if status in ("NOT_REGISTERED", "NONE")
            else f"{kind}: {status.replace('_', ' ').lower()}"
        )
        out.append(RegistrationClaim(kind, status, number if verified else None,
                                     display, verified))
    return out


def verification_badge() -> Optional[Dict[str, str]]:
    """Returns a badge ONLY when a registration number is actually configured.

    The UI renders nothing when this is None - no 'SEBI Verified', no shield
    icon, no green tick.
    """
    cfg = load_compliance()
    for claim in registration_claims():
        if claim.verified:
            return {
                "label": f"{claim.kind} registration on file",
                "number": claim.number or "",
                "entity": cfg.get("legal_entity_name") or cfg.get("entity_name", ""),
                "reviewed_by": cfg.get("legal_reviewer") or "",
                "reviewed_on": cfg.get("last_reviewed_date") or "",
                "caveat": "Registration details are self-declared by the operator "
                          "of this deployment. Verify them on SEBI's own portal.",
            }
    return None


def platform_descriptor() -> str:
    cfg = load_compliance()
    if verification_badge() is None:
        return cfg.get(
            "platform_descriptor", "Educational / informational market research platform"
        )
    return cfg.get("entity_name", "Market Intelligence India")


def disclaimers() -> Dict[str, str]:
    cfg = load_compliance()
    return {
        "primary": cfg.get("primary_disclaimer", ""),
        "derivatives": cfg.get("derivatives_disclaimer", ""),
        "generated_signal": cfg.get("generated_signal_disclaimer", ""),
        "external_research": cfg.get("external_research_disclaimer", ""),
        "gmp": cfg.get("gmp_disclaimer", ""),
        "version": cfg.get("disclaimer_version", "0"),
    }


def statistical_claims(app_env: str) -> List[Dict[str, Any]]:
    """Only serve a statistic if it carries a study period, and in PRODUCTION
    only if a human has recorded verifying it against the current source."""
    cfg = load_compliance()
    out: List[Dict[str, Any]] = []
    for claim in cfg.get("statistical_claims", []):
        if not claim.get("study_period"):
            continue
        if app_env == "PRODUCTION" and not claim.get("verified_on"):
            continue
        out.append(claim)
    return out


# --------------------------------------------------------------------------
# Guard rails on generated / admin-entered text
# --------------------------------------------------------------------------


def prohibited_claims() -> List[str]:
    return load_compliance().get("prohibited_claims", [])


def find_prohibited_claims(text: str) -> List[str]:
    if not text:
        return []
    hits: List[str] = []
    for phrase in prohibited_claims():
        if re.search(rf"\b{re.escape(phrase)}\b", text, flags=re.IGNORECASE):
            # "SEBI Registered" is allowed if we genuinely are.
            if phrase.lower().startswith("sebi registered") and verification_badge():
                continue
            hits.append(phrase)
    return hits


def assert_no_prohibited_claim(text: str, field_name: str = "text") -> None:
    hits = find_prohibited_claims(text)
    if hits:
        raise ComplianceViolation(
            f"{field_name} contains claim(s) this deployment cannot support: "
            + ", ".join(hits)
        )


def compliance_snapshot(app_env: str) -> Dict[str, Any]:
    cfg = load_compliance()
    badge = verification_badge()
    return {
        "entity_name": cfg.get("entity_name"),
        "legal_entity_name": cfg.get("legal_entity_name"),
        "entity_type": cfg.get("entity_type"),
        "descriptor": platform_descriptor(),
        "verification_badge": badge,
        "is_registered": badge is not None,
        "registration_claims": [c.__dict__ for c in registration_claims()],
        "disclaimers": disclaimers(),
        "statistical_claims": statistical_claims(app_env),
        "source_urls": cfg.get("source_urls", []),
        "prohibited_claims": cfg.get("prohibited_claims", []),
        "effective_date": cfg.get("effective_date"),
        "last_reviewed_date": cfg.get("last_reviewed_date"),
        "legal_reviewer": cfg.get("legal_reviewer"),
        "config_loaded_at": _loaded_at.isoformat() if _loaded_at else None,
        "review_overdue": _review_overdue(cfg),
    }


def _review_overdue(cfg: Dict[str, Any]) -> bool:
    """Flag a compliance review older than a year so it shows on the admin
    dashboard instead of quietly rotting."""
    raw = cfg.get("last_reviewed_date")
    if not raw:
        return True
    try:
        reviewed = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return True
    return (date.today() - reviewed).days > 365
