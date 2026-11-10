"""Comprehensive Unit & Regression Test Suite for Phase 3 Financial NLP Layer."""

from datetime import datetime, timezone, timedelta
import json
import pytest

from backend.alpha.entities import (
    FinancialEntityResolver,
    ResolutionState,
    get_entity_resolver,
)
from backend.alpha.event_classifier import (
    EventPrimaryFamily,
    EventSubtype,
    FinancialEventClassifier,
    get_event_classifier,
)
from backend.alpha.event_extraction import (
    CurrencyUnit,
    DirectionChange,
    FinancialEventExtractor,
    FinancialMetricType,
    TemporalComparison,
    get_event_extractor,
)
from backend.alpha.event_retrieval import (
    CausalHistoricalEventRetriever,
    HistoricalEventRecord,
    get_event_retriever,
)
from backend.alpha.materiality import (
    FinancialMaterialityEngine,
    MaterialityTier,
    get_materiality_engine,
)
from backend.alpha.nlp_pipeline import (
    FinancialEventRepresentation,
    FinancialNLPPipeline,
    get_nlp_pipeline,
)
from backend.alpha.schemas import NewsEvent
from backend.alpha.surprise import (
    ExpectationType,
    NumericalSurpriseEngine,
    PointInTimeExpectation,
    SurpriseCategory,
    get_surprise_engine,
)


# ============================================================================
# 1. Financial Entity Recognition & Disambiguation Tests
# ============================================================================

class TestFinancialEntityResolution:

    @pytest.fixture
    def resolver(self) -> FinancialEntityResolver:
        return get_entity_resolver()

    def test_direct_ticker_matching_with_financial_context(self, resolver: FinancialEntityResolver):
        results = resolver.resolve_text("TCS quarterly results show steady growth in digital services")
        assert len(results) >= 1
        assert results[0].entity_id == "TCS"
        assert results[0].resolution_state == ResolutionState.RESOLVED
        assert results[0].canonical_name == "Tata Consultancy Services Limited"
        assert len(results[0].instruments) > 0

    def test_rejection_of_ticker_collision_stopwords(self, resolver: FinancialEntityResolver):
        # "IT" is an English stopword and should NOT resolve to an entity without company context
        res_it = resolver.resolve_text("Government policy focus on IT infrastructure growth across rural schools")
        assert all(r.entity_id != "IT" for r in res_it)
        assert res_it[0].resolution_state == ResolutionState.UNRESOLVED

        # "AT" is an English preposition
        res_at = resolver.resolve_text("Looking AT the macroeconomic horizon ahead of the annual symposium")
        assert all(r.entity_id != "AT" for r in res_at)

    def test_subsidiary_to_parent_resolution(self, resolver: FinancialEntityResolver):
        res_jio = resolver.resolve_text("Jio Infocomm awards multi-crore telecom equipment deal")
        assert len(res_jio) >= 1
        assert res_jio[0].parent_entity_id == "RELIANCE"

        res_retail = resolver.resolve_text("Reliance Retail announces expansion of digital grocery formats")
        assert len(res_retail) >= 1
        assert res_retail[0].parent_entity_id == "RELIANCE"

    def test_executive_name_cooccurrence(self, resolver: FinancialEntityResolver):
        res_mukesh = resolver.resolve_text("Mukesh Ambani delivers keynote on artificial intelligence")
        assert any(r.entity_id == "RELIANCE" for r in res_mukesh)

        res_tim = resolver.resolve_text("Tim Cook announces new operating system launch in Cupertino")
        assert any(r.entity_id == "AAPL" for r in res_tim)

    def test_explicit_unresolved_state(self, resolver: FinancialEntityResolver):
        res = resolver.resolve_text("A random local cooperative society holds its annual summer picnic")
        assert len(res) == 1
        assert res[0].resolution_state == ResolutionState.UNRESOLVED
        assert res[0].entity_id is None
        assert res[0].confidence == 0.0


# ============================================================================
# 2. Hierarchical Event Classification Tests
# ============================================================================

