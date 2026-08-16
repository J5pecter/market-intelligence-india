"""Risk, confidence, conflict detection, news scoring, IPO scoring, analogues.

The theme running through these: the engines must refuse to invent certainty.
A missing input has to be reported as missing, a conflict has to survive to the
output, and a thin sample must not be dressed up as a statistic.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.services import indicators as ind
from app.services.confidence import confidence_service
from app.services.evidence import (EvidenceChain, EvidenceItem, Stance,
                                   merge_stance)
from app.services.historical_analogue import historical_analogue_service
from app.services.ipo_analysis import ipo_analysis_service
from app.services.news_analysis import news_analysis_service
from app.services.options_analysis import classify_buildup
from app.services.risk import RiskRating, risk_service


# --------------------------------------------------------------------------
# Evidence chain
# --------------------------------------------------------------------------


def _chain(dimension: str, positives: int, negatives: int) -> EvidenceChain:
    chain = EvidenceChain(dimension=dimension)
    for i in range(positives):
        chain.add(EvidenceItem(metric=f"pos{i}", value=1, stance=Stance.POSITIVE))
    for i in range(negatives):
        chain.add(EvidenceItem(metric=f"neg{i}", value=-1, stance=Stance.NEGATIVE))
    return chain.finalise()


def test_all_positive_evidence_scores_high():
    assert _chain("TECHNICAL", 4, 0).score == 100.0


def test_all_negative_evidence_scores_zero():
    assert _chain("TECHNICAL", 0, 4).score == 0.0


def test_balanced_evidence_scores_neutral():
    assert _chain("TECHNICAL", 2, 2).score == pytest.approx(50.0)


def test_empty_chain_scores_none_not_fifty():
    chain = EvidenceChain(dimension="TECHNICAL").note_gap("no data").finalise()
    assert chain.score is None
    assert chain.stance is Stance.UNKNOWN


def test_explanation_names_the_metrics_behind_the_conclusion():
    chain = EvidenceChain(dimension="TECHNICAL")
    chain.add(EvidenceItem(
        metric="RSI(14)", value=62, stance=Stance.POSITIVE, weight=2.0,
        interpretation="RSI is 62, above the 55 threshold",
    ))
    chain.finalise()
    text = chain.explain()
    assert "because" in text
    assert "RSI is 62" in text


def test_unavailable_chain_explains_the_gap_rather_than_hedging():
    chain = EvidenceChain(dimension="OPTIONS")
    chain.note_gap("no option chain provider is configured")
    chain.finalise()
    assert "unavailable" in chain.explain()
    assert "no option chain provider" in chain.explain()


def test_conflict_between_dimensions_is_reported():
    result = merge_stance([_chain("TECHNICAL", 4, 0), _chain("FUNDAMENTAL", 0, 4)])
    assert result["conflict_detected"] is True
    assert "TECHNICAL" in result["positive_dimensions"]
    assert "FUNDAMENTAL" in result["negative_dimensions"]


def test_agreement_is_not_reported_as_conflict():
    result = merge_stance([_chain("TECHNICAL", 4, 0), _chain("FUNDAMENTAL", 3, 0)])
    assert result["conflict_detected"] is False


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_conflicting_evidence_lowers_confidence_and_blocks_a_direction():
    result = confidence_service.score(
        [_chain("TECHNICAL", 4, 0), _chain("FUNDAMENTAL", 0, 4)],
        data_quality=90.0,
    )
    assert result.conflict["conflict_detected"] is True
    assert "conflict" in result.penalties
    assert result.recommendation_state == "MIXED_WAIT_FOR_CONFIRMATION"


def test_thin_coverage_is_penalised():
    result = confidence_service.score([_chain("TECHNICAL", 4, 0)],
                                      data_quality=90.0)
    assert "coverage" in result.penalties
    assert result.coverage_pct < 50


def test_poor_data_quality_is_penalised():
    chains = [_chain(d, 3, 0) for d in
              ("TECHNICAL", "FUNDAMENTAL", "OPTIONS", "NEWS", "VOLUME",
               "HISTORICAL", "CATALYST")]
    good = confidence_service.score(chains, data_quality=95.0)
    poor = confidence_service.score(chains, data_quality=30.0)
    assert poor.overall < good.overall
    assert "data_quality" in poor.penalties


def test_no_scoreable_dimension_returns_none_not_zero():
    empty = EvidenceChain(dimension="TECHNICAL").note_gap("nothing").finalise()
    result = confidence_service.score([empty])
    assert result.overall is None
    assert result.band == "UNAVAILABLE"
    assert result.recommendation_state == "INSUFFICIENT_EVIDENCE"


def test_confidence_payload_states_it_is_not_a_probability():
    result = confidence_service.score([_chain("TECHNICAL", 3, 0)])
    assert "not a probability" in result.to_dict()["caveat"]


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------


def test_poor_risk_reward_forces_at_least_high_risk():
    assessment = risk_service.assess(
        symbol="TEST", risk_reward=0.4, atr_pct=1.0,
        average_daily_turnover=5e8, data_quality=95.0,
    )
    assert assessment.rating in (RiskRating.HIGH, RiskRating.VERY_HIGH)


def test_illiquid_instrument_raises_a_warning():
    assessment = risk_service.assess(
        symbol="TEST", risk_reward=3.0, atr_pct=1.0,
        average_daily_turnover=2e5, data_quality=95.0,
    )
    assert any("Low liquidity" in w for w in assessment.warnings)


def test_a_single_severe_factor_is_not_averaged_away():
    """Everything benign except liquidity - the rating must still rise."""
    assessment = risk_service.assess(
        symbol="TEST", risk_reward=4.0, atr_pct=1.0,
        average_daily_turnover=1e4, data_quality=98.0,
    )
    assert assessment.rating in (RiskRating.HIGH, RiskRating.VERY_HIGH)


def test_option_near_expiry_is_flagged():
    assessment = risk_service.assess(
        symbol="TEST", segment="OPTION", days_to_expiry=1,
        option_premium=20.0, theta_per_day=-2.0, implied_volatility=45.0,
        open_interest=50_000, risk_reward=2.0, data_quality=90.0,
    )
    assert assessment.rating in (RiskRating.HIGH, RiskRating.VERY_HIGH)
    assert any(f.key == "expiry" for f in assessment.factors)


def test_unassessed_dimensions_are_named_not_hidden():
    assessment = risk_service.assess(symbol="TEST", risk_reward=2.0)
    assert assessment.unassessed
    assert "Not assessed" in assessment.explain()


def test_no_inputs_yields_unknown_rating():
    assessment = risk_service.assess(symbol="TEST")
    assert assessment.rating is RiskRating.UNKNOWN
    assert assessment.score is None


def test_imminent_earnings_raise_event_risk():
    assessment = risk_service.assess(
        symbol="TEST", risk_reward=3.0, atr_pct=1.0,
        average_daily_turnover=5e8, earnings_within_days=2, data_quality=95.0,
    )
    event = next(f for f in assessment.factors if f.key == "event")
    assert event.score >= 80


# --------------------------------------------------------------------------
# Options build-up
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "price_change,oi_change,expected",
    [
        (1.0, 100, "LONG_BUILDUP"),
        (-1.0, 100, "SHORT_BUILDUP"),
        (1.0, -100, "SHORT_COVERING"),
        (-1.0, -100, "LONG_UNWINDING"),
        (0.0, 100, "UNCLEAR"),
        (1.0, 0, "UNCLEAR"),
        (None, 100, "UNCLEAR"),
        (1.0, None, "UNCLEAR"),
    ],
)
def test_buildup_classification(price_change, oi_change, expected):
    assert classify_buildup(price_change, oi_change) == expected


# --------------------------------------------------------------------------
# News scoring
# --------------------------------------------------------------------------


def test_positive_earnings_headline_scores_positive():
    result = news_analysis_service.assess(
        "Acme Ltd Q2 profit rises 42%, beats estimates",
        publisher="Reuters", symbol="ACME", company_name="Acme Ltd",
        published_at=datetime.now(tz=timezone.utc),
    )
    assert result.sentiment == "POSITIVE"
    assert result.event_category == "EARNINGS"
    assert result.impact_score > 30


def test_fraud_headline_scores_strongly_negative():
    result = news_analysis_service.assess(
        "Sebi orders forensic audit into alleged fraud at Acme Ltd",
        publisher="Business Standard", symbol="ACME", company_name="Acme Ltd",
        published_at=datetime.now(tz=timezone.utc),
    )
    assert result.sentiment == "NEGATIVE"
    assert result.sentiment_score < -0.3


def test_negation_flips_a_lexicon_term():
    positive = news_analysis_service.assess("Acme beats estimates",
                                            symbol="ACME")
    negated = news_analysis_service.assess("Acme does not beat estimates",
                                           symbol="ACME")
    assert negated.sentiment_score < positive.sentiment_score


def test_irrelevant_company_lowers_the_impact_score():
    relevant = news_analysis_service.assess(
        "Acme Ltd wins order worth Rs 500 crore", symbol="ACME",
        company_name="Acme Ltd", publisher="Reuters",
        published_at=datetime.now(tz=timezone.utc),
    )
    irrelevant = news_analysis_service.assess(
        "Some other company wins order worth Rs 500 crore", symbol="ACME",
        company_name="Acme Ltd", publisher="Reuters",
        published_at=datetime.now(tz=timezone.utc),
    )
    assert irrelevant.impact_score < relevant.impact_score


def test_old_news_scores_lower_than_fresh_news():
    now = datetime.now(tz=timezone.utc)
    fresh = news_analysis_service.assess(
        "Acme Ltd profit rises 42%", symbol="ACME", company_name="Acme Ltd",
        publisher="Reuters", published_at=now,
    )
    stale = news_analysis_service.assess(
        "Acme Ltd profit rises 42%", symbol="ACME", company_name="Acme Ltd",
        publisher="Reuters", published_at=now - timedelta(days=20),
    )
    assert stale.impact_score < fresh.impact_score


def test_unknown_publisher_is_scored_neutrally_and_disclosed():
    result = news_analysis_service.assess(
        "Acme Ltd profit rises", publisher="Some Blog", symbol="ACME",
    )
    assert result.components["source_credibility"] == 0.5
    assert any("credibility table" in limit for limit in result.limitations)


def test_every_assessment_carries_its_limitations():
    result = news_analysis_service.assess("Acme Ltd results", symbol="ACME")
    assert len(result.limitations) >= 2
    assert any("headline only" in limit for limit in result.limitations)


# --------------------------------------------------------------------------
# IPO scoring
# --------------------------------------------------------------------------


def _ipo_inputs(**overrides):
    base = {
        "ipo": {
            "industry": "Software", "price_band_low": 475.0,
            "price_band_high": 500.0, "issue_size_cr": 1850.0,
            "fresh_issue_cr": 1200.0, "ofs_cr": 650.0,
        },
        "financials": [
            {"period_label": "FY23", "revenue": 620.0, "ebitda": 118.0,
             "pat": 74.0, "eps": 12.4, "net_worth": 340.0, "total_debt": 40.0,
             "roe": 21.8, "roce": 26.4, "ebitda_margin": 19.0,
             "net_margin": 11.9, "period_end": date(2023, 3, 31)},
            {"period_label": "FY24", "revenue": 812.0, "ebitda": 168.0,
             "pat": 104.0, "eps": 16.9, "net_worth": 455.0, "total_debt": 32.0,
             "roe": 22.9, "roce": 28.1, "ebitda_margin": 20.7,
             "net_margin": 12.8, "period_end": date(2024, 3, 31)},
            {"period_label": "FY25", "revenue": 1090.0, "ebitda": 235.0,
             "pat": 148.0, "eps": 22.6, "net_worth": 620.0, "total_debt": 25.0,
             "roe": 23.9, "roce": 30.2, "ebitda_margin": 21.6,
             "net_margin": 13.6, "period_end": date(2025, 3, 31)},
        ],
        "risk_factors": [],
        "latest_gmp": {"gmp": 145.0, "gmp_pct": 29.0},
        "gmp_history": [{"gmp": 120.0}, {"gmp": 135.0}, {"gmp": 145.0}],
        "subscription": {"qib_times": 4.2, "nii_times": 6.8,
                         "retail_times": 2.1, "total_times": 3.4},
        "peers": [{"pe": 28.0, "ev_ebitda": 18.0, "roe": 19.0},
                  {"pe": 32.0, "ev_ebitda": 20.0, "roe": 21.0}],
    }
    base.update(overrides)
    return base


def test_ipo_assessment_never_returns_a_bare_subscribe():
    assessment = ipo_analysis_service.assess(**_ipo_inputs())
    assert assessment.label not in ("SUBSCRIBE", "AVOID")
    assert assessment.label_reason


def test_high_severity_risks_dominate_the_label():
    assessment = ipo_analysis_service.assess(**_ipo_inputs(risk_factors=[
        {"category": "LEVERAGE", "description": "Debt is 2.4x equity",
         "severity": "HIGH", "quantum": 2.4},
        {"category": "REGULATORY", "description": "Tariff risk",
         "severity": "HIGH"},
    ]))
    assert assessment.label == "High risk"


def test_loss_making_company_is_labelled_speculative_or_high_risk():
    financials = _ipo_inputs()["financials"]
    financials[-1] = {**financials[-1], "pat": -50.0, "roe": -8.0,
                      "roce": -6.0, "net_margin": -4.6, "ebitda_margin": 2.0}
    assessment = ipo_analysis_service.assess(
        **_ipo_inputs(financials=financials)
    )
    assert assessment.label in ("Speculative", "High risk", "Weak research profile")


def test_missing_everything_reports_insufficient_data():
    assessment = ipo_analysis_service.assess(
        ipo={}, financials=[], risk_factors=[], latest_gmp=None,
        gmp_history=None, subscription=None, peers=None,
    )
    assert assessment.label == "Insufficient data"
    assert assessment.data_completeness_pct < 40


def test_gmp_component_is_capped_in_its_influence():
    """A wild GMP must not carry a weak issue to a strong label."""
    weak = _ipo_inputs(
        financials=[{**_ipo_inputs()["financials"][-1], "pat": -80.0,
                     "roe": -12.0, "roce": -9.0, "net_margin": -7.0,
                     "ebitda_margin": 1.0}],
        latest_gmp={"gmp": 400.0, "gmp_pct": 80.0},
        risk_factors=[{"category": "LEVERAGE", "description": "high",
                       "severity": "HIGH"}],
    )
    assessment = ipo_analysis_service.assess(**weak)
    assert assessment.label in ("High risk", "Speculative", "Weak research profile")


def test_valuation_premium_is_computed_against_peers():
    assessment = ipo_analysis_service.assess(**_ipo_inputs())
    valuation = assessment.valuation
    assert valuation["implied_pe"] == pytest.approx(500.0 / 22.6, abs=0.01)
    assert valuation["peer_median_pe"] is not None
    assert valuation["verdict"] in ("Valuation premium", "Valuation discount")


def test_negative_eps_yields_undefined_pe_not_a_huge_number():
    financials = [{**_ipo_inputs()["financials"][-1], "eps": -3.0}]
    assessment = ipo_analysis_service.assess(**_ipo_inputs(financials=financials))
    assert assessment.valuation["implied_pe"] is None
    assert "undefined" in assessment.valuation["pe_note"]


def test_swot_points_carry_evidence():
    assessment = ipo_analysis_service.assess(**_ipo_inputs())
    for bucket in assessment.swot.values():
        for point in bucket:
            assert point["evidence"]


def test_application_simulator_always_shows_a_below_issue_scenario():
    result = ipo_analysis_service.simulate_application(
        capital=200_000, lots=2, lot_size=30, price=500.0, gmp=145.0,
    )
    names = [s["scenario"] for s in result["scenarios"]]
    assert "Lists below issue price" in names
    assert "allotment" in result["allotment_note"].lower()
    assert "unofficial" in result["disclaimer"].lower()


def test_application_simulator_flags_unaffordable_applications():
    result = ipo_analysis_service.simulate_application(
        capital=5_000, lots=2, lot_size=30, price=500.0, gmp=0.0,
    )
    assert result["affordable"] is False


# --------------------------------------------------------------------------
# Historical analogues
# --------------------------------------------------------------------------


def _series(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    index = pd.date_range("2022-01-03", periods=n, freq="B", tz="UTC")
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.013, n)),
                      index=index)
    spread = close * 0.012
    return pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close + spread, "low": close - spread, "close": close,
        "volume": pd.Series(rng.integers(1e5, 3e6, n), index=index),
    })


def test_short_history_refuses_to_produce_statistics():
    result = historical_analogue_service.find(ind.compute_all(_series(80)))
    assert result.sample_sufficient is False
    assert result.statistics == {}


def test_sufficient_history_produces_a_described_sample():
    result = historical_analogue_service.find(ind.compute_all(_series(700)))
    if result.matches_found:
        assert result.statistics["sample_size"] == result.matches_found
        assert 0 <= result.statistics["hit_rate_pct"] <= 100
        assert result.statistics["worst_return_pct"] <= result.statistics["best_return_pct"]
    assert "not a prediction" in result.to_dict()["disclaimer"]


def test_analogues_never_include_the_unresolved_tail():
    """A match must have a full forward window, or it cannot be scored."""
    frame = ind.compute_all(_series(700))
    result = historical_analogue_service.find(frame, horizon_bars=10)
    last_date = frame.index[-1].date()
    for match in result.matches:
        match_date = date.fromisoformat(match.date)
        assert (last_date - match_date).days > 10


def test_limitations_are_always_reported():
    result = historical_analogue_service.find(ind.compute_all(_series(700)))
    assert result.limitations
    assert any("overlap" in limit for limit in result.limitations)
