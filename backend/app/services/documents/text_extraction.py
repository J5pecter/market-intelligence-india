"""Turn a document into pages of text.

PDF, HTML and plain text. The output is deliberately page-aware: a citation
that cannot name a page is not much of a citation, and a reviewer needs to be
able to open the source and find the number.

Scanned PDFs are the common failure. This module does not OCR; it detects that
a page yielded no extractable text and reports it, so the pipeline can tell a
reviewer "this document is a scan" instead of silently extracting nothing.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader
except Exception:  # noqa: BLE001
    PdfReader = None

try:  # pragma: no cover
    from bs4 import BeautifulSoup
except Exception:  # noqa: BLE001
    BeautifulSoup = None


# A page with fewer than this many extractable characters is treated as
# image-only: a scan, a cover, or a chart page.
MIN_TEXT_CHARS = 40


@dataclass
class Page:
    number: int
    text: str
    lines: List[str] = field(default_factory=list)
    is_empty: bool = False

    def __post_init__(self) -> None:
        if not self.lines:
            self.lines = [
                line.strip() for line in self.text.splitlines() if line.strip()
            ]
        self.is_empty = len(self.text.strip()) < MIN_TEXT_CHARS


@dataclass
class ExtractedDocument:
    pages: List[Page]
    page_count: int
    method: str
    char_count: int
    empty_pages: List[int]
    warnings: List[str] = field(default_factory=list)
    checksum: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.char_count >= MIN_TEXT_CHARS

    @property
    def looks_like_a_scan(self) -> bool:
        """More than 70% of pages yielded nothing readable."""
        if not self.page_count:
            return False
        return len(self.empty_pages) / self.page_count > 0.7

    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)


class ExtractionError(RuntimeError):
    """Raised when a document cannot be read at all."""


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def extract_from_path(path: str | Path) -> ExtractedDocument:
    file_path = Path(path)
    if not file_path.exists():
        raise ExtractionError(f"file not found: {file_path}")

    data = file_path.read_bytes()
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        document = extract_from_pdf(data)
    elif suffix in (".html", ".htm", ".xhtml"):
        document = extract_from_html(data.decode("utf-8", errors="replace"))
    elif suffix in (".txt", ".md", ".csv"):
        document = extract_from_text(data.decode("utf-8", errors="replace"))
    else:
        # Sniff: PDFs always start with %PDF-
        if data[:5] == b"%PDF-":
            document = extract_from_pdf(data)
        else:
            document = extract_from_text(data.decode("utf-8", errors="replace"))

    document.checksum = hashlib.sha256(data).hexdigest()[:32]
    return document


def extract_from_bytes(data: bytes, filename: str = "") -> ExtractedDocument:
    if data[:5] == b"%PDF-" or filename.lower().endswith(".pdf"):
        document = extract_from_pdf(data)
    elif filename.lower().endswith((".html", ".htm")):
        document = extract_from_html(data.decode("utf-8", errors="replace"))
    else:
        document = extract_from_text(data.decode("utf-8", errors="replace"))
    document.checksum = hashlib.sha256(data).hexdigest()[:32]
    return document


# --------------------------------------------------------------------------
# Format handlers
# --------------------------------------------------------------------------


def extract_from_pdf(data: bytes) -> ExtractedDocument:
    if PdfReader is None:
        raise ExtractionError(
            "pypdf is not installed, so PDF documents cannot be read. "
            "Install it with `pip install pypdf`."
        )
    import io

    warnings: List[str] = []
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"could not open the PDF: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        # An empty password unlocks many filings; if it does not, say so.
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise ExtractionError(
                "the PDF is encrypted and could not be opened with an empty "
                "password"
            ) from None
        warnings.append("The PDF was encrypted and was opened with an empty "
                        "password.")

    pages: List[Page] = []
    empty: List[int] = []
    for index, raw_page in enumerate(reader.pages, start=1):
        try:
            text = raw_page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the run
            text = ""
            warnings.append(f"Page {index} could not be read: {exc}")
        page = Page(number=index, text=_normalise(text))
        if page.is_empty:
            empty.append(index)
        pages.append(page)

    document = ExtractedDocument(
        pages=pages,
        page_count=len(pages),
        method="pypdf",
        char_count=sum(len(p.text) for p in pages),
        empty_pages=empty,
        warnings=warnings,
    )
    if document.looks_like_a_scan:
        document.warnings.append(
            f"{len(empty)} of {len(pages)} pages yielded no extractable text. "
            "This document is probably a scan; it needs OCR, which this "
            "pipeline does not perform. No figures will be extracted from "
            "those pages."
        )
    return document


def extract_from_html(html: str) -> ExtractedDocument:
    if BeautifulSoup is None:
        raise ExtractionError("beautifulsoup4 is not installed")

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Tables are where the numbers live; flatten each row onto one line so the
    # label and its figures stay together for the parser.
    for table in soup.find_all("table"):
        rows = []
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if any(cells):
                rows.append("  ".join(cells))
        table.replace_with("\n".join(rows) + "\n")

    text = _normalise(soup.get_text("\n"))
    page = Page(number=1, text=text)
    return ExtractedDocument(
        pages=[page], page_count=1, method="beautifulsoup",
        char_count=len(text), empty_pages=[] if not page.is_empty else [1],
        warnings=[
            "HTML has no page numbering, so every citation from this document "
            "refers to page 1."
        ],
    )


def extract_from_text(text: str) -> ExtractedDocument:
    normalised = _normalise(text)
    # Respect form feeds if the source used them as page breaks.
    chunks = normalised.split("\f") if "\f" in normalised else [normalised]
    pages = [Page(number=i, text=chunk) for i, chunk in enumerate(chunks, start=1)]
    return ExtractedDocument(
        pages=pages, page_count=len(pages), method="plaintext",
        char_count=len(normalised),
        empty_pages=[p.number for p in pages if p.is_empty],
    )


# --------------------------------------------------------------------------


_WHITESPACE = re.compile(r"[ \t   ]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def _normalise(text: str) -> str:
    """Collapse runs of whitespace without destroying line structure.

    Line structure matters: the figure parser relies on a label and its numbers
    sharing a line, which is how tabular PDF text comes out.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Unicode dashes and quotes confuse the number and cue matchers.
    text = (text.replace("–", "-").replace("—", "-")
                .replace("‘", "'").replace("’", "'")
                .replace("“", '"').replace("”", '"'))
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()