class TestFinancialEventClassifier:

    @pytest.fixture
    def classifier(self) -> FinancialEventClassifier:
        return get_event_classifier()

    def test_coverage_of_primary_taxonomy_categories(self, classifier: FinancialEventClassifier):
        # 1. Corporate Financial
        c_earn = classifier.classify_text("TCS reports Q3 net profit of Rs 12,434 crore")
        assert c_earn.primary_family == EventPrimaryFamily.CORPORATE_FINANCIAL
        assert c_earn.primary_subtype == EventSubtype.EARNINGS_ANNOUNCEMENT

        # 2. Corporate Operational
        c_ord = classifier.classify_text("L&T bags order worth Rs 2,500 crore from international client")
        assert c_ord.primary_family == EventPrimaryFamily.CORPORATE_OPERATIONAL
        assert c_ord.primary_subtype == EventSubtype.CONTRACT_ORDER_WIN

        # 3. Macroeconomic
        c_rate = classifier.classify_text("RBI Monetary Policy Committee hikes repo rate by 50 bps")
        assert c_rate.primary_family == EventPrimaryFamily.MACROECONOMIC
        assert c_rate.primary_subtype == EventSubtype.INTEREST_RATE_DECISION

        # 4. Regulatory
        c_reg = classifier.classify_text("SEBI order imposes penalty of Rs 25 lakh for insider trading violations")
        assert c_reg.primary_subtype == EventSubtype.REGULATORY_INVESTIGATION

        # 5. Routine Administrative
        c_rout = classifier.classify_text("Trading window closure notice for designated persons")
        assert c_rout.primary_family == EventPrimaryFamily.GENERAL_CORPORATE
        assert c_rout.primary_subtype == EventSubtype.ROUTINE_ADMINISTRATIVE

    def test_multi_label_classification(self, classifier: FinancialEventClassifier):
        c = classifier.classify_text(
            "Company reports quarterly results, declares interim dividend and cuts annual guidance"
        )
        assert c.primary_subtype == EventSubtype.EARNINGS_ANNOUNCEMENT
        assert c.is_multi_label is True
        assert len(c.secondary_subtypes) >= 1
        assert EventSubtype.DIVIDEND_ANNOUNCEMENT in c.secondary_subtypes or EventSubtype.GUIDANCE_REVISION in c.secondary_subtypes

    def test_routine_board_meeting_override(self, classifier: FinancialEventClassifier):
        c = classifier.classify_text(
            "Intimation of Board Meeting to consider financial results and dividend"
        )
        assert c.primary_subtype == EventSubtype.ROUTINE_ADMINISTRATIVE
        assert c.severity_score == 0.10


# ============================================================================
# 3. Structured Numerical Extraction Tests
# ============================================================================

class TestFinancialEventExtractor:

    @pytest.fixture
    def extractor(self) -> FinancialEventExtractor:
        return get_event_extractor()

    def test_indian_crore_and_lakh_conversions(self, extractor: FinancialEventExtractor):
        r_cr = extractor.extract_metrics("L&T wins contract worth Rs 2,500 crore")
        assert r_cr.primary_metric is not None
        assert r_cr.primary_metric.normalized_value == 2.5e10
        assert r_cr.primary_metric.currency == CurrencyUnit.INR

        r_lakh = extractor.extract_metrics("SEBI penalty of Rs 25 lakh on company director")
        assert r_lakh.primary_metric is not None
        assert r_lakh.primary_metric.normalized_value == 2.5e6
        assert r_lakh.primary_metric.currency == CurrencyUnit.INR

    def test_international_million_billion_conversions(self, extractor: FinancialEventExtractor):
        r_bn = extractor.extract_metrics("Apple quarterly revenue reaches $94.9 billion")
        assert r_bn.primary_metric is not None
        assert r_bn.primary_metric.normalized_value == 9.49e10
        assert r_bn.primary_metric.currency == CurrencyUnit.USD

        r_mn = extractor.extract_metrics("Venture fund invests $50 million in fintech startup")
        assert r_mn.primary_metric is not None
        assert r_mn.primary_metric.normalized_value == 5.0e7
        assert r_mn.primary_metric.currency == CurrencyUnit.USD

    def test_numerical_range_extraction(self, extractor: FinancialEventExtractor):
        r_range = extractor.extract_metrics("Capex planned between Rs 500 cr and Rs 600 cr for expansion")
        m = r_range.primary_metric
        assert m is not None
        assert m.is_range is True
        assert m.range_low == 5.0e9
        assert m.range_high == 6.0e9
        assert m.normalized_value == 5.5e9

    def test_parenthesized_accounting_losses(self, extractor: FinancialEventExtractor):
        r_loss = extractor.extract_metrics("Company posts (Rs 120 cr) loss in Q2")
        assert r_loss.primary_metric is not None
        assert r_loss.primary_metric.normalized_value == -1.2e9
        assert r_loss.primary_metric.metric_type == FinancialMetricType.NET_PROFIT_PAT

    def test_percentages_and_basis_points(self, extractor: FinancialEventExtractor):
        r_pct = extractor.extract_metrics("Net profit jumps 15.5% YoY")
        assert any(m.is_percentage and m.normalized_value == 15.5 for m in r_pct.metrics)

        r_bps = extractor.extract_metrics("Operating margin contracts 75 bps")
        assert any(m.is_basis_points and m.normalized_value == 0.75 for m in r_bps.metrics)

    def test_comparison_period_and_direction(self, extractor: FinancialEventExtractor):
        r = extractor.extract_metrics("Revenue falls 8% YoY")
        assert any(
            m.direction == DirectionChange.DECREASE and m.comparison_period == TemporalComparison.YOY
            for m in r.metrics
        )


