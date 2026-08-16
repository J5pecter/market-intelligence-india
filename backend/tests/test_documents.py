"""Document extraction pipeline.

The behaviours that matter are the conservative ones: Indian number formats
parse correctly, an unknown unit means the figure is *not* normalised, and
nothing reaches the fundamentals tables without a human approving it.
"""

import io
from datetime import date

import pytest

from app.services.documents import figures as fig
from app.services.documents import sectioning, text_extraction
from app.services.documents.pipeline import _period_end
from app.services.documents.text_extraction import ExtractedDocument, Page


# --------------------------------------------------------------------------
# Number parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [
        ("1,23,456.78", 123456.78),      # Indian 2-2-3 grouping
        ("1,234,567", 1234567.0),        # western grouping still works
        ("(1,234)", -1234.0),            # brackets are negative
        ("-1,234", -1234.0),
        ("12.5%", 12.5),
        ("1250", 1250.0),
        ("0", 0.0),
        ("Nil", None),
        ("NA", None),
        ("-", None),
        ("—", None),
        ("", None),
        ("abc", None),
    ],
)
def test_indian_number_parsing(token, expected):
    assert fig.parse_indian_number(token) == expected


def test_numbers_in_a_table_row_are_found_in_order():
    row = "Revenue from operations  1,09,000.50  81,200.00  62,000.00"
    values = [value for value, _ in fig.numbers_in(row)]
    assert values == [109000.50, 81200.00, 62000.00]


def test_a_bracketed_loss_is_read_as_negative():
    row = "Profit after tax (4,512.30) 1,200.00"
    values = [value for value, _ in fig.numbers_in(row)]
    assert values[0] == -4512.30


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("(Rs. in crore)", "crore"),
        ("(₹ in lakhs)", "lakh"),
        ("All figures in INR million", "million"),
        ("Amounts in Rs. lakhs unless otherwise stated", "lakh"),
        ("Figures in crores", "crore"),
        ("Statement of Profit and Loss", None),
    ],
)
def test_unit_declaration_detection(text, expected):
    assert fig.detect_unit(text) == expected


def test_a_unit_declared_on_one_page_carries_to_the_next():
    document = ExtractedDocument(
        pages=[
            Page(1, "Consolidated Statement of Profit and Loss (Rs. in crore)"),
            Page(2, "Revenue from operations 1,090.00 812.00 " + "x" * 60),
        ],
        page_count=2, method="test", char_count=200, empty_pages=[],
    )
    units = fig.document_unit_map(document)
    assert units[2][0] == "crore"
    assert "page 1" in units[2][1]


def test_a_figure_without_a_unit_is_not_normalised():
    """The 100x error this guard exists to prevent."""
    document = ExtractedDocument(
        pages=[Page(1, "Revenue from operations 1,090.00 812.00 620.00 "
                       + "padding " * 10)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    found = fig.extract_figures(document)
    revenue = [f for f in found if f.metric_key == "revenue"]
    assert revenue
    assert revenue[0].raw_value == 1090.0
    assert revenue[0].normalised_value is None
    assert revenue[0].unit is None
    assert revenue[0].needs_review is True
    assert any("no unit" in r.lower() for r in revenue[0].confidence_reasons)


def test_a_figure_with_a_unit_is_normalised_to_rupees():
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore)\nRevenue from operations 1,090.00 812.00 "
                       + "padding " * 10)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    found = fig.extract_figures(document)
    revenue = next(f for f in found if f.metric_key == "revenue")
    assert revenue.unit == "crore"
    assert revenue.normalised_value == pytest.approx(1090.0 * 1e7)


def test_an_inline_unit_beats_the_page_declaration():
    line = "Order book stood at Rs 12,500 crore as at the year end. " + "x" * 40
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in lakhs)\n" + line)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    found = fig.extract_figures(document)
    order_book = next(f for f in found if f.metric_key == "order_book")
    assert order_book.unit == "crore"
    assert "next to the number" in order_book.unit_source


# --------------------------------------------------------------------------
# Metric matching
# --------------------------------------------------------------------------


def test_the_longest_alias_wins_so_pbt_is_not_read_as_pat():
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore)\nProfit before tax 240.00 180.00 "
                       + "padding " * 10)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    found = fig.extract_figures(document)
    keys = {f.metric_key for f in found}
    assert "pbt" in keys
    assert "pat" not in keys


