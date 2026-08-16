"""The review gate, exercised through the API.

The single most important property of this pipeline: an extracted figure is a
citation awaiting review, never a fact, until a human approves it. These tests
exist to make that regressible.
"""

import pytest
from sqlalchemy import select

from tests.conftest import auth

STATEMENT = (
    "STATEMENT OF PROFIT AND LOSS\n"
    "(Rs. in crore)\n"
    "Particulars FY25 FY24 FY23\n"
    "Revenue from operations 1,090.00 812.00 620.00\n"
    "EBITDA 235.00 168.00 118.00\n"
    "Profit after tax 148.00 104.00 74.00\n"
)


def _upload(app_client, token, body: str, symbol: str = "HDFCBANK") -> str:
    response = app_client.post(
        "/api/admin/documents/upload",
        headers=auth(token),
        files={"file": ("filing.txt", body.encode("utf-8"), "text/plain")},
        data={"doc_type": "ANNUAL_REPORT", "title": "Test filing",
              "symbol": symbol, "document_date": "2025-06-30"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _extract(app_client, token, document_id: str) -> dict:
    response = app_client.post(
        f"/api/admin/documents/{document_id}/extract", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# Access control and validation
# --------------------------------------------------------------------------


def test_anonymous_callers_cannot_upload_or_extract(app_client):
    assert app_client.post("/api/admin/documents/upload").status_code == 401
    assert app_client.post(
        "/api/admin/documents/whatever/extract"
    ).status_code == 401


def test_upload_requires_a_symbol_or_an_ipo(app_client, admin_token):
    response = app_client.post(
        "/api/admin/documents/upload", headers=auth(admin_token),
        files={"file": ("f.txt", b"hello world", "text/plain")},
        data={"doc_type": "ANNUAL_REPORT"},
    )
    assert response.status_code == 400
    assert "symbol or an IPO" in response.json()["detail"]


def test_upload_rejects_an_unsupported_file_type(app_client, admin_token):
    response = app_client.post(
        "/api/admin/documents/upload", headers=auth(admin_token),
        files={"file": ("thing.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"doc_type": "ANNUAL_REPORT", "symbol": "HDFCBANK"},
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_upload_rejects_an_unknown_document_type(app_client, admin_token):
    response = app_client.post(
        "/api/admin/documents/upload", headers=auth(admin_token),
        files={"file": ("f.txt", b"hello world", "text/plain")},
        data={"doc_type": "NOT_A_TYPE", "symbol": "HDFCBANK"},
    )
    assert response.status_code == 400


def test_upload_rejects_an_empty_file(app_client, admin_token):
    response = app_client.post(
        "/api/admin/documents/upload", headers=auth(admin_token),
        files={"file": ("f.txt", b"", "text/plain")},
        data={"doc_type": "ANNUAL_REPORT", "symbol": "HDFCBANK"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_extraction_writes_citations_not_facts(app_client, admin_token, db):
    from app.models.fundamental import FinancialStatement

    document_id = _upload(app_client, admin_token, STATEMENT)

    before = len(db.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == "HDFCBANK")
        .where(FinancialStatement.provider == "pipeline")
    ).scalars().all())

    summary = _extract(app_client, admin_token, document_id)
    assert summary["status"] == "EXTRACTED"
    assert summary["figures_found"] >= 3
    assert summary["citations_written"] >= 3

    db.expire_all()
    after = len(db.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == "HDFCBANK")
        .where(FinancialStatement.provider == "pipeline")
    ).scalars().all())
    assert after == before, "extraction must not write into fundamentals"


def test_every_citation_is_pending_with_a_page_and_a_quote(app_client,
                                                           admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT)
    _extract(app_client, admin_token, document_id)

    detail = app_client.get(f"/api/documents/{document_id}").json()
    figures = detail["citations"].get("FIGURE", [])
    assert figures
    for citation in figures:
        assert citation["review_status"] == "PENDING"
        assert citation["page"]
        assert citation["quote"]
        assert citation["confidence_reasons"]
        assert citation["source"]["document_id"] == document_id
    assert detail["summary"]["approved"] == 0


def test_units_are_shown_on_both_sides_of_the_conversion(app_client,
                                                         admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT)
    _extract(app_client, admin_token, document_id)

    detail = app_client.get(f"/api/documents/{document_id}").json()
    revenue = next(c for c in detail["citations"]["FIGURE"]
                   if c["metric_key"] == "revenue")
    assert revenue["raw_value"] == pytest.approx(1090.0)
    assert revenue["unit"] == "crore"
    assert revenue["unit_multiplier"] == pytest.approx(1e7)
    assert revenue["normalised_value"] == pytest.approx(1090.0 * 1e7)


def test_approval_promotes_the_figure_and_names_the_approver(
    app_client, admin_token, db
):
    from app.models.fundamental import FinancialStatement

    document_id = _upload(app_client, admin_token, STATEMENT, symbol="INFY")
    _extract(app_client, admin_token, document_id)

    detail = app_client.get(f"/api/documents/{document_id}").json()
    revenue = next(c for c in detail["citations"]["FIGURE"]
                   if c["metric_key"] == "revenue" and c["period_label"])

    approved = app_client.post(
        f"/api/admin/documents/citations/{revenue['id']}/approve",
        headers=auth(admin_token), json={},
    ).json()
    assert approved["review_status"] == "APPROVED"
    assert "financial_statements.revenue" in (approved.get("promoted_to") or "")

    db.expire_all()
    row = db.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == "INFY")
        .where(FinancialStatement.period_label == revenue["period_label"])
    ).scalars().first()
    assert row is not None
    assert row.revenue == pytest.approx(revenue["value"])
    # A promoted figure is operator-approved, and never claims to be live data.
    assert row.data_status == "MANUAL"

    audit = app_client.get(
        f"/api/admin/audit?entity_type=research_citation&entity_id={revenue['id']}",
        headers=auth(admin_token),
    ).json()
    assert audit["entries"]
    assert audit["entries"][0]["actor_email"]


def test_a_reviewer_can_correct_the_value_before_approving(app_client,
                                                           admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT, symbol="VOLTAS")
    _extract(app_client, admin_token, document_id)
    detail = app_client.get(f"/api/documents/{document_id}").json()
    citation = next(c for c in detail["citations"]["FIGURE"]
                    if c["metric_key"] == "pat" and c["period_label"])

    app_client.post(
        f"/api/admin/documents/citations/{citation['id']}/approve",
        headers=auth(admin_token),
        json={"override_value": 1_480_000_000.0, "reason": "unit corrected"},
    )
    updated = app_client.get(f"/api/documents/{document_id}").json()
    corrected = next(c for c in updated["citations"]["FIGURE"]
                     if c["id"] == citation["id"])
    assert corrected["value"] == pytest.approx(1_480_000_000.0)
    assert any("corrected by reviewer" in reason
               for reason in corrected["confidence_reasons"])


def test_approving_twice_is_refused(app_client, admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT, symbol="SIEMENS")
    _extract(app_client, admin_token, document_id)
    detail = app_client.get(f"/api/documents/{document_id}").json()
    citation = detail["citations"]["FIGURE"][0]

    first = app_client.post(
        f"/api/admin/documents/citations/{citation['id']}/approve",
        headers=auth(admin_token), json={})
    second = app_client.post(
        f"/api/admin/documents/citations/{citation['id']}/approve",
        headers=auth(admin_token), json={})
    assert first.status_code == 200
    assert second.status_code == 400


def test_rejection_records_the_decision(app_client, admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT,
                          symbol="BAJAJELEC")
    _extract(app_client, admin_token, document_id)
    detail = app_client.get(f"/api/documents/{document_id}").json()
    citation = detail["citations"]["FIGURE"][0]

    response = app_client.post(
        f"/api/admin/documents/citations/{citation['id']}/reject",
        headers=auth(admin_token), json={"reason": "misparsed row"},
    ).json()
    assert response["review_status"] == "REJECTED"


# --------------------------------------------------------------------------
# Queues and views
# --------------------------------------------------------------------------


def test_the_review_queue_lists_only_pending_machine_extractions(app_client,
                                                                 admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT,
                          symbol="RELIANCE")
    _extract(app_client, admin_token, document_id)

    queue = app_client.get(
        "/api/documents/citations/queue?symbol=RELIANCE"
    ).json()
    assert queue["count"] > 0
    for citation in queue["citations"]:
        assert citation["review_status"] == "PENDING"
        assert citation["extracted_by"] == "PIPELINE"
    assert "not whether the figure is correct" in queue["guidance"]


def test_the_company_view_excludes_unreviewed_claims(app_client, admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT,
                          symbol="ICICIBANK")
    _extract(app_client, admin_token, document_id)

    view = app_client.get("/api/stocks/ICICIBANK/documents").json()
    assert view["pending_claims"] > 0
    assert view["approved_claims"] == []
    assert "deliberately excluded" in view["note"]


def test_re_extraction_replaces_rather_than_duplicates(app_client, admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT, symbol="BDL")
    first = _extract(app_client, admin_token, document_id)
    second = _extract(app_client, admin_token, document_id)
    assert first["citations_written"] == second["citations_written"]

    detail = app_client.get(f"/api/documents/{document_id}").json()
    assert detail["summary"]["total"] == second["citations_written"]


def test_an_unreadable_document_reports_the_problem(app_client, admin_token):
    document_id = _upload(app_client, admin_token, "   ")
    summary = _extract(app_client, admin_token, document_id)
    assert summary["status"] in ("NO_CONTENT", "FAILED")
    assert summary["warnings"]


def test_extraction_and_upload_are_both_audited(app_client, admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT, symbol="INFY")
    _extract(app_client, admin_token, document_id)

    audit = app_client.get(
        f"/api/admin/audit?entity_type=research_document&entity_id={document_id}",
        headers=auth(admin_token),
    ).json()
    actions = {entry["action"] for entry in audit["entries"]}
    assert "DOCUMENT_EXTRACTED" in actions
    assert "DOCUMENT_UPLOADED" in actions


def test_the_document_list_reports_pending_counts(app_client, admin_token):
    document_id = _upload(app_client, admin_token, STATEMENT, symbol="INFY")
    _extract(app_client, admin_token, document_id)

    listing = app_client.get("/api/documents?symbol=INFY").json()
    row = next(d for d in listing["documents"] if d["id"] == document_id)
    assert row["citations"] > 0
    assert row["pending_review"] >= 0
    assert "awaiting review" in listing["review_note"]