# ============================================================================
# 4. Numerical Surprise & Consensus Integrity Tests
# ============================================================================

class TestNumericalSurpriseEngine:

    @pytest.fixture
    def engine(self) -> NumericalSurpriseEngine:
        return NumericalSurpriseEngine(in_line_threshold_pct=1.0)

    def test_positive_and_negative_surprise_calculation(self, engine: NumericalSurpriseEngine):
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=3)

        exp = PointInTimeExpectation(
            instrument_id="TCS",
            metric_name="NET_PROFIT_PAT",
            expected_value=12000.0,
            dispersion_std=200.0,
            captured_at=past,
        )

        from backend.alpha.event_extraction import ExtractedMetricValue
        metric_beat = ExtractedMetricValue(
            metric_type=FinancialMetricType.NET_PROFIT_PAT,
            raw_text="12434",
            normalized_value=12434.0,
        )
        res_beat = engine.compute_surprise(metric_beat, "TCS", now, explicit_expectation=exp)
        assert res_beat.surprise_category == SurpriseCategory.POSITIVE_SURPRISE
        assert res_beat.absolute_surprise == 434.0
        assert res_beat.percentage_surprise == 3.617
        assert res_beat.standardised_surprise_z == 2.17

        metric_miss = ExtractedMetricValue(
            metric_type=FinancialMetricType.NET_PROFIT_PAT,
            raw_text="11500",
            normalized_value=11500.0,
        )
        res_miss = engine.compute_surprise(metric_miss, "TCS", now, explicit_expectation=exp)
        assert res_miss.surprise_category == SurpriseCategory.NEGATIVE_SURPRISE
        assert res_miss.absolute_surprise == -500.0
        assert res_miss.percentage_surprise == -4.167

    def test_negative_consensus_expectation_outperformance(self, engine: NumericalSurpriseEngine):
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=2)

        exp_loss = PointInTimeExpectation(
            instrument_id="STARTUP",
            metric_name="NET_PROFIT_PAT",
            expected_value=-100.0,  # Expected loss of 100
            captured_at=past,
        )

        from backend.alpha.event_extraction import ExtractedMetricValue
        metric = ExtractedMetricValue(
            metric_type=FinancialMetricType.NET_PROFIT_PAT,
            raw_text="-80",
            normalized_value=-80.0,  # Actual loss of 80 (beat consensus by 20)
        )
        res = engine.compute_surprise(metric, "STARTUP", now, explicit_expectation=exp_loss)
        assert res.absolute_surprise == 20.0
        assert res.percentage_surprise == 20.0
        assert res.surprise_category == SurpriseCategory.POSITIVE_SURPRISE

    def test_zero_denominator_handling(self, engine: NumericalSurpriseEngine):
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)

        exp_zero = PointInTimeExpectation(
            instrument_id="XYZ",
            metric_name="NET_PROFIT_PAT",
            expected_value=0.0,
            captured_at=past,
        )

        from backend.alpha.event_extraction import ExtractedMetricValue
        metric = ExtractedMetricValue(
            metric_type=FinancialMetricType.NET_PROFIT_PAT,
            raw_text="50",
            normalized_value=50.0,
        )
        res = engine.compute_surprise(metric, "XYZ", now, explicit_expectation=exp_zero)
        assert res.percentage_surprise is None  # Handled safely without ZeroDivisionError
        assert res.surprise_category == SurpriseCategory.POSITIVE_SURPRISE

    def test_missing_expectation_returns_unavailable_expectation(self, engine: NumericalSurpriseEngine):
        now = datetime.now(timezone.utc)
        from backend.alpha.event_extraction import ExtractedMetricValue

        metric = ExtractedMetricValue(
            metric_type=FinancialMetricType.NET_PROFIT_PAT,
            raw_text="5000",
            normalized_value=5000.0,
        )
        res = engine.compute_surprise(metric, "NON_EXISTENT_CO", now)
        assert res.surprise_category == SurpriseCategory.UNAVAILABLE_EXPECTATION
        assert "Recorded as UNAVAILABLE_EXPECTATION" in res.status_notes

    def test_causal_integrity_lookahead_violation(self, engine: NumericalSurpriseEngine):
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=1)

        exp_future = PointInTimeExpectation(
            instrument_id="TCS",
            metric_name="NET_PROFIT_PAT",
            expected_value=12000.0,
            captured_at=future,  # In the future!
        )

        from backend.alpha.event_extraction import ExtractedMetricValue
        metric = ExtractedMetricValue(
            metric_type=FinancialMetricType.NET_PROFIT_PAT,
            raw_text="12434",
            normalized_value=12434.0,
        )
        with pytest.raises(ValueError, match="Causal violation"):
            engine.compute_surprise(metric, "TCS", now, explicit_expectation=exp_future)


