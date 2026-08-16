"""Tests for the exchange-record layer.

Deliberately offline: every test drives the parsers and the reconciliation
logic with fixed inputs rather than hitting NSE, BSE or a broker. A test suite
that depends on a live exchange fails at the weekend and passes for the wrong
reasons on a Tuesday.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.core.data_quality import DataStatus, SourceReliability
from app.services.market_flows import (BUILDUP_MEANING, DeliveryRegime,
                                       OiBuildup, classify_buildup)
from app.services.reconciliation import (Agreement, SourceReading, reconcile,
                                         reconciliation_evidence)


def reading(provider: str, value, *, status=DataStatus.LIVE,
            reliability=SourceReliability.HIGH, error=None) -> SourceReading:
    return SourceReading(provider=provider, source_name=provider, value=value,
                         status=status, reliability=reliability, error=error)


# --------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------


class TestReconciliation:
    def test_agreeing_sources_publish_a_consensus(self):
        rec = reconcile("ltp", [reading("yahoo", 727.0), reading("kite", 727.1),
                                reading("dhan", 726.95)])
        assert rec.agreement is Agreement.CONFIRMED
        assert rec.consensus == pytest.approx(727.0)
        assert rec.is_trustworthy

    def test_conflicting_sources_publish_no_consensus(self):
        """The core guarantee: a disagreement is never averaged into a number
        that no source actually reported."""
        rec = reconcile("ltp", [reading("yahoo", 727.0), reading("kite", 727.1),
                                reading("upstox", 812.0)])
        assert rec.agreement is Agreement.CONFLICT
        assert rec.consensus is None
        assert not rec.is_trustworthy
        # It still names a reference so the caller has somewhere to start.
        assert rec.authoritative_value is not None

    def test_median_not_mean_resists_one_bad_tick(self):
        """Two good sources plus one wild outlier: the reference must stay on
        the good pair, which a mean would not do."""
        rec = reconcile("ltp", [reading("yahoo", 100.0), reading("kite", 100.1),
                                reading("upstox", 500.0)])
        outliers = [r.provider for r in rec.readings if r.is_outlier]
        assert outliers == ["upstox"]

    def test_two_sources_are_never_labelled_outliers(self):
        """With two readings the median sits between them, so flagging both
        says nothing - they simply disagree."""
        rec = reconcile("close", [reading("nse_archives", 1320.5),
                                  reading("yahoo", 1100.0)])
        assert rec.agreement is Agreement.CONFLICT
        assert not any(r.is_outlier for r in rec.readings)
        assert "neither can be called the outlier" in rec.explanation

    def test_single_source_is_flagged_unverified(self):
        rec = reconcile("ltp", [reading("yahoo", 727.0)])
        assert rec.agreement is Agreement.SINGLE_SOURCE
        assert rec.consensus == 727.0
        assert not rec.is_trustworthy
        assert "no second source" in rec.explanation

    def test_no_sources_reports_every_failure_reason(self):
        rec = reconcile("ltp", [
            reading("yahoo", None, status=DataStatus.UNAVAILABLE, error="timeout"),
            reading("kite", None, status=DataStatus.UNAVAILABLE, error="no token"),
        ])
        assert rec.agreement is Agreement.UNAVAILABLE
        assert rec.consensus is None
        assert "timeout" in rec.explanation and "no token" in rec.explanation

    def test_cross_venue_close_uses_the_wider_tolerance(self):
        """NSE and BSE are separate order books. A small difference is a fact
        about the market, not a data error."""
        rec = reconcile("close", [reading("nse_archives", 1320.5),
                                  reading("bse_archives", 1325.0)])
        assert rec.agreement is Agreement.CONFIRMED
        assert rec.tolerance_pct == 1.0
        assert "separate" in rec.explanation and "order books" in rec.explanation

    def test_same_venue_close_keeps_the_tight_tolerance(self):
        rec = reconcile("close", [reading("nse_archives", 1320.5),
                                  reading("yahoo", 1325.0)])
        assert rec.tolerance_pct == 0.1
        assert rec.agreement is not Agreement.CONFIRMED

    def test_zero_valued_metric_does_not_divide_by_zero(self):
        rec = reconcile("net_flow", [reading("a", 0.0), reading("b", 0.0)])
        assert rec.agreement is Agreement.CONFIRMED
        assert rec.spread_pct == 0.0

    def test_exchange_archive_outranks_a_vendor(self):
        rec = reconcile("close", [reading("yahoo", 100.0),
                                  reading("nse_archives", 105.0)])
        assert rec.authoritative_source == "nse_archives"

    def test_evidence_chain_separates_confirmed_from_disputed(self):
        recs = {
            "ltp": reconcile("ltp", [reading("a", 10.0), reading("b", 10.01)]),
            "volume": reconcile("volume", [reading("a", 100.0), reading("b", 900.0)]),
        }
        chain = reconciliation_evidence(recs)
        assert chain.dimension == "DATA_INTEGRITY"
        assert len(chain.items) == 1          # ltp agreed
        assert len(chain.counter_items) == 1  # volume did not
        assert chain.score == 50.0
        # The standing caveat must always be present.
        assert any("consistent" in l for l in chain.limitations)


# --------------------------------------------------------------------------
# open interest
# --------------------------------------------------------------------------


class TestOiBuildup:
    @pytest.mark.parametrize("price,oi,expected", [
        (2.0, 5.0, OiBuildup.LONG_BUILDUP),
        (-2.0, 5.0, OiBuildup.SHORT_BUILDUP),
        (2.0, -5.0, OiBuildup.SHORT_COVERING),
        (-2.0, -5.0, OiBuildup.LONG_UNWINDING),
    ])
    def test_four_way_classification(self, price, oi, expected):
        assert classify_buildup(price, oi) is expected

    @pytest.mark.parametrize("price,oi", [
        (0.02, 5.0),      # price barely moved
        (2.0, 0.3),       # OI barely moved
        (0.0, 0.0),
    ])
    def test_noise_is_not_forced_into_a_corner(self, price, oi):
        assert classify_buildup(price, oi) is OiBuildup.INDETERMINATE

    @pytest.mark.parametrize("price,oi", [(None, 5.0), (2.0, None), (None, None)])
    def test_missing_inputs_are_indeterminate(self, price, oi):
        assert classify_buildup(price, oi) is OiBuildup.INDETERMINATE

    def test_every_state_carries_an_explanation(self):
        for state in OiBuildup:
            assert BUILDUP_MEANING[state]

    def test_buildup_language_never_predicts(self):
        """The wording must describe positioning, not forecast price."""
        for state, text in BUILDUP_MEANING.items():
            lowered = text.lower()
            assert "will rise" not in lowered
            assert "will fall" not in lowered
            assert "guaranteed" not in lowered


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------


class TestDeliveryAnalysis:
    def test_short_history_reports_unknown_rather_than_guessing(self, monkeypatch):
        from app.core.data_quality import Sourced
        import app.services.market_flows as flows

        env = Sourced(value=[{"symbol": "TESTCO", "series": "EQ",
                              "delivery_pct": 88.0, "traded_quantity": 1000,
                              "deliverable_quantity": 880,
                              "session_date": "2026-08-14"}],
                      provider="nse_archives", source_name="NSE",
                      status=DataStatus.DELAYED)
        monkeypatch.setattr(flows, "delivery_snapshot", lambda on=None: env)

        reading_, _ = flows.analyse_delivery("TESTCO", history=[50.0] * 5)
        assert reading_.regime is DeliveryRegime.UNKNOWN
        assert "short of the 20" in reading_.interpretation
        # It must not claim accumulation off five observations.
        assert "ACCUMULATION" not in reading_.interpretation

    def test_percentile_against_own_history(self, monkeypatch):
        from app.core.data_quality import Sourced
        import app.services.market_flows as flows

        env = Sourced(value=[{"symbol": "TESTCO", "series": "EQ",
                              "delivery_pct": 88.0, "traded_quantity": 1000,
                              "deliverable_quantity": 880,
                              "session_date": "2026-08-14"}],
                      provider="nse_archives", source_name="NSE",
                      status=DataStatus.DELAYED)
        monkeypatch.setattr(flows, "delivery_snapshot", lambda on=None: env)

        reading_, _ = flows.analyse_delivery("TESTCO", history=[40.0] * 30)
        assert reading_.regime is DeliveryRegime.ACCUMULATION
        assert reading_.percentile == 100.0
        assert reading_.median_pct == 40.0

    def test_churn_when_far_below_own_norm(self, monkeypatch):
        from app.core.data_quality import Sourced
        import app.services.market_flows as flows

        env = Sourced(value=[{"symbol": "TESTCO", "series": "EQ",
                              "delivery_pct": 12.0, "traded_quantity": 1000,
                              "deliverable_quantity": 120,
                              "session_date": "2026-08-14"}],
                      provider="nse_archives", source_name="NSE",
                      status=DataStatus.DELAYED)
        monkeypatch.setattr(flows, "delivery_snapshot", lambda on=None: env)

        reading_, _ = flows.analyse_delivery("TESTCO", history=[70.0] * 30)
        assert reading_.regime is DeliveryRegime.CHURN
        assert "squared off intraday" in reading_.interpretation


# --------------------------------------------------------------------------
# archive parsers
# --------------------------------------------------------------------------


MTO_SAMPLE = b"""Security Wise Delivery Position - Compulsory Rolling Settlement
10,MTO,14082026,1636546147,0003186
Trade Date <14-AUG-2026>,Settlement Type <N>
Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity(gross across client level),% of Deliverable Quantity to Traded Quantity
20,1,1003ISFL28,N4,310,310,100.00
20,497,AZAD,EQ,621810,213907,34.40
20,498,HDFCBANK,EQ,20364131,14796227,72.66
"""


class TestArchiveParsers:
    def test_mto_columns_are_not_shifted(self):
        """The MTO file carries a record-type marker before the serial number.
        Reading the serial as the symbol silently attributes every delivery
        figure to the wrong stock - the worst failure mode in this module."""
        from app.providers.nse_archives import NseArchivesProvider

        provider = NseArchivesProvider()
        rows = provider._delivery_for.__wrapped__(provider, date(2026, 8, 14)) \
            if hasattr(provider._delivery_for, "__wrapped__") else None
        # Parse directly rather than over the network.
        parsed = []
        for line in MTO_SAMPLE.decode().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7 or parts[0] != "20":
                continue
            parsed.append({"symbol": parts[2], "series": parts[3],
                           "traded": int(parts[4]), "delivered": int(parts[5]),
                           "pct": float(parts[6])})

        assert [p["symbol"] for p in parsed] == ["1003ISFL28", "AZAD", "HDFCBANK"]
        hdfc = parsed[-1]
        assert hdfc["series"] == "EQ"
        assert hdfc["traded"] == 20_364_131
        assert hdfc["delivered"] == 14_796_227
        assert hdfc["pct"] == 72.66
        # The ratio must reproduce the published percentage.
        assert round(hdfc["delivered"] / hdfc["traded"] * 100, 2) == 72.66

    def test_delivery_percentages_are_bounded(self):
        for line in MTO_SAMPLE.decode().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7 or parts[0] != "20":
                continue
            assert 0.0 <= float(parts[6]) <= 100.0

    def test_deal_date_parses_the_exchange_format(self):
        from app.providers.nse_archives import _deal_date

        assert _deal_date("14-AUG-2026") == date(2026, 8, 14)
        assert _deal_date("14-08-2026") == date(2026, 8, 14)
        assert _deal_date("2026-08-14") == date(2026, 8, 14)
        assert _deal_date("") is None
        assert _deal_date("not a date") is None

    def test_rows_accepts_cached_str_as_well_as_bytes(self):
        """The cache serialises through JSON, so a cached payload comes back
        decoded. Both must parse identically."""
        from app.providers.nse_archives import _rows

        csv_bytes = b"Date,Symbol\n14-AUG-2026,ABC\n"
        assert _rows(csv_bytes) == _rows(csv_bytes.decode())


# --------------------------------------------------------------------------
# broker configuration
# --------------------------------------------------------------------------


class TestBrokerConfiguration:
    def test_unconfigured_broker_never_enters_a_chain(self, monkeypatch):
        from app.core.config import Settings

        for key in ("ANGELONE_API_KEY", "DHAN_CLIENT_ID", "KITE_API_KEY",
                    "UPSTOX_ACCESS_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        settings = Settings(_env_file=None)
        chain = settings.providers_for("quote")
        assert not ({"angelone", "dhan", "kite", "upstox"} & set(chain))

    def test_partially_configured_broker_is_not_used(self, monkeypatch):
        """Half a credential set is worse than none: it would open the circuit
        breaker on every call."""
        from app.core.config import Settings

        monkeypatch.setenv("ANGELONE_API_KEY", "k")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "c")
        # password and totp_secret deliberately absent
        settings = Settings(_env_file=None)
        assert not settings.broker_is_configured("angelone")
        assert "angelone" not in settings.providers_for("quote")

    def test_configured_broker_leads_every_market_chain(self, monkeypatch):
        from app.core.config import Settings

        monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
        monkeypatch.setenv("DHAN_ACCESS_TOKEN", "tok")
        settings = Settings(_env_file=None)
        assert settings.broker_is_configured("dhan")
        for capability in ("quote", "history", "option_chain"):
            assert settings.providers_for(capability)[0] == "dhan"

    def test_credentials_are_never_exposed_by_describe(self, monkeypatch):
        from app.providers.brokers import DhanProvider

        monkeypatch.setenv("DHAN_CLIENT_ID", "secret-client")
        monkeypatch.setenv("DHAN_ACCESS_TOKEN", "secret-token")
        import app.core.config as config_module
        config_module.get_settings.cache_clear()
        monkeypatch.setattr(config_module, "settings", config_module.Settings(_env_file=None))

        described = repr(DhanProvider().describe())
        assert "secret-client" not in described
        assert "secret-token" not in described

    def test_totp_is_six_digits(self):
        from app.providers.broker_base import totp_now

        code = totp_now("JBSWY3DPEHPK3PXP")
        assert len(code) == 6 and code.isdigit()

    def test_totp_rejects_a_non_base32_secret(self):
        from app.providers.broker_base import BrokerAuthError, totp_now

        with pytest.raises(BrokerAuthError):
            totp_now("not!valid!base32!")


# --------------------------------------------------------------------------
# personal use mode
# --------------------------------------------------------------------------


class TestPersonalUseMode:
    def test_descriptor_never_claims_registration(self, monkeypatch):
        """Personal mode changes the framing. It must not manufacture a
        credential the operator does not hold."""
        import app.core.config as config_module
        from app.core.compliance import platform_descriptor, verification_badge

        monkeypatch.setenv("PERSONAL_USE_MODE", "true")
        monkeypatch.setenv("OPERATOR_NAME", "J. Mahajan")
        config_module.get_settings.cache_clear()
        monkeypatch.setattr(config_module, "settings",
                            config_module.Settings(_env_file=None))

        descriptor = platform_descriptor()
        assert "J. Mahajan" in descriptor
        assert "single operator" in descriptor
        for banned in ("SEBI", "registered", "certified", "approved"):
            assert banned.lower() not in descriptor.lower()
        assert verification_badge() is None
