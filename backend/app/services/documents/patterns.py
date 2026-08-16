"""The domain vocabulary the extractor matches against.

Kept as data in one file, deliberately: an operator adding a synonym their
filings use should not have to touch parsing logic, and a reviewer should be
able to read exactly what the machine was looking for.

Everything here is Indian-filing specific. Annual reports and offer documents
quote in lakhs, crore or millions, put negatives in brackets, and group digits
in the 2-2-3 pattern (1,23,456.78) - all of which break naive parsers.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# --------------------------------------------------------------------------
# Unit declarations
#
# Indian statements carry a line like "(Rs. in lakhs)" or "All figures in
# INR crore unless otherwise stated". The multiplier converts the printed
# figure to absolute rupees. Getting this wrong is a 100x error, so the
# extractor refuses to normalise when it cannot find a declaration.
# --------------------------------------------------------------------------

UNIT_MULTIPLIERS: Dict[str, float] = {
    "absolute": 1.0,
    "thousand": 1e3,
    "lakh": 1e5,
    "million": 1e6,
    "crore": 1e7,
    "billion": 1e9,
}

UNIT_PATTERNS: List[Tuple[str, str]] = [
    # (regex, unit key) - checked in order, first match wins
    (r"\bin\s+(?:rs\.?|inr|₹)?\s*crores?\b", "crore"),
    (r"\b(?:rs\.?|inr|₹)\s*(?:in\s+)?crores?\b", "crore"),
    (r"\bin\s+(?:rs\.?|inr|₹)?\s*lakhs?\b", "lakh"),
    (r"\b(?:rs\.?|inr|₹)\s*(?:in\s+)?lakhs?\b", "lakh"),
    (r"\bin\s+(?:rs\.?|inr|₹)?\s*millions?\b", "million"),
    (r"\b(?:rs\.?|inr|₹)\s*(?:in\s+)?millions?\b", "million"),
    (r"\bin\s+(?:rs\.?|inr|₹)?\s*billions?\b", "billion"),
    (r"\bin\s+(?:rs\.?|inr|₹)?\s*thousands?\b", "thousand"),
    (r"\bfigures?\s+in\s+crores?\b", "crore"),
    (r"\bfigures?\s+in\s+lakhs?\b", "lakh"),
    (r"\bamounts?\s+in\s+(?:rs\.?\s*)?crores?\b", "crore"),
    (r"\bamounts?\s+in\s+(?:rs\.?\s*)?lakhs?\b", "lakh"),
]

# A unit word attached to a single number, e.g. "Rs 1,250 crore".
INLINE_UNIT = re.compile(
    r"(?P<number>\(?-?[\d,]+(?:\.\d+)?\)?)\s*"
    r"(?P<unit>crores?|lakhs?|millions?|billions?|cr\b|mn\b|bn\b)",
    re.IGNORECASE,
)

INLINE_UNIT_KEYS = {
    "cr": "crore", "crore": "crore", "crores": "crore",
    "lakh": "lakh", "lakhs": "lakh",
    "mn": "million", "million": "million", "millions": "million",
    "bn": "billion", "billion": "billion", "billions": "billion",
}


# --------------------------------------------------------------------------
# Metric vocabulary
#
# `aliases` are matched case-insensitively as whole phrases. `negative_cues`
# are phrases that, when present on the same line, mean the line is NOT the
# metric (e.g. "revenue from operations" is revenue, but "deferred revenue" on
# a balance sheet is not).
# --------------------------------------------------------------------------

METRIC_DEFINITIONS: Dict[str, Dict[str, object]] = {
    "revenue": {
        "label": "Revenue",
        "aliases": [
            "revenue from operations", "total revenue", "total income",
            "revenue from contracts with customers", "net sales",
            "income from operations", "turnover", "gross revenue",
            "revenue (net)", "sales",
        ],
        "negative_cues": ["deferred revenue", "unearned revenue",
                          "revenue per share", "segment revenue"],
        "kind": "currency",
    },
    "other_income": {
        "label": "Other income",
        "aliases": ["other income", "other operating income"],
        "negative_cues": [],
        "kind": "currency",
    },
    "ebitda": {
        "label": "EBITDA",
        "aliases": ["ebitda", "operating profit", "operating ebitda",
                    "earnings before interest, tax, depreciation"],
        "negative_cues": ["ebitda margin", "ebitda %"],
        "kind": "currency",
    },
    "ebitda_margin": {
        "label": "EBITDA margin",
        "aliases": ["ebitda margin", "operating margin", "ebitda %"],
        "negative_cues": [],
        "kind": "percent",
    },
    "ebit": {
        "label": "EBIT",
        "aliases": ["ebit", "earnings before interest and tax",
                    "profit before interest and tax"],
        "negative_cues": ["ebitda"],
        "kind": "currency",
    },
    "interest": {
        "label": "Finance cost",
        "aliases": ["finance cost", "finance costs", "interest expense",
                    "interest cost"],
        "negative_cues": ["interest income", "interest coverage"],
        "kind": "currency",
    },
    "depreciation": {
        "label": "Depreciation and amortisation",
        "aliases": ["depreciation and amortisation",
                    "depreciation and amortization", "depreciation expense",
                    "depreciation, amortisation"],
        "negative_cues": [],
        "kind": "currency",
    },
    "pbt": {
        "label": "Profit before tax",
        "aliases": ["profit before tax", "pbt", "profit/(loss) before tax",
                    "earnings before tax"],
        "negative_cues": ["profit before tax margin"],
        "kind": "currency",
    },
    "tax": {
        "label": "Tax expense",
        "aliases": ["tax expense", "total tax expense", "current tax",
                    "provision for tax", "income tax expense"],
        "negative_cues": ["deferred tax asset", "deferred tax liability"],
        "kind": "currency",
    },
    "pat": {
        "label": "Profit after tax",
        "aliases": ["profit after tax", "pat", "net profit", "net income",
                    "profit for the year", "profit for the period",
                    "profit/(loss) for the year", "profit/(loss) after tax",
                    "net profit after tax",
                    "profit attributable to owners"],
        "negative_cues": ["pat margin", "net profit margin"],
        "kind": "currency",
    },
    "eps": {
        "label": "Earnings per share",
        "aliases": ["earnings per share", "basic eps", "eps (basic)",
                    "basic earnings per share", "eps"],
        "negative_cues": ["diluted"],
        "kind": "per_share",
    },
    "total_assets": {
        "label": "Total assets",
        "aliases": ["total assets"],
        "negative_cues": ["return on total assets"],
        "kind": "currency",
    },
    "total_debt": {
        "label": "Total debt",
        "aliases": ["total debt", "total borrowings", "gross debt",
                    "borrowings (current and non-current)"],
        "negative_cues": ["debt to equity", "debt/equity", "net debt to"],
        "kind": "currency",
    },
    "cash_and_equivalents": {
        "label": "Cash and equivalents",
        "aliases": ["cash and cash equivalents", "cash and bank balances",
                    "cash & cash equivalents"],
        "negative_cues": [],
        "kind": "currency",
    },
    "net_worth": {
        "label": "Net worth",
        "aliases": ["net worth", "total equity", "shareholders' funds",
                    "shareholders funds", "total shareholders' equity",
                    "equity attributable to owners"],
        "negative_cues": ["return on net worth"],
        "kind": "currency",
    },
    "working_capital": {
        "label": "Working capital",
        "aliases": ["working capital", "net working capital"],
        "negative_cues": ["working capital days"],
        "kind": "currency",
    },
    "operating_cash_flow": {
        "label": "Operating cash flow",
        "aliases": ["net cash from operating activities",
                    "cash flow from operations", "cash generated from operations",
                    "net cash generated from operating activities",
                    "operating cash flow"],
        "negative_cues": [],
        "kind": "currency",
    },
    "investing_cash_flow": {
        "label": "Investing cash flow",
        "aliases": ["net cash from investing activities",
                    "net cash used in investing activities"],
        "negative_cues": [],
        "kind": "currency",
    },
    "financing_cash_flow": {
        "label": "Financing cash flow",
        "aliases": ["net cash from financing activities",
                    "net cash used in financing activities"],
        "negative_cues": [],
        "kind": "currency",
    },
    "capex": {
        "label": "Capital expenditure",
        "aliases": ["capital expenditure", "capex",
                    "purchase of property, plant and equipment",
                    "additions to property, plant and equipment"],
        "negative_cues": ["capex plan", "capex guidance"],
        "kind": "currency",
    },
    "roe": {
        "label": "Return on equity",
        "aliases": ["return on equity", "roe", "return on net worth", "ronw"],
        "negative_cues": [],
        "kind": "percent",
    },
    "roce": {
        "label": "Return on capital employed",
        "aliases": ["return on capital employed", "roce"],
        "negative_cues": [],
        "kind": "percent",
    },
    "net_margin": {
        "label": "Net margin",
        "aliases": ["net profit margin", "pat margin", "net margin"],
        "negative_cues": [],
        "kind": "percent",
    },
    "order_book": {
        "label": "Order book",
        "aliases": ["order book", "order backlog", "unexecuted order book",
                    "outstanding order book"],
        "negative_cues": [],
        "kind": "currency",
    },
    "promoter_holding": {
        "label": "Promoter holding",
        "aliases": ["promoter holding", "promoter and promoter group",
                    "promoters' shareholding", "promoter shareholding"],
        "negative_cues": ["pledge"],
        "kind": "percent",
    },
    "promoter_pledge": {
        "label": "Promoter pledge",
        "aliases": ["shares pledged", "pledged shares", "encumbered shares"],
        "negative_cues": [],
        "kind": "percent",
    },
}


# --------------------------------------------------------------------------
# Document sections
#
# Matched against a line on its own (a heading), not anywhere in the text.
# --------------------------------------------------------------------------

SECTION_PATTERNS: List[Tuple[str, str]] = [
    (r"^risk\s+factors?$", "RISK_FACTORS"),
    (r"^internal\s+risk\s+factors?$", "RISK_FACTORS"),
    (r"^objects?\s+of\s+the\s+(?:issue|offer)$", "USE_OF_PROCEEDS"),
    (r"^use\s+of\s+(?:net\s+)?proceeds$", "USE_OF_PROCEEDS"),
    (r"^management(?:'s)?\s+discussion\s+and\s+analysis", "MDA"),
    (r"^(?:directors|board)(?:'|’)?s?\s+report$", "DIRECTORS_REPORT"),
    (r"^financial\s+statements?$", "FINANCIALS"),
    (r"^(?:standalone|consolidated)\s+financial\s+(?:statements|results)",
     "FINANCIALS"),
    (r"^statement\s+of\s+profit\s+and\s+loss$", "PROFIT_AND_LOSS"),
    (r"^balance\s+sheet$", "BALANCE_SHEET"),
    (r"^cash\s+flow\s+statement$", "CASH_FLOW"),
    (r"^statement\s+of\s+cash\s+flows?$", "CASH_FLOW"),
    (r"^our\s+business$", "BUSINESS"),
    (r"^business\s+overview$", "BUSINESS"),
    (r"^industry\s+overview$", "INDUSTRY"),
    (r"^capital\s+structure$", "CAPITAL_STRUCTURE"),
    (r"^related\s+party\s+transactions?$", "RELATED_PARTY"),
    (r"^contingent\s+liabilit(?:y|ies)", "CONTINGENT_LIABILITIES"),
    (r"^(?:outstanding\s+)?litigations?", "LITIGATION"),
    (r"^legal\s+proceedings?$", "LITIGATION"),
    (r"^corporate\s+governance", "GOVERNANCE"),
    (r"^shareholding\s+pattern$", "SHAREHOLDING"),
    (r"^basis\s+for\s+(?:the\s+)?(?:issue|offer)\s+price$", "VALUATION"),
    (r"^our\s+promoters?", "PROMOTERS"),
    (r"^segment\s+(?:wise\s+)?(?:reporting|information|results)", "SEGMENTS"),
]


# --------------------------------------------------------------------------
# Management commentary
#
# Cue phrases that mark a sentence as forward-looking or qualitative. These are
# surfaced as *quotes with citations*, never paraphrased or scored, because a
# paraphrase of guidance is how a research note becomes a claim.
# --------------------------------------------------------------------------

COMMENTARY_CUES: Dict[str, List[str]] = {
    "GUIDANCE": [
        "we expect", "we anticipate", "guidance", "we are targeting",
        "we aim to", "outlook for", "we project", "expected to grow",
        "we remain confident", "on track to", "we forecast",
    ],
    "CAPEX": [
        "capital expenditure", "capex", "we plan to invest", "investment of",
        "greenfield", "brownfield", "new plant", "capacity addition",
    ],
    "DEMAND": [
        "demand environment", "demand remained", "demand has", "order inflow",
        "order intake", "volume growth", "realisation", "realization",
        "pricing environment",
    ],
    "EXPANSION": [
        "expansion", "we will expand", "new market", "entered into",
        "commissioned", "capacity utilisation", "capacity utilization",
    ],
    "MARGIN": [
        "margin expansion", "margin contraction", "cost pressure",
        "input cost", "raw material cost", "operating leverage",
    ],
    "RISK": [
        "headwind", "challenging", "uncertainty", "slowdown", "adverse impact",
        "may adversely affect", "we may not be able to",
    ],
    "SEGMENT": [
        "segment reported", "segment revenue", "segment performance",
        "business vertical",
    ],
}


# --------------------------------------------------------------------------
# Period labels: FY24, Q2FY25, "year ended March 31, 2025"
# --------------------------------------------------------------------------

PERIOD_PATTERNS: List[Tuple[str, str]] = [
    (r"\bq([1-4])\s*fy\s*(\d{2,4})\b", "quarter"),
    (r"\bfy\s*(\d{2,4})\b", "annual"),
    (r"\b(?:year|period)\s+ended\s+(?:on\s+)?(?:march|mar)\.?\s*31,?\s*(\d{4})\b",
     "annual"),
    (r"\b(?:quarter)\s+ended\s+(?:on\s+)?\w+\.?\s*\d{1,2},?\s*(\d{4})\b",
     "quarter"),
    (r"\b(\d{4})\s*[-–]\s*(\d{2,4})\b", "annual"),
]


def compiled_units() -> List[Tuple[re.Pattern[str], str]]:
    return [(re.compile(pattern, re.IGNORECASE), unit)
            for pattern, unit in UNIT_PATTERNS]


def compiled_sections() -> List[Tuple[re.Pattern[str], str]]:
    return [(re.compile(pattern, re.IGNORECASE), name)
            for pattern, name in SECTION_PATTERNS]


def compiled_periods() -> List[Tuple[re.Pattern[str], str]]:
    return [(re.compile(pattern, re.IGNORECASE), kind)
            for pattern, kind in PERIOD_PATTERNS]


def metric_alias_index() -> List[Tuple[str, str, int]]:
    """(alias, metric_key, alias_length) sorted longest-first.

    Longest-first matters: "profit before tax" must win over "profit" so a PBT
    line is never recorded as PAT.
    """
    index: List[Tuple[str, str, int]] = []
    for key, definition in METRIC_DEFINITIONS.items():
        for alias in definition["aliases"]:  # type: ignore[index]
            index.append((alias.lower(), key, len(alias)))
    index.sort(key=lambda item: item[2], reverse=True)
    return index