def test_negative_cues_stop_a_margin_line_becoming_a_currency_figure():
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore)\nEBITDA margin 21.60 20.70 "
                       + "padding " * 10)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    found = fig.extract_figures(document)
    keys = {f.metric_key for f in found}
    assert "ebitda_margin" in keys
    assert "ebitda" not in keys


def test_a_note_numbered_row_still_matches():
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore)\n1. Revenue from operations 1,090.00 "
                       + "padding " * 10)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    found = fig.extract_figures(document)
    assert any(f.metric_key == "revenue" for f in found)


def test_a_metric_mentioned_mid_sentence_is_not_extracted():
    """Anchoring at the start of the line is what prevents prose matches."""
    prose = ("The board noted that segment total revenue disclosures in note 4 "
             "will change from 2026 onwards following the new standard.")
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore)\n" + prose)],
        page_count=1, method="test", char_count=300, empty_pages=[],
    )
    found = fig.extract_figures(document)
    assert not [f for f in found if f.metric_key == "revenue"]


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FY25", "FY25"),
        ("fy2025", "FY25"),
        ("Q2FY25", "Q2FY25"),
        ("Q3 FY 2024", "Q3FY24"),
        ("for the year ended March 31, 2025", "FY25"),
    ],
)
def test_period_label_normalisation(text, expected):
    assert expected in fig.find_periods(text)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("FY25", date(2025, 3, 31)),
        ("Q1FY25", date(2024, 6, 30)),
        ("Q4FY25", date(2025, 3, 31)),
        ("nonsense", None),
        (None, None),
    ],
)
def test_period_end_derivation(label, expected):
    assert _period_end(label) == expected


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_a_statement_row_scores_higher_than_a_bare_line():
    rich = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore) FY25 FY24 FY23\n"
                       "Revenue from operations 1,090.00 812.00 620.00 "
                       + "padding " * 8)],
        page_count=1, method="test", char_count=300, empty_pages=[],
    )
    bare = ExtractedDocument(
        pages=[Page(1, "Revenue from operations 1,090.00 " + "padding " * 8)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    rich_score = next(f for f in fig.extract_figures(rich)
                      if f.metric_key == "revenue").confidence
    bare_score = next(f for f in fig.extract_figures(bare)
                      if f.metric_key == "revenue").confidence
    assert rich_score > bare_score


def test_every_figure_explains_its_confidence():
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore) FY25\nRevenue from operations 1,090.00 "
                       + "padding " * 8)],
        page_count=1, method="test", char_count=200, empty_pages=[],
    )
    for figure in fig.extract_figures(document):
        assert figure.confidence_reasons
        assert any("Final confidence" in r for r in figure.confidence_reasons)
        assert figure.quote
        assert figure.page >= 1


def test_corroboration_across_pages_raises_confidence():
    line = "Revenue from operations 1,090.00 812.00 " + "padding " * 8
    document = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore) FY25\n" + line),
               Page(2, "(Rs. in crore) FY25\n" + line)],
        page_count=2, method="test", char_count=400, empty_pages=[],
    )
    plain = fig.extract_figures(document)
    before = max(f.confidence for f in plain if f.metric_key == "revenue")
    boosted = fig.agreement_bonus(plain)
    after = max(f.confidence for f in boosted if f.metric_key == "revenue")
    assert after > before
    assert any("Corroborated" in r
               for f in boosted for r in f.confidence_reasons)


# --------------------------------------------------------------------------
# Sectioning
# --------------------------------------------------------------------------


def test_sections_are_detected_and_pages_assigned():
    document = ExtractedDocument(
        pages=[
            Page(1, "OUR BUSINESS\n" + "content " * 20),
            Page(2, "more content " * 20),
            Page(3, "RISK FACTORS\n" + "content " * 20),
            Page(4, "more risk content " * 20),
        ],
        page_count=4, method="test", char_count=800, empty_pages=[],
    )
    page_map, spans = sectioning.map_sections(document)
    assert page_map[1] == "BUSINESS"
    assert page_map[2] == "BUSINESS"       # inherited
    assert page_map[3] == "RISK_FACTORS"
    assert page_map[4] == "RISK_FACTORS"
    assert {span.name for span in spans} == {"BUSINESS", "RISK_FACTORS"}


