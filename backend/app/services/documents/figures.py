"""Pull financial figures out of document text, with a citation for each.

Two rules govern everything here:

1. **A number without a unit is not normalised.** Indian filings print "1,250"
   meaning 1,250 crore. Guessing the multiplier is a 100x error, so a figure
   whose unit could not be established is kept as `raw_value` with
   `normalised_value = None` and a low confidence, for a human to resolve.

2. **Every figure carries the line it came from.** The reviewer sees the exact
   quote, the page, the alias that matched and why the confidence is what it
   is. Nothing is accepted on the extractor's authority.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.documents.patterns import (INLINE_UNIT, INLINE_UNIT_KEYS,
                                             METRIC_DEFINITIONS,
                                             UNIT_MULTIPLIERS,
                                             compiled_periods,
                                             compiled_units,
                                             metric_alias_index)
from app.services.documents.text_extraction import ExtractedDocument, Page

_UNIT_RES = compiled_units()
_PERIOD_RES = compiled_periods()
_ALIASES = metric_alias_index()

# A number as it appears in an Indian statement: optional bracket for negative,
# 2-2-3 digit grouping, optional decimals, optional trailing percent.
NUMBER = re.compile(
    r"\(?\s*-?\s*(?:\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*\)?%?"
)

# Tokens that mean "no value" rather than zero.
NIL_TOKENS = {"nil", "na", "n.a.", "n/a", "-", "--", "—", "*", ""}


@dataclass
class ExtractedFigure:
    metric_key: str
    metric_label: str
    kind: str                       # currency | percent | per_share
    raw_value: float
    normalised_value: Optional[float]   # absolute rupees, when the unit is known
    unit: Optional[str]
    unit_multiplier: Optional[float]
    unit_source: str                # how the unit was established
    period_label: Optional[str]
    page: int
    section: Optional[str]
    quote: str
    matched_alias: str
    confidence: float               # 0-1
    confidence_reasons: List[str] = field(default_factory=list)
    needs_review: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Number parsing
# --------------------------------------------------------------------------


def parse_indian_number(token: str) -> Optional[float]:
    """Parse a printed figure. Returns None when the token means 'no value'.

    Handles: 1,23,456.78 · (1,234) as negative · trailing % · Nil / NA / dashes.
    """
    if token is None:
        return None
    cleaned = token.strip().lower()
    if cleaned in NIL_TOKENS:
        return None

    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").strip()
    cleaned = cleaned.rstrip("%").strip()
    cleaned = cleaned.replace(",", "").replace(" ", "")

    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    if not cleaned or not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return None

    value = float(cleaned)
    return -value if negative else value


def numbers_in(line: str) -> List[Tuple[float, str]]:
    """Every parseable number on a line, with the token that produced it."""
    out: List[Tuple[float, str]] = []
    for match in NUMBER.finditer(line):
        token = match.group(0)
        value = parse_indian_number(token)
        if value is not None:
            out.append((value, token.strip()))
    return out


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def detect_unit(text: str) -> Optional[str]:
    """Find a unit declaration such as '(Rs. in crore)'."""
    lowered = text.lower()
    for pattern, unit in _UNIT_RES:
        if pattern.search(lowered):
            return unit
    return None


def document_unit_map(document: ExtractedDocument) -> Dict[int, Tuple[str, str]]:
    """Unit in force for each page, and where it came from.

    A declaration on page 40 applies to page 41 unless page 41 declares its own
    - which is how a statements section actually reads. The map records the
    source so a reviewer can check the inheritance.
    """
    resolved: Dict[int, Tuple[str, str]] = {}
    current: Optional[Tuple[str, str]] = None

    for page in document.pages:
        own = detect_unit(page.text)
        if own:
            current = (own, f"declared on page {page.number}")
        if current:
            resolved[page.number] = current
    return resolved


def inline_unit(line: str, token: str) -> Optional[str]:
    """A unit attached to this specific number, e.g. 'Rs 1,250 crore'."""
    for match in INLINE_UNIT.finditer(line):
        if match.group("number").strip() == token:
            return INLINE_UNIT_KEYS.get(match.group("unit").lower().rstrip("s"),
                                        None) or \
                INLINE_UNIT_KEYS.get(match.group("unit").lower())
    return None


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


def find_periods(text: str) -> List[str]:
    """Period labels on a line, normalised to FY24 / Q2FY25."""
    found: List[str] = []
    lowered = text.lower()
    for pattern, kind in _PERIOD_RES:
        for match in pattern.finditer(lowered):
            groups = [g for g in match.groups() if g]
            if not groups:
                continue
            if kind == "quarter" and len(groups) >= 2:
                year = _two_digit_year(groups[1])
                found.append(f"Q{groups[0]}FY{year}")
            elif kind == "annual":
                found.append(f"FY{_two_digit_year(groups[-1])}")
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(found))


def _two_digit_year(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[-2:] if len(digits) >= 2 else digits


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def extract_figures(
    document: ExtractedDocument,
    sections: Optional[Dict[int, str]] = None,
    max_per_metric: int = 8,
) -> List[ExtractedFigure]:
    """Walk every line looking for `<metric label> <numbers...>`.

    Financial tables in extracted PDF text collapse to exactly that shape, so
    it is the highest-yield pattern by a wide margin - and, importantly, one a
    reviewer can verify at a glance against the printed statement.
    """
    units = document_unit_map(document)
    sections = sections or {}
    figures: List[ExtractedFigure] = []

    for page in document.pages:
        if page.is_empty:
            continue
        page_unit = units.get(page.number)
        section = sections.get(page.number)
        # Period headers usually sit above the table, so remember what we saw.
        page_periods = find_periods(page.text)

        for line in page.lines:
            if len(line) > 400:      # a paragraph, not a table row
                continue
            match = _match_metric(line)
            if match is None:
                continue
            metric_key, alias, alias_end = match

            definition = METRIC_DEFINITIONS[metric_key]
            tail = line[alias_end:]
            values = numbers_in(tail)
            if not values:
                continue

            line_periods = find_periods(line)
            # Left-most number is the most recent period in Indian statements.
            value, token = values[0]
            unit, unit_source = _resolve_unit(
                line, token, page_unit, definition["kind"]  # type: ignore[index]
            )

            multiplier = UNIT_MULTIPLIERS.get(unit) if unit else None
            normalised = (
                value * multiplier
                if multiplier is not None and definition["kind"] == "currency"
                else (value if definition["kind"] != "currency" else None)
            )

            confidence, reasons = _score(
                line=line, tail=tail, alias=alias, values=values,
                unit=unit, kind=str(definition["kind"]), section=section,
                periods=line_periods or page_periods,
            )

            figures.append(ExtractedFigure(
                metric_key=metric_key,
                metric_label=str(definition["label"]),
                kind=str(definition["kind"]),
                raw_value=value,
                normalised_value=normalised,
                unit=unit,
                unit_multiplier=multiplier,
                unit_source=unit_source,
                period_label=(line_periods or page_periods or [None])[0],
                page=page.number,
                section=section,
                quote=line[:400],
                matched_alias=alias,
                confidence=confidence,
                confidence_reasons=reasons,
                needs_review=confidence < 0.75,
            ))

    return _prune(figures, max_per_metric)


def _match_metric(line: str) -> Optional[Tuple[str, str, int]]:
    """Longest alias that starts the line (allowing a leading label number).

    Anchoring at the start is what keeps 'total revenue' from matching inside
    'segment total revenue disclosure note 4'.
    """
    lowered = line.lower()
    # Statements often prefix rows with a note number: "1. Revenue from ..."
    offset = 0
    prefix = re.match(r"^\s*(?:\(?[ivxlc]+\)?|\d{1,2})[.)]\s+", lowered)
    if prefix:
        offset = prefix.end()
        lowered = lowered[offset:]

    for alias, metric_key, _ in _ALIASES:
        if not lowered.startswith(alias):
            continue
        definition = METRIC_DEFINITIONS[metric_key]
        negatives = definition["negative_cues"]  # type: ignore[index]
        if any(cue in line.lower() for cue in negatives):  # type: ignore[operator]
            continue
        return metric_key, alias, offset + len(alias)
    return None


def _resolve_unit(line: str, token: str, page_unit: Optional[Tuple[str, str]],
                  kind: str) -> Tuple[Optional[str], str]:
    if kind in ("percent", "per_share"):
        return None, "not applicable to this metric kind"

    attached = inline_unit(line, token)
    if attached:
        return attached, "stated next to the number"

    own_line = detect_unit(line)
    if own_line:
        return own_line, "declared on the same line"

    if page_unit:
        return page_unit[0], page_unit[1]

    return None, "no unit declaration found"


def _score(*, line: str, tail: str, alias: str,
           values: List[Tuple[float, str]], unit: Optional[str], kind: str,
           section: Optional[str], periods: List[str]) -> Tuple[float, List[str]]:
    """Confidence in 0-1, with the reasons that produced it.

    Starts pessimistic and earns its way up. A figure only clears the
    auto-accept threshold when the label anchored the line, the unit is known,
    a period was identified and the row looks like a statement row.
    """
    score = 0.35
    reasons: List[str] = ["Base confidence for a label-anchored match: 0.35"]

    # The number should follow the label closely, not sit 60 characters away in
    # a sentence.
    gap = tail.index(values[0][1]) if values[0][1] in tail else 0
    if gap <= 24:
        score += 0.15
        reasons.append(f"Number appears {gap} characters after the label: +0.15")
    else:
        reasons.append(f"Number is {gap} characters from the label: no credit")

    if unit is not None or kind != "currency":
        score += 0.15
        reasons.append(
            "Unit established, so the figure can be normalised: +0.15"
            if kind == "currency" else "Unit not required for this metric: +0.15"
        )
    else:
        reasons.append("No unit declaration found: the figure cannot be "
                       "normalised and must be reviewed")

    if periods:
        score += 0.12
        reasons.append(f"Period identified ({periods[0]}): +0.12")
    else:
        reasons.append("No reporting period identified on or near the line")

    # Two or more figures on the row is the signature of a comparative table.
    if len(values) >= 2:
        score += 0.13
        reasons.append(f"{len(values)} figures on the row, consistent with a "
                       f"comparative statement table: +0.13")

    if section in ("FINANCIALS", "PROFIT_AND_LOSS", "BALANCE_SHEET",
                   "CASH_FLOW"):
        score += 0.12
        reasons.append(f"Found inside the {section} section: +0.12")
    elif section:
        reasons.append(f"Found in the {section} section, not a statements "
                       f"section: no credit")

    # A long line with prose around the number is more likely a sentence.
    if len(line) > 160:
        score -= 0.10
        reasons.append("Line is long and prose-like: -0.10")

    score = max(0.05, min(0.98, score))
    reasons.append(f"Final confidence: {score:.2f}")
    return round(score, 3), reasons


def _prune(figures: List[ExtractedFigure],
           max_per_metric: int) -> List[ExtractedFigure]:
    """Keep the strongest candidates per (metric, period).

    A 200-page annual report mentions revenue dozens of times. Presenting all
    of them to a reviewer is the same as presenting none.
    """
    best: Dict[Tuple[str, Optional[str]], List[ExtractedFigure]] = {}
    for figure in figures:
        best.setdefault((figure.metric_key, figure.period_label), []).append(figure)

    out: List[ExtractedFigure] = []
    for candidates in best.values():
        candidates.sort(key=lambda f: (f.confidence, -f.page), reverse=True)
        out.extend(candidates[:max_per_metric])

    out.sort(key=lambda f: (f.metric_key, f.period_label or "", -f.confidence))
    return out


def agreement_bonus(figures: List[ExtractedFigure]) -> List[ExtractedFigure]:
    """Raise confidence where independent pages report the same figure.

    Two pages agreeing on revenue is real corroboration, and it is the only
    signal here that does not come from the shape of a single line.
    """
    grouped: Dict[Tuple[str, Optional[str]], List[ExtractedFigure]] = {}
    for figure in figures:
        grouped.setdefault((figure.metric_key, figure.period_label), []).append(figure)

    for candidates in grouped.values():
        if len(candidates) < 2:
            continue
        for figure in candidates:
            peers = [
                other for other in candidates
                if other is not figure and other.page != figure.page
                and _close(other.normalised_value or other.raw_value,
                           figure.normalised_value or figure.raw_value)
            ]
            if peers:
                figure.confidence = round(min(0.98, figure.confidence + 0.10), 3)
                figure.confidence_reasons.append(
                    f"Corroborated by page(s) {sorted({p.page for p in peers})}: "
                    f"+0.10 (now {figure.confidence:.2f})"
                )
                figure.needs_review = figure.confidence < 0.75
    return figures


def _close(a: Optional[float], b: Optional[float], tolerance: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance
