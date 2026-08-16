"""News classification and impact scoring.

Generic sentiment is close to useless on financial headlines - "profit falls
40% but beats estimates" is positive for the tape and negative to a general
sentiment model. So the impact score is built from six components, each shown
separately:

    headline sentiment  x  event importance  x  company relevance
    x  sector relevance  x  source credibility  ( x historical reaction )

The lexicon below is domain-specific and deliberately small: every term is
inspectable, and unknown headlines score neutral rather than being guessed at.
No external model, no API key, no black box.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

METHODOLOGY = "/methodology#news"

# --------------------------------------------------------------------------
# Event taxonomy: pattern -> (category, base importance 0-1, directional prior)
# The prior is *not* sentiment; it is how the market typically treats the event
# type before reading the specifics.
# --------------------------------------------------------------------------

EVENT_PATTERNS: List[Tuple[str, str, float, float]] = [
    (r"\b(q[1-4]|quarterly|quarter)\s*(results?|earnings|numbers)\b", "EARNINGS", 0.9, 0.0),
    (r"\b(results?|earnings)\s*(announce|declar|report|beat|miss)", "EARNINGS", 0.9, 0.0),
    (r"\bprofit\s*(rise|rises|jump|surge|up|grow)", "EARNINGS", 0.85, 0.7),
    (r"\bprofit\s*(fall|falls|drop|decline|down|slump|plunge)", "EARNINGS", 0.85, -0.7),
    (r"\b(order|contract)\s*(win|wins|won|bag|bags|secure|receives?)\b", "ORDER_WIN", 0.75, 0.7),
    (r"\b(loses?|lost)\s*(order|contract)\b", "ORDER_LOSS", 0.75, -0.7),
    (r"\b(acquisition|acquires?|acquire|takeover)\b", "ACQUISITION", 0.8, 0.3),
    (r"\bmerger|merges?\b", "MERGER", 0.8, 0.2),
    (r"\b(qip|fund\s*rais|raise[sd]?\s*(funds|capital)|rights issue|preferential)\b",
     "FUNDRAISING", 0.7, -0.1),
    (r"\bbuyback\b", "BUYBACK", 0.75, 0.5),
    (r"\bdividend\b", "DIVIDEND", 0.55, 0.3),
    (r"\bbonus\s*(issue|share)|stock\s*split\b", "DIVIDEND", 0.5, 0.2),
    (r"\b(upgrade[sd]?|raises?\s*target|initiate[sd]?\s*coverage.*buy)\b",
     "RATING", 0.6, 0.6),
    (r"\b(downgrade[sd]?|cuts?\s*target|slashes?\s*target)\b", "RATING", 0.6, -0.6),
    (r"\b(sebi|rbi|cci|nclt|regulator|regulatory)\b", "REGULATORY", 0.8, -0.1),
    (r"\b(probe|investigation|raid|summon|show\s*cause|penalt|fine[sd]?)\b",
     "REGULATORY", 0.9, -0.8),
    (r"\b(fraud|scam|misappropriat|siphon|forensic\s*audit)\b", "GOVERNANCE", 1.0, -0.95),
    (r"\b(resign|steps?\s*down|quits?)\b.*\b(ceo|cfo|md|chairman|director|auditor)\b",
     "MANAGEMENT", 0.85, -0.5),
    (r"\b(appoint|names?)\b.*\b(ceo|cfo|md|chairman)\b", "MANAGEMENT", 0.6, 0.1),
    (r"\b(promoter)\b.*\b(stake|sell|sold|buy|bought|pledge)\b", "PROMOTER", 0.8, 0.0),
    (r"\bpledge[sd]?\b", "PROMOTER", 0.8, -0.6),
    (r"\binsider\s*(trad|deal)", "INSIDER", 0.7, 0.0),
    (r"\b(lawsuit|litigation|court|tribunal|arbitration)\b", "LITIGATION", 0.7, -0.5),
    (r"\b(capacity\s*expansion|new\s*plant|capex|greenfield|brownfield)\b",
     "CAPEX", 0.6, 0.4),
    (r"\b(launch|unveil|introduce)[sd]?\b", "PRODUCT", 0.5, 0.3),
    (r"\b(gdp|inflation|repo\s*rate|monetary\s*policy|budget|fed|crude)\b",
     "MACRO", 0.6, 0.0),
    (r"\b(delisting|insolvency|ibc|bankrupt|default)\b", "REGULATORY", 1.0, -0.9),
]

# Directional lexicon. Weight reflects how strongly the term moves a reading.
POSITIVE_TERMS: Dict[str, float] = {
    "beats": 0.8, "beat": 0.8, "surge": 0.8, "surges": 0.8, "jumps": 0.7,
    "jump": 0.7, "rally": 0.7, "rallies": 0.7, "record high": 0.9,
    "all-time high": 0.9, "upgrade": 0.8, "outperform": 0.6, "strong": 0.5,
    "robust": 0.5, "expands": 0.4, "expansion": 0.4, "wins": 0.7, "win": 0.7,
    "bags": 0.7, "approval": 0.6, "approved": 0.6, "growth": 0.4,
    "turnaround": 0.6, "profit": 0.3, "highest ever": 0.8, "doubles": 0.7,
    "multi-year high": 0.7, "raises guidance": 0.9, "hikes": 0.3,
}

NEGATIVE_TERMS: Dict[str, float] = {
    "misses": 0.8, "miss": 0.8, "slump": 0.8, "slumps": 0.8, "plunge": 0.9,
    "plunges": 0.9, "crash": 0.9, "crashes": 0.9, "downgrade": 0.8,
    "underperform": 0.6, "weak": 0.5, "weakness": 0.5, "loss": 0.7,
    "losses": 0.7, "decline": 0.5, "declines": 0.5, "falls": 0.5, "fall": 0.5,
    "drop": 0.5, "drops": 0.5, "cuts": 0.5, "warning": 0.7, "warns": 0.7,
    "probe": 0.8, "fraud": 0.95, "default": 0.9, "resigns": 0.6,
    "record low": 0.8, "52-week low": 0.7, "halts": 0.6, "recall": 0.7,
    "cuts guidance": 0.9, "impairment": 0.7, "writedown": 0.7, "write-off": 0.7,
}

NEGATORS = {"not", "no", "never", "without", "despite", "denies", "denied"}

# Source credibility. Anything unlisted scores 0.5 rather than being assumed
# good, and the payload names the source so a reader can judge for themselves.
SOURCE_CREDIBILITY: Dict[str, float] = {
    "nse": 1.0, "bse": 1.0, "sebi": 1.0, "rbi": 1.0,
    "company filing": 1.0, "exchange filing": 1.0,
    "reuters": 0.9, "bloomberg": 0.9, "pti": 0.85, "ani": 0.75,
    "the hindu businessline": 0.8, "business standard": 0.8,
    "the economic times": 0.75, "economic times": 0.75, "mint": 0.8,
    "livemint": 0.8, "moneycontrol": 0.7, "cnbc-tv18": 0.7,
    "financial express": 0.7, "ndtv profit": 0.7, "zee business": 0.6,
}


@dataclass
class NewsAssessment:
    headline: str
    url: str
    publisher: str
    published_at: Optional[str]
    event_category: str
    sentiment: str
    sentiment_score: float          # -1 .. +1
    impact_score: float             # 0 .. 100
    components: Dict[str, float] = field(default_factory=dict)
    matched_terms: List[str] = field(default_factory=list)
    explanation: str = ""
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "methodology": METHODOLOGY}


class NewsAnalysisService:
    MODEL_VERSION = "1.0.0"

    def assess(
        self,
        headline: str,
        publisher: str = "",
        url: str = "",
        published_at: Optional[datetime] = None,
        symbol: Optional[str] = None,
        company_name: Optional[str] = None,
        sector: Optional[str] = None,
        historical_reaction: Optional[float] = None,
    ) -> NewsAssessment:
        text = (headline or "").lower()

        category, importance, prior = self._classify(text)
        sentiment_score, matched = self._sentiment(text, prior)
        company_relevance = self._company_relevance(text, symbol, company_name)
        sector_relevance = self._sector_relevance(text, sector)
        credibility = self._credibility(publisher)
        recency = self._recency(published_at)

        # Impact is a product of magnitude and relevance, not a sum: a huge
        # event about a company you do not hold is not a big signal for you.
        magnitude = abs(sentiment_score)
        impact = (
            100.0
            * (0.35 * importance + 0.35 * magnitude + 0.30 * (
                historical_reaction if historical_reaction is not None else 0.5
            ))
            * (0.55 + 0.45 * company_relevance)
            * (0.80 + 0.20 * sector_relevance)
            * (0.65 + 0.35 * credibility)
            * recency
        )
        impact = round(max(0.0, min(100.0, impact)), 1)

        label = (
            "POSITIVE" if sentiment_score >= 0.15
            else "NEGATIVE" if sentiment_score <= -0.15
            else "NEUTRAL"
        )

        components = {
            "headline_sentiment": round(sentiment_score, 3),
            "event_importance": round(importance, 3),
            "historical_reaction": (
                round(historical_reaction, 3)
                if historical_reaction is not None else None
            ),
            "company_relevance": round(company_relevance, 3),
            "sector_relevance": round(sector_relevance, 3),
            "source_credibility": round(credibility, 3),
            "recency_multiplier": round(recency, 3),
        }

        limitations = [
            "Scored from the headline only. The article body is not fetched or "
            "reproduced, so nuance inside it is not captured.",
            "The lexicon is a fixed, inspectable word list - it will misread "
            "sarcasm, unusual phrasing and headlines about a different company "
            "with a similar name.",
        ]
        if historical_reaction is None:
            limitations.append(
                "No measured price reaction to comparable past headlines was "
                "available, so a neutral 0.5 was used for that component."
            )
        if publisher.lower() not in SOURCE_CREDIBILITY:
            limitations.append(
                f"'{publisher}' is not in the credibility table; 0.5 was used."
            )

        return NewsAssessment(
            headline=headline,
            url=url,
            publisher=publisher,
            published_at=published_at.isoformat() if published_at else None,
            event_category=category,
            sentiment=label,
            sentiment_score=round(sentiment_score, 3),
            impact_score=impact,
            components=components,
            matched_terms=matched,
            explanation=self._explain(category, label, impact, components,
                                      matched),
            limitations=limitations,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _classify(text: str) -> Tuple[str, float, float]:
        best = ("OTHER", 0.35, 0.0)
        for pattern, category, importance, prior in EVENT_PATTERNS:
            if re.search(pattern, text):
                if importance > best[1]:
                    best = (category, importance, prior)
        return best

    @staticmethod
    def _sentiment(text: str, prior: float) -> Tuple[float, List[str]]:
        """Lexicon score blended with the event-type prior.

        Negation flips a term when a negator appears within three words before
        it - crude, but it catches "does not beat estimates".
        """
        words = re.findall(r"[a-z0-9'-]+", text)
        matched: List[str] = []
        score = 0.0
        weight_sum = 0.0

        def _scan(lexicon: Dict[str, float], sign: float) -> None:
            nonlocal score, weight_sum
            for term, weight in lexicon.items():
                if term not in text:
                    continue
                position = _word_position(words, term)
                negated = (
                    position is not None
                    and any(w in NEGATORS for w in words[max(0, position - 3):position])
                )
                effective = -sign if negated else sign
                score += effective * weight
                weight_sum += weight
                matched.append(f"{term}{' (negated)' if negated else ''}")

        _scan(POSITIVE_TERMS, 1.0)
        _scan(NEGATIVE_TERMS, -1.0)

        lexical = score / weight_sum if weight_sum else 0.0
        # Blend: the lexicon leads when it fired, the prior fills the silence.
        if weight_sum == 0:
            blended = prior * 0.6
        else:
            blended = 0.7 * lexical + 0.3 * prior
        return max(-1.0, min(1.0, blended)), matched

    @staticmethod
    def _company_relevance(text: str, symbol: Optional[str],
                           company_name: Optional[str]) -> float:
        if not symbol and not company_name:
            return 0.5
        score = 0.0
        if symbol and symbol.lower() in text:
            score = max(score, 0.85)
        if company_name:
            name = company_name.lower()
            for suffix in (" ltd", " limited", " ltd.", " india", " corporation"):
                name = name.replace(suffix, "")
            name = name.strip()
            if name and name in text:
                score = max(score, 1.0)
            else:
                tokens = [t for t in name.split() if len(t) > 3]
                if tokens and all(t in text for t in tokens[:2]):
                    score = max(score, 0.75)
        return score if score else 0.25

    @staticmethod
    def _sector_relevance(text: str, sector: Optional[str]) -> float:
        if not sector:
            return 0.5
        tokens = [t for t in sector.lower().split() if len(t) > 3]
        if not tokens:
            return 0.5
        return 1.0 if any(t in text for t in tokens) else 0.35

    @staticmethod
    def _credibility(publisher: str) -> float:
        key = (publisher or "").strip().lower()
        if key in SOURCE_CREDIBILITY:
            return SOURCE_CREDIBILITY[key]
        for known, score in SOURCE_CREDIBILITY.items():
            if known in key:
                return score
        return 0.5

    @staticmethod
    def _recency(published_at: Optional[datetime]) -> float:
        """Old news is not news. Decays to 0.3 over a week."""
        if published_at is None:
            return 0.7
        published = (
            published_at if published_at.tzinfo
            else published_at.replace(tzinfo=timezone.utc)
        )
        hours = (datetime.now(tz=timezone.utc) - published).total_seconds() / 3600.0
        if hours <= 6:
            return 1.0
        if hours <= 24:
            return 0.9
        if hours <= 72:
            return 0.65
        if hours <= 168:
            return 0.45
        return 0.3

    @staticmethod
    def _explain(category, label, impact, components, matched) -> str:
        parts = [
            f"Classified as {category.replace('_', ' ').lower()} with importance "
            f"{components['event_importance']:.2f}."
        ]
        if matched:
            parts.append(f"Lexicon matched: {', '.join(matched[:6])}.")
        else:
            parts.append(
                "No lexicon term matched; the reading comes from the event type "
                "prior alone."
            )
        parts.append(
            f"Directional reading is {label.lower()} "
            f"({components['headline_sentiment']:+.2f} on a -1 to +1 scale)."
        )
        parts.append(
            f"Impact {impact}/100 after weighting for company relevance "
            f"{components['company_relevance']:.2f}, source credibility "
            f"{components['source_credibility']:.2f} and recency "
            f"{components['recency_multiplier']:.2f}."
        )
        return " ".join(parts)

    # ------------------------------------------------------------------

    def build_evidence(self, assessments: List[NewsAssessment]):
        """Fold a set of headlines into a NEWS evidence chain."""
        from app.services.evidence import EvidenceChain, EvidenceItem, Stance

        chain = EvidenceChain(dimension="NEWS", methodology_ref=METHODOLOGY)
        if not assessments:
            chain.note_gap("no recent headlines matched this instrument")
            chain.finalise()
            chain.summary = chain.explain()
            return chain

        material = [a for a in assessments if a.impact_score >= 25]
        if not material:
            chain.add(EvidenceItem(
                metric="Recent news", value=len(assessments),
                stance=Stance.NEUTRAL, weight=0.8,
                calculation=f"{len(assessments)} headlines, none scoring 25+ impact",
                interpretation=(
                    f"{len(assessments)} headlines were found but none cleared "
                    f"the materiality threshold of 25/100."
                ),
                source="news pipeline",
            ))
        for item in sorted(material, key=lambda a: a.impact_score,
                           reverse=True)[:6]:
            chain.add(EvidenceItem(
                metric=f"{item.event_category.replace('_', ' ').title()}: "
                       f"{item.headline[:110]}",
                value=item.impact_score,
                stance=(
                    Stance.POSITIVE if item.sentiment == "POSITIVE"
                    else Stance.NEGATIVE if item.sentiment == "NEGATIVE"
                    else Stance.NEUTRAL
                ),
                weight=0.6 + 0.9 * (item.impact_score / 100.0),
                unit="impact",
                calculation=item.explanation,
                interpretation=(
                    f"{item.publisher or 'unknown source'}, "
                    f"{(item.published_at or 'undated')[:16]} - impact "
                    f"{item.impact_score}/100."
                ),
                source=item.publisher, source_url=item.url,
            ))

        chain.finalise()
        chain.summary = chain.explain()
        chain.limit(
            "News evidence reflects headlines the aggregator surfaced. Absence "
            "of negative news is not evidence of its absence in the world."
        )
        return chain


def url_hash(url: str) -> str:
    return hashlib.sha256((url or "").strip().lower().encode()).hexdigest()[:32]


def _word_position(words: List[str], term: str) -> Optional[int]:
    head = term.split()[0]
    try:
        return words.index(head)
    except ValueError:
        return None


news_analysis_service = NewsAnalysisService()
