"""API, authentication, permissions, provenance and compliance guard rails."""

import pytest

from tests.conftest import auth


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


def test_no_verification_badge_without_a_configured_registration(app_client):
    payload = app_client.get("/api/config/compliance").json()
    assert payload["is_registered"] is False
    assert payload["verification_badge"] is None
    assert payload["descriptor"] == "Educational / informational market research platform"


def test_root_does_not_claim_verification(app_client):
    payload = app_client.get("/").json()
    assert payload["verified"] is False
    assert "not investment advice" in payload["notice"].lower()


def test_prohibited_claims_are_published_so_the_ui_can_show_them(app_client):
    payload = app_client.get("/api/config/compliance").json()
    claims = [c.lower() for c in payload["prohibited_claims"]]
    for phrase in ("sebi verified", "guaranteed returns", "risk-free"):
        assert phrase in claims


def test_a_research_call_containing_a_prohibited_claim_is_rejected(
    app_client, admin_token
):
    response = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                               json={
        "symbol": "HDFCBANK", "company_name": "HDFC Bank Ltd",
        "side": "BUY", "source_type": "EXTERNAL_RESEARCH",
        "source_name": "Test desk",
        "entry_min": 700.0, "entry_max": 702.0, "stop_loss": 690.0,
        "target_1": 730.0,
        "rationale": "This is a risk-free trade with guaranteed returns.",
    })
    assert response.status_code == 400
    assert "cannot support" in response.json()["detail"]


def test_external_research_must_name_its_source(app_client, admin_token):
    response = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                               json={
        "symbol": "HDFCBANK", "side": "BUY",
        "source_type": "EXTERNAL_RESEARCH", "source_name": "",
        "entry_min": 700.0, "entry_max": 702.0, "stop_loss": 690.0,
        "target_1": 730.0,
    })
    assert response.status_code in (400, 422)


def test_a_buy_with_a_stop_above_the_entry_is_rejected(app_client, admin_token):
    response = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                               json={
        "symbol": "HDFCBANK", "side": "BUY",
        "source_type": "EXTERNAL_RESEARCH", "source_name": "Test desk",
        "entry_min": 700.0, "entry_max": 702.0,
        "stop_loss": 710.0,          # above the entry range
        "target_1": 730.0,
    })
    assert response.status_code == 400
    assert "stop loss must sit below" in response.json()["detail"]


def test_a_buy_with_a_target_below_the_entry_is_rejected(app_client, admin_token):
    response = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                               json={
        "symbol": "HDFCBANK", "side": "BUY",
        "source_type": "EXTERNAL_RESEARCH", "source_name": "Test desk",
        "entry_min": 700.0, "entry_max": 702.0, "stop_loss": 690.0,
        "target_1": 695.0,           # below the entry range
    })
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Authentication and permissions
# --------------------------------------------------------------------------


def test_protected_endpoints_reject_anonymous_callers(app_client):
    for path in ("/api/watchlists", "/api/alerts", "/api/portfolio",
                 "/api/admin/users"):
        assert app_client.get(path).status_code == 401, path


def test_invalid_token_is_rejected(app_client):
    response = app_client.get("/api/watchlists",
                              headers={"Authorization": "Bearer nonsense"})
    assert response.status_code == 401


def test_first_registered_user_becomes_admin(app_client, admin_token):
    payload = app_client.get("/api/auth/me", headers=auth(admin_token)).json()
    assert payload["role"] == "ADMIN"


def test_second_user_is_not_an_admin(app_client, user_token):
    payload = app_client.get("/api/auth/me", headers=auth(user_token)).json()
    assert payload["role"] == "USER"


def test_plain_user_cannot_reach_the_admin_panel(app_client, user_token):
    response = app_client.get("/api/admin/users", headers=auth(user_token))
    assert response.status_code == 403
    assert "ADMIN role" in response.json()["detail"]