def test_numbered_headings_are_recognised():
    document = ExtractedDocument(
        pages=[Page(1, "SECTION IV - RISK FACTORS\n" + "content " * 20)],
        page_count=1, method="test", char_count=300, empty_pages=[],
    )
    page_map, _ = sectioning.map_sections(document)
    assert page_map.get(1) == "RISK_FACTORS"


def test_a_figure_inside_the_statements_section_scores_higher():
    without = ExtractedDocument(
        pages=[Page(1, "(Rs. in crore) FY25\n"
                       "Revenue from operations 1,090.00 812.00 " + "pad " * 8)],
        page_count=1, method="test", char_count=300, empty_pages=[],
    )
    with_section = ExtractedDocument(
        pages=[Page(1, "Statement of Profit and Loss\n(Rs. in crore) FY25\n"
                       "Revenue from operations 1,090.00 812.00 " + "pad " * 8)],
        page_count=1, method="test", char_count=300, empty_pages=[],
    )
    page_map, _ = sectioning.map_sections(with_section)
    plain = next(f for f in fig.extract_figures(without)
                 if f.metric_key == "revenue")
    sectioned = next(f for f in fig.extract_figures(with_section, page_map)
                     if f.metric_key == "revenue")
    assert sectioned.confidence > plain.confidence


# --------------------------------------------------------------------------
# Commentary and risk factors
# --------------------------------------------------------------------------


def test_commentary_is_reproduced_verbatim_not_paraphrased():
    sentence = ("We expect revenue growth in the mid-teens for the coming year "
                "as capacity utilisation improves across our plants.")
    document = ExtractedDocument(
        pages=[Page(1, "MANAGEMENT DISCUSSION AND ANALYSIS\n" + sentence)],
        page_count=1, method="test", char_count=300, empty_pages=[],
    )
    page_map, _ = sectioning.map_sections(document)
    quotes = sectioning.extract_commentary(document, page_map)
    guidance = [q for q in quotes if q.category == "GUIDANCE"]
    assert guidance
    assert guidance[0].text.startswith("We expect revenue growth")
    assert "does not paraphrase" in guidance[0].note


def test_a_quantified_concentration_risk_is_extracted_with_its_quantum():
    paragraph = (
        "Our top five customers accounted for 54.20% of our revenue from "
        "operations in the last financial year, and the loss of any one of "
        "them would adversely affect our business and results of operations."
    )
    document = ExtractedDocument(
        pages=[Page(1, "RISK FACTORS\n\n" + paragraph)],
        page_count=1, method="test", char_count=400, empty_pages=[],
    )
    page_map, _ = sectioning.map_sections(document)
    risks = sectioning.extract_risk_factors(document, page_map)
    assert risks
    concentration = next(r for r in risks
                         if r["category"] == "CUSTOMER_CONCENTRATION")
    assert concentration["quantum"] == pytest.approx(54.2)
    assert concentration["severity"] == "HIGH"
    assert "54.2" in concentration["severity_basis"]


def test_an_unquantified_risk_defaults_to_medium_and_says_so():
    paragraph = (
        "We are subject to extensive regulatory approval requirements and any "
        "failure to obtain or renew our licences could interrupt operations "
        "at one or more of our facilities for an extended period."
    )
    document = ExtractedDocument(
        pages=[Page(1, "RISK FACTORS\n\n" + paragraph)],
        page_count=1, method="test", char_count=400, empty_pages=[],
    )
    page_map, _ = sectioning.map_sections(document)
    risks = sectioning.extract_risk_factors(document, page_map)
    regulatory = next(r for r in risks if r["category"] == "REGULATORY")
    assert regulatory["severity"] == "MEDIUM"
    assert "pending review" in regulatory["severity_basis"]


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------


def test_html_tables_are_flattened_so_labels_keep_their_numbers():
    html = """
    <html><body>
      <p>(Rs. in crore)</p>
      <table>
        <tr><th>Particulars</th><th>FY25</th><th>FY24</th></tr>
        <tr><td>Revenue from operations</td><td>1,090.00</td><td>812.00</td></tr>
      </table>
    </body></html>
    """
    document = text_extraction.extract_from_html(html)
    assert "Revenue from operations" in document.full_text()
    found = fig.extract_figures(document)
    revenue = next(f for f in found if f.metric_key == "revenue")
    assert revenue.raw_value == 1090.0
    assert revenue.unit == "crore"


def test_a_page_with_no_text_is_flagged_rather_than_ignored():
    document = ExtractedDocument(
        pages=[Page(1, ""), Page(2, "real content " * 20)],
        page_count=2, method="test", char_count=250, empty_pages=[1],
    )
    assert document.pages[0].is_empty
    assert not document.pages[1].is_empty


def test_a_mostly_empty_pdf_is_reported_as_a_probable_scan():
    pages = [Page(i, "") for i in range(1, 10)] + [Page(10, "text " * 30)]
    document = ExtractedDocument(
        pages=pages, page_count=10, method="pypdf", char_count=150,
        empty_pages=list(range(1, 10)),
    )
    assert document.looks_like_a_scan is True


def test_plaintext_form_feeds_become_pages():
    document = text_extraction.extract_from_text("page one text\fpage two text")
    assert document.page_count == 2


# --------------------------------------------------------------------------
# End to end, through a real generated PDF
# --------------------------------------------------------------------------


def _statement_pdf() -> bytes:
    """A two-page PDF that looks like an Indian annual report extract."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)

    pdf.setFont("Helvetica", 10)
    y = 800
    for line in [
        "STATEMENT OF PROFIT AND LOSS",
        "(Rs. in crore)",
        "Particulars                     FY25        FY24        FY23",
        "Revenue from operations      1,090.00      812.00      620.00",
        "Other income                    18.40       14.20       11.90",
        "EBITDA                         235.00      168.00      118.00",
        "Finance cost                    12.30       14.80       16.10",
        "Profit before tax              198.50      142.60       96.40",
        "Profit after tax               148.00      104.00       74.00",
        "Earnings per share              22.60       16.90       12.40",
    ]:
        pdf.drawString(60, y, line)
        y -= 18
    pdf.showPage()

    pdf.setFont("Helvetica", 10)
    y = 800
    for line in [
        "RISK FACTORS",
        "",
        "Our top five customers accounted for 54.20% of our revenue from",
        "operations in the last financial year and the loss of any of them",
        "would adversely affect our business and results of operations.",
        "",
        "MANAGEMENT DISCUSSION AND ANALYSIS",
        "",
        "We expect revenue growth in the mid-teens for the coming year as",
        "capacity utilisation improves across our manufacturing plants.",
    ]:
        pdf.drawString(60, y, line)
        y -= 18
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_a_real_pdf_flows_all_the_way_through():
    pytest.importorskip("pypdf")
    document = text_extraction.extract_from_bytes(_statement_pdf(), "report.pdf")

    assert document.succeeded
    assert document.page_count == 2
    assert document.method == "pypdf"
    assert document.checksum

    page_map, _ = sectioning.map_sections(document)
    found = fig.agreement_bonus(fig.extract_figures(document, page_map))
    by_key = {f.metric_key: f for f in found}

    # The figures the statement actually prints, normalised from crore.
    assert by_key["revenue"].raw_value == pytest.approx(1090.0)
    assert by_key["revenue"].unit == "crore"
    assert by_key["revenue"].normalised_value == pytest.approx(1090.0 * 1e7)
    assert by_key["pat"].raw_value == pytest.approx(148.0)
    assert by_key["pbt"].raw_value == pytest.approx(198.5)
    assert by_key["ebitda"].raw_value == pytest.approx(235.0)

    # EPS is per-share, so it is never multiplied by the crore factor.
    assert by_key["eps"].raw_value == pytest.approx(22.6)
    assert by_key["eps"].normalised_value == pytest.approx(22.6)

    # Everything cites a real page.
    for figure in found:
        assert 1 <= figure.page <= 2
        assert figure.quote

    risks = sectioning.extract_risk_factors(document, page_map)
    assert any(r["category"] == "CUSTOMER_CONCENTRATION" for r in risks)

    commentary = sectioning.extract_commentary(document, page_map)
    assert any(q.category == "GUIDANCE" for q in commentary)


def test_extraction_of_a_non_document_fails_cleanly():
    with pytest.raises(text_extraction.ExtractionError):
        text_extraction.extract_from_bytes(b"%PDF-not really a pdf", "x.pdf")
