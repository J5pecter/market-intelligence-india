"""Locate document sections and pull qualitative content out of them.

Two jobs:

* `map_sections` tells the figure extractor which part of the document a page
  belongs to, so a revenue line inside "Statement of Profit and Loss" scores
  higher than one inside "Industry Overview".
* `extract_commentary` and `extract_risk_factors` return **quotes with
  citations**, never paraphrases. A paraphrase of management guidance is how a
  research note quietly becomes a claim, so the pipeline does not make them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.documents.patterns import (COMMENTARY_CUES,
                                             compiled_sections)
from app.services.documents.text_extraction import ExtractedDocument

_SECTION_RES = compiled_sections()

# A heading is short, and is not a full sentence.
MAX_HEADING_CHARS = 90


@dataclass
class SectionSpan:
    name: str
    start_page: int
    end_page: int
    heading: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Quote:
    category: str
    text: str
    page: int
    section: Optional[str]
    matched_cue: str
    confidence: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def find_section_headings(document: ExtractedDocument) -> List[Tuple[int, str, str]]:
    """(page, section_name, heading_text) for every heading found."""
    hits: List[Tuple[int, str, str]] = []
    for page in document.pages:
        for line in page.lines:
            if len(line) > MAX_HEADING_CHARS:
                continue
            candidate = _strip_numbering(line)
            for pattern, name in _SECTION_RES:
                if pattern.match(candidate):
                    hits.append((page.number, name, line))
                    break
    return hits


def map_sections(document: ExtractedDocument) -> Tuple[Dict[int, str],
                                                       List[SectionSpan]]:
    """Assign every page to the most recent heading that preceded it."""
    headings = find_section_headings(document)
    if not headings:
        return {}, []

    spans: List[SectionSpan] = []
    for index, (page_number, name, heading) in enumerate(headings):
        end = (
            headings[index + 1][0] - 1 if index + 1 < len(headings)
            else document.page_count
        )
        spans.append(SectionSpan(name=name, start_page=page_number,
                                 end_page=max(end, page_number),
                                 heading=heading))

    page_map: Dict[int, str] = {}
    for span in spans:
        for page_number in range(span.start_page, span.end_page + 1):
            page_map[page_number] = span.name
    return page_map, spans


def _strip_numbering(line: str) -> str:
    """Drop 'SECTION IV - ', '4.2 ', 'A. ' and similar heading prefixes."""
    cleaned = re.sub(r"^\s*(?:section\s+[ivxlc]+\s*[-–:]?\s*)", "", line,
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[A-Z]|\([a-z]\))[.)]?\s+", "",
                     cleaned)
    return cleaned.strip().rstrip(":").strip()


# --------------------------------------------------------------------------
# Management commentary
# --------------------------------------------------------------------------


SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def extract_commentary(
    document: ExtractedDocument,
    sections: Optional[Dict[int, str]] = None,
    max_per_category: int = 6,
) -> List[Quote]:
    """Sentences carrying a forward-looking or qualitative cue, verbatim."""
    sections = sections or {}
    found: Dict[str, List[Quote]] = {}

    for page in document.pages:
        if page.is_empty:
            continue
        section = sections.get(page.number)
        for sentence in _sentences(page.text):
            lowered = sentence.lower()
            for category, cues in COMMENTARY_CUES.items():
                cue = next((c for c in cues if c in lowered), None)
                if cue is None:
                    continue
                confidence = _commentary_confidence(sentence, section, category)
                found.setdefault(category, []).append(Quote(
                    category=category,
                    text=sentence.strip()[:600],
                    page=page.number,
                    section=section,
                    matched_cue=cue,
                    confidence=confidence,
                    note=(
                        "Reproduced verbatim. The platform does not paraphrase "
                        "or score management commentary."
                    ),
                ))
                break

    out: List[Quote] = []
    for category, quotes in found.items():
        quotes.sort(key=lambda q: q.confidence, reverse=True)
        out.extend(quotes[:max_per_category])
    return out


def _sentences(text: str) -> List[str]:
    sentences: List[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if len(block) < 40:      # table rows and headings, not prose
            continue
        sentences.extend(s for s in SENTENCE.split(block) if len(s.strip()) >= 40)
    return sentences


def _commentary_confidence(sentence: str, section: Optional[str],
                           category: str) -> float:
    score = 0.5
    if section in ("MDA", "DIRECTORS_REPORT", "BUSINESS"):
        score += 0.2
    if 60 <= len(sentence) <= 320:
        score += 0.15
    if re.search(r"\d", sentence):    # a quantified statement is more useful
        score += 0.1
    if category == "GUIDANCE":
        score += 0.05
    return round(min(0.95, score), 3)


# --------------------------------------------------------------------------
# Risk factors
# --------------------------------------------------------------------------


RISK_CATEGORY_CUES: List[Tuple[str, List[str]]] = [
    ("CUSTOMER_CONCENTRATION",
     ["top five customers", "top 5 customers", "largest customer",
      "customer concentration", "dependent on a limited number of customers"]),
    ("SUPPLIER_CONCENTRATION",
     ["top five suppliers", "supplier concentration", "single supplier",
      "limited number of suppliers"]),
    ("GEOGRAPHIC",
     ["geographic concentration", "concentrated in", "single state",
      "operations are located in"]),
    ("LITIGATION",
     ["outstanding litigation", "legal proceedings", "criminal proceeding",
      "pending before", "civil suit"]),
    ("CONTINGENT_LIABILITY",
     ["contingent liabilit", "guarantees issued", "claims not acknowledged"]),
    ("RELATED_PARTY",
     ["related party transaction", "transactions with our promoter"]),
    ("REGULATORY",
     ["regulatory approval", "licences", "licenses", "statutory approval",
      "non-compliance", "penalty imposed"]),
    ("PROMOTER",
     ["our promoters", "promoter group", "pledged", "encumbered"]),
    ("LEVERAGE",
     ["indebtedness", "our borrowings", "debt service", "restrictive covenant",
      "repayment obligations"]),
]

# The "N% of revenue" shape that quantifies a concentration risk.
QUANTUM = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s+(?:our\s+)?(?:total\s+)?"
    r"(?:revenue|revenues|income|sales|turnover))",
    re.IGNORECASE,
)


def extract_risk_factors(
    document: ExtractedDocument,
    sections: Optional[Dict[int, str]] = None,
    max_factors: int = 25,
) -> List[Dict[str, Any]]:
    """Risk-factor paragraphs, categorised, with any quantum they state.

    Severity is derived only from what the text itself says - a quantified
    concentration is HIGH, an unquantified mention is MEDIUM. The pipeline does
    not judge how serious a risk 'really' is.
    """
    sections = sections or {}
    risk_pages = [
        page for page in document.pages
        if sections.get(page.number) == "RISK_FACTORS" and not page.is_empty
    ]
    # Offer documents put risks in a named section; annual reports often do not.
    if not risk_pages:
        risk_pages = [p for p in document.pages if not p.is_empty]

    factors: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for page in risk_pages:
        for paragraph in _paragraphs(page.text):
            lowered = paragraph.lower()
            category = next(
                (name for name, cues in RISK_CATEGORY_CUES
                 if any(cue in lowered for cue in cues)),
                None,
            )
            if category is None:
                continue

            fingerprint = lowered[:110]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            quantum_match = QUANTUM.search(paragraph)
            quantum = float(quantum_match.group(1)) if quantum_match else None

            factors.append({
                "category": category,
                "description": paragraph.strip()[:900],
                "severity": "HIGH" if quantum and quantum >= 25 else (
                    "HIGH" if quantum else "MEDIUM"
                ),
                "quantum": quantum,
                "quantum_unit": "%" if quantum is not None else None,
                "page": page.number,
                "quote": paragraph.strip()[:400],
                "confidence": 0.8 if quantum is not None else 0.6,
                "severity_basis": (
                    f"Quantified at {quantum}% of revenue in the document text."
                    if quantum is not None else
                    "Category matched but no quantum stated in the text; "
                    "severity defaults to MEDIUM pending review."
                ),
            })
            if len(factors) >= max_factors:
                return factors
    return factors


def _paragraphs(text: str) -> List[str]:
    blocks: List[str] = []
    current: List[str] = []
    for line in text.split("\n"):
        if not line.strip():
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        current.append(line.strip())
    if current:
        blocks.append(" ".join(current))
    return [b for b in blocks if 80 <= len(b) <= 2000]


# --------------------------------------------------------------------------
# Use of proceeds
# --------------------------------------------------------------------------


PROCEEDS_CUES = [
    "repayment", "prepayment", "working capital", "general corporate purposes",
    "capital expenditure", "funding", "acquisition", "investment in",
    "issue related expenses", "offer related expenses",
]


def extract_use_of_proceeds(
    document: ExtractedDocument,
    sections: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
    """Line items from the 'Objects of the Issue' section."""
    sections = sections or {}
    pages = [
        page for page in document.pages
        if sections.get(page.number) == "USE_OF_PROCEEDS" and not page.is_empty
    ]
    if not pages:
        return []

    from app.services.documents.figures import numbers_in

    items: List[Dict[str, Any]] = []
    for page in pages:
        for line in page.lines:
            lowered = line.lower()
            cue = next((c for c in PROCEEDS_CUES if c in lowered), None)
            if cue is None or len(line) > 300:
                continue
            values = numbers_in(line)
            items.append({
                "purpose": line.strip()[:300],
                "amount": values[0][0] if values else None,
                "matched_cue": cue,
                "page": page.number,
                "note": "Amounts are as printed; the unit is whatever the "
                        "section declared.",
            })
    return items[:15]