def test_registering_an_existing_email_does_not_confirm_it_exists(app_client,
                                                                  admin_token):
    response = app_client.post("/api/auth/register", json={
        "email": "admin@example.com", "password": "a-sufficiently-long-password",
    })
    assert response.status_code == 400
    # Deliberately non-specific: no account enumeration.
    assert "could not be completed" in response.json()["detail"]


def test_short_passwords_are_rejected(app_client):
    response = app_client.post("/api/auth/register", json={
        "email": "weak@example.com", "password": "short",
    })
    assert response.status_code == 422


def test_login_with_a_wrong_password_fails(app_client, admin_token):
    response = app_client.post("/api/auth/login", json={
        "email": "admin@example.com", "password": "definitely-not-the-password",
    })
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_every_research_call_carries_its_source(app_client):
    payload = app_client.get("/api/research/calls").json()
    assert payload["calls"], "the demo seed should have published calls"
    for call in payload["calls"]:
        assert call["source"]["name"]
        assert call["source"]["attribution_notice"]
        assert call["data_status"]


def test_demo_rows_are_badged_as_demo(app_client):
    payload = app_client.get("/api/research/calls").json()
    assert any(call["is_demo"] for call in payload["calls"])


def test_status_is_derived_not_a_permanent_buy(app_client):
    payload = app_client.get("/api/research/calls").json()
    statuses = {call["status"] for call in payload["calls"]}
    # The seeded set deliberately spans several states.
    assert len(statuses) > 1
    assert statuses <= {
        "NOT_ACTIVATED", "WITHIN_ENTRY", "ABOVE_ENTRY", "BELOW_ENTRY",
        "TARGET_IN_PROGRESS", "TARGET_ACHIEVED", "STOP_LOSS_TRIGGERED",
        "EXPIRED", "INVALIDATED", "UNKNOWN",
    }


def test_stock_research_returns_a_full_evidence_chain(app_client):
    payload = app_client.get("/api/stocks/HDFCBANK/research").json()
    for key in ("technical", "risk", "confidence", "scorecard", "why_now",
                "why_not", "sources", "data_quality", "disclaimer"):
        assert key in payload, key
    assert payload["why_now"] and payload["why_not"]
    assert payload["sources"]
    for source in payload["sources"]:
        assert source["status"]
        assert source["source"]


def test_technical_evidence_items_carry_their_calculation(app_client):
    payload = app_client.get("/api/stocks/HDFCBANK/technicals").json()
    items = payload["evidence_chain"]["evidence"] + \
        payload["evidence_chain"]["counter_evidence"]
    assert items
    for item in items:
        assert item["metric"]
        assert item["calculation"] or item["interpretation"]


def test_unknown_symbol_returns_a_helpful_404(app_client):
    response = app_client.get("/api/stocks/NOTAREALSYMBOL")
    assert response.status_code == 404
    assert "instrument master" in response.json()["detail"]


def test_option_chain_reports_unavailability_with_a_remedy(app_client):
    payload = app_client.get("/api/fno/options/NOSUCHUNDERLYING").json()
    assert payload["available"] is False
    assert payload["reason"]
    assert payload["how_to_fix"]


# --------------------------------------------------------------------------
# Derivatives disclosure
# --------------------------------------------------------------------------


def test_fno_summary_always_carries_a_risk_disclosure(app_client):
    payload = app_client.get("/api/fno/summary").json()
    assert payload["risk_disclosure"]["text"]
    assert "magnify" in payload["risk_disclosure"]["text"].lower()


def test_any_quoted_statistic_carries_its_study_period(app_client):
    payload = app_client.get("/api/fno/summary").json()
    for claim in payload["risk_disclosure"]["statistical_claims"]:
        assert claim["study_period"]
        assert claim["source_name"]


# --------------------------------------------------------------------------
# Calculators
# --------------------------------------------------------------------------


def test_position_size_endpoint(app_client):
    response = app_client.post("/api/calculators/position-size", json={
        "capital": 100000, "max_loss_pct": 1, "entry": 100, "stop_loss": 95,
    })
    payload = response.json()
    assert payload["quantity"] == 200
    assert payload["assumptions"]