# ============================================================================
# 5. Composite Materiality Tests
# ============================================================================

class TestFinancialMaterialityEngine:

    @pytest.fixture
    def engine(self) -> FinancialMaterialityEngine:
        return get_materiality_engine()

    def test_materiality_bounds_and_tier_assignment(self, engine: FinancialMaterialityEngine):
        c = get_event_classifier()
        e = get_event_extractor()

        # High Materiality Event: Central Bank Rate Decision
        cls_macro = c.classify_text("RBI Monetary Policy Committee hikes repo rate by 50 bps")
        ext_macro = e.extract_metrics("RBI Monetary Policy Committee hikes repo rate by 50 bps")
        mat_high = engine.evaluate_materiality(cls_macro, ext_macro)
        assert 0.0 <= mat_high.composite_materiality_score <= 1.0
        assert mat_high.materiality_tier == MaterialityTier.TIER_3_HIGH

        # Routine Event: Trading Window Closure
        cls_rout = c.classify_text("Trading window closure notice for designated employees")
        ext_rout = e.extract_metrics("Trading window closure notice for designated employees")
        mat_low = engine.evaluate_materiality(cls_rout, ext_rout)
        assert mat_low.materiality_tier == MaterialityTier.TIER_1_ROUTINE
        assert mat_low.composite_materiality_score < 0.28

    def test_materiality_distinct_from_sentiment(self, engine: FinancialMaterialityEngine):
        c = get_event_classifier()
        e = get_event_extractor()

        # Negative sentiment event (Regulatory Show Cause / Investigation)
        cls_neg = c.classify_text("SEBI order imposes show cause notice for market manipulation")
        ext_neg = e.extract_metrics("SEBI order imposes show cause notice for market manipulation")
        mat_neg = engine.evaluate_materiality(cls_neg, ext_neg)

        # Despite negative sentiment, materiality must remain HIGH
        assert mat_neg.composite_materiality_score >= 0.50


# ============================================================================
# 6. Causal Historical Event Retrieval Tests
# ============================================================================

class TestCausalHistoricalRetrieval:

    @pytest.fixture
    def retriever(self) -> CausalHistoricalEventRetriever:
        r = CausalHistoricalEventRetriever(top_k=3)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t_future = datetime(2026, 3, 1, tzinfo=timezone.utc)

        r.index_event(
            HistoricalEventRecord(
                event_id="e_past_1",
                instrument_id="LT",
                headline="L&T bags order worth Rs 2000 cr",
                event_subtype="CONTRACT_ORDER_WIN",
                timestamp=t0,
                materiality_score=0.70,
                post_event_return_1d=0.03,
            )
        )
        r.index_event(
            HistoricalEventRecord(
                event_id="e_past_2",
                instrument_id="TCS",
                headline="TCS reports quarterly profit",
                event_subtype="EARNINGS_ANNOUNCEMENT",
                timestamp=t0,
                materiality_score=0.80,
                post_event_return_1d=0.01,
            )
        )
        r.index_event(
            HistoricalEventRecord(
                event_id="e_future",
                instrument_id="LT",
                headline="Future mega contract",
                event_subtype="CONTRACT_ORDER_WIN",
                timestamp=t_future,
                materiality_score=0.90,
            )
        )
        return r

    def test_strict_causal_boundary_no_future_leakage(self, retriever: CausalHistoricalEventRetriever):
        decision_time = datetime(2026, 1, 15, tzinfo=timezone.utc)

        ctx = retriever.retrieve_comparable_events(
            headline="L&T bags contract worth Rs 2500 cr",
            instrument_id="LT",
            event_subtype=EventSubtype.CONTRACT_ORDER_WIN,
            materiality_score=0.72,
            decision_timestamp=decision_time,
        )

        matched_ids = [m.event_id for m in ctx.matches]
        assert "e_past_1" in matched_ids
        assert "e_future" not in matched_ids
        assert ctx.mean_historical_return_1d is not None

    def test_ranking_prefers_same_instrument_and_subtype(self, retriever: CausalHistoricalEventRetriever):
        decision_time = datetime(2026, 1, 15, tzinfo=timezone.utc)

        ctx = retriever.retrieve_comparable_events(
            headline="L&T bags contract worth Rs 2500 cr",
            instrument_id="LT",
            event_subtype=EventSubtype.CONTRACT_ORDER_WIN,
            materiality_score=0.72,
            decision_timestamp=decision_time,
        )
        assert ctx.matches[0].event_id == "e_past_1"
        assert ctx.matches[0].similarity_score > ctx.matches[1].similarity_score


# ============================================================================
# 7. End-to-End Financial NLP Pipeline Tests
# ============================================================================

class TestFinancialNLPPipeline:

    @pytest.fixture
    def pipeline(self) -> FinancialNLPPipeline:
        return get_nlp_pipeline()

    def test_end_to_end_news_event_processing(self, pipeline: FinancialNLPPipeline):
        now = datetime.now(timezone.utc)
        news_evt = NewsEvent(
            provider_id="nse_corporate",
            source_name="NSE Corporate Announcements",
            source_url="https://nsearchives.nseindia.com/corporate/LT_announcement.pdf",
            headline="L&T bags order worth Rs 2,500 crore from Middle East client",
            published_at_utc=now,
            local_received_at_utc=now,
            local_monotonic_ns=1000000,
            content_hash="abc123sha256",
        )

        rep = pipeline.process_news_event(news_evt)

        assert isinstance(rep, FinancialEventRepresentation)
        assert rep.entity_resolution.entity_id == "LT"
        assert rep.classification.primary_subtype == EventSubtype.CONTRACT_ORDER_WIN
        assert rep.extraction.primary_metric is not None
        assert rep.extraction.primary_metric.normalized_value == 2.5e10
        assert rep.materiality.materiality_tier == MaterialityTier.TIER_3_HIGH
        assert 0.0 < rep.composite_confidence <= 1.0

        # Latency must be < 10,000 us (10ms)
        assert rep.execution_latency_us < 10000

    def test_serialization_roundtrip(self, pipeline: FinancialNLPPipeline):
        rep = pipeline.process_raw_text(
            "TCS reports Q3 net profit of Rs 12,434 crore",
            decision_timestamp=datetime.now(timezone.utc),
        )

        # JSON Serialization
        json_str = rep.to_json()
        assert isinstance(json_str, str)
        parsed_dict = json.loads(json_str)
        assert parsed_dict["entity_resolution"]["entity_id"] == "TCS"
        assert parsed_dict["classification"]["primary_subtype"] == "EARNINGS_ANNOUNCEMENT"

        # Model re-parsing
        rep_reconstructed = FinancialEventRepresentation.model_validate(parsed_dict)
        assert rep_reconstructed.event_id == rep.event_id
        assert rep_reconstructed.composite_confidence == rep.composite_confidence