def test_pnl_endpoint_returns_three_scenarios_and_a_disclaimer(app_client):
    response = app_client.post("/api/calculators/pnl", json={
        "capital": 100000, "entry": 100, "stop_loss": 95, "target": 110,
        "quantity": 500,
    })
    payload = response.json()
    assert {s["scenario"] for s in payload["scenarios"]} == {"Bull", "Base", "Bear"}
    assert "is a prediction" in payload["disclaimer"]


# --------------------------------------------------------------------------
# Health, methodology, search
# --------------------------------------------------------------------------


def test_health_reports_every_provider(app_client):
    payload = app_client.get("/api/health").json()
    assert payload["status"] in ("OK", "DEGRADED", "DOWN")
    names = {p["name"] for p in payload["providers"]}
    assert {"yahoo", "nse", "demo", "manual"} <= names


def test_environment_endpoint_leaks_no_secrets(app_client):
    body = app_client.get("/api/config/environment").text.lower()
    for secret in ("secret_key", "password", "api_secret", "bot_token"):
        assert secret not in body


def test_methodology_names_the_source_file_for_each_engine(app_client):
    payload = app_client.get("/api/methodology").json()
    assert payload["source_files"]["greeks"].endswith("greeks.py")
    assert payload["engine_versions"]


def test_search_returns_quick_actions(app_client):
    payload = app_client.get("/api/search?q=HDFC").json()
    assert payload["results"]
    assert payload["results"][0]["actions"]


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------


def test_creating_a_research_call_writes_an_audit_entry(app_client, admin_token):
    created = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                              json={
        "symbol": "INFY", "company_name": "Infosys Ltd", "side": "BUY",
        "source_type": "EXTERNAL_RESEARCH", "source_name": "Test desk",
        "entry_min": 1550.0, "entry_max": 1560.0, "stop_loss": 1500.0,
        "target_1": 1650.0, "rationale": "Test rationale.",
    })
    assert created.status_code == 201
    call_id = created.json()["id"]

    audit = app_client.get(
        f"/api/admin/audit?entity_type=research_call&entity_id={call_id}",
        headers=auth(admin_token),
    ).json()
    assert audit["entries"]
    # Entries are newest-first and the status engine writes its own row,
    # so assert the creation is present rather than that it is on top.
    actions = {entry["action"] for entry in audit["entries"]}
    assert "RESEARCH_CALL_CREATED" in actions
    assert "append-only" in audit["note"]


def test_derived_fields_cannot_be_set_by_hand(app_client, admin_token):
    created = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                              json={
        "symbol": "VOLTAS", "company_name": "Voltas Ltd", "side": "BUY",
        "source_type": "EXTERNAL_RESEARCH", "source_name": "Test desk",
        "entry_min": 1300.0, "entry_max": 1310.0, "stop_loss": 1250.0,
        "target_1": 1400.0,
    }).json()

    response = app_client.patch(
        f"/api/admin/research-call/{created['id']}", headers=auth(admin_token),
        json={"reason": "attempting to fake a win",
              "changes": {"status": "TARGET_ACHIEVED", "achieved_pct": 99.0}},
    )
    assert response.status_code == 400
    assert "derived" in response.json()["detail"]


def test_editing_a_call_requires_a_reason(app_client, admin_token):
    created = app_client.post("/api/admin/research-call", headers=auth(admin_token),
                              json={
        "symbol": "BAJAJELEC", "company_name": "Bajaj Electricals Ltd",
        "side": "BUY", "source_type": "EXTERNAL_RESEARCH",
        "source_name": "Test desk", "entry_min": 360.0, "entry_max": 362.0,
        "stop_loss": 350.0, "target_1": 380.0,
    }).json()

    response = app_client.patch(
        f"/api/admin/research-call/{created['id']}", headers=auth(admin_token),
        json={"changes": {"target_1": 400.0}},
    )
    assert response.status_code == 422  # `reason` is required by the schema
