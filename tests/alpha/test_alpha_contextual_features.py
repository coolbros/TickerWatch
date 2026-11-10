"""Unit tests for Phase 4 Contextual Feature Synthesizer (Work Package F)."""

from datetime import datetime, timedelta, timezone
import pytest

from backend.alpha.contextual_features import (
    ContextualFeatureSynthesizer,
    EvidenceStatus,
)
from backend.alpha.event_retrieval import (
    EventRetrievalContext,
    RetrievalScoreBreakdown,
    RetrievedHistoricalMatch,
)
from backend.alpha.historical_store import StoredOutcomeRecord


def _make_match(
    event_id: str,
    decision_ts: datetime,
    days_ago: int,
    ret_1d: float,
    bench_adj_1d: float,
    sim: float = 0.85,
    inst_match: float = 1.0,
    sector_match: float = 1.0,
    mat: float = 0.75,
    surprise_align: float = 0.5,
    quality_status: str = "VERIFIED",
) -> RetrievedHistoricalMatch:
    ts = decision_ts - timedelta(days=days_ago)
    start_ts = ts
    end_ts = ts + timedelta(days=1)
    rec = StoredOutcomeRecord(
        event_id=event_id,
        instrument_id="NSE:LT:EQ",
        horizon_label="1d",
        outcome_start_ts=start_ts,
        outcome_end_ts=end_ts,
        return_simple=ret_1d,
        benchmark_adjusted_return=bench_adj_1d,
        realized_volatility=0.012,
        data_quality_status=quality_status,
    )
    breakdown = RetrievalScoreBreakdown(
        combined_score=sim,
        lexical_jaccard=0.3,
        lexical_bm25_norm=0.5,
        semantic_cosine=0.8,
        event_subtype_match=1.0,
        instrument_match=inst_match,
        surprise_alignment=surprise_align,
        materiality_proximity=0.9,
        temporal_proximity=0.8,
        sector_match=sector_match,
    )
    return RetrievedHistoricalMatch(
        event_id=event_id,
        instrument_id="NSE:LT:EQ",
        headline=f"L&T wins order {event_id}",
        event_subtype="MAJOR_CONTRACT_WIN",
        timestamp=ts,
        similarity_score=sim,
        materiality_score=mat,
        post_event_return_1d=ret_1d,
        is_1d_outcome_matured=(end_ts <= decision_ts),
        days_prior_to_decision=float(days_ago),
        score_breakdown=breakdown,
        instrument_match=inst_match,
        available_outcome_horizons=["1d"],
        matured_outcomes={"1d": rec},
    )


def test_contextual_features_sufficient_distribution_and_slices():
    decision_ts = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    matches = [
        _make_match("e1", decision_ts, 10, 0.03, 0.025, sim=0.92, surprise_align=1.0),
        _make_match("e2", decision_ts, 20, 0.02, 0.015, sim=0.88, surprise_align=1.0),
        _make_match("e3", decision_ts, 30, 0.01, 0.008, sim=0.80, surprise_align=1.0),
        _make_match("e4", decision_ts, 40, -0.01, -0.012, sim=0.75, inst_match=0.4, surprise_align=1.0),
        _make_match("e5", decision_ts, 50, 0.05, 0.044, sim=0.95, surprise_align=1.0),
        _make_match("e6", decision_ts, 60, 0.02, 0.018, sim=0.82, surprise_align=1.0),
    ]
    ctx = EventRetrievalContext(
        query_event_id="q_lt_order",
        decision_timestamp=decision_ts,
        matches=matches,
        total_comparable_count=12,
    )

    synth = ContextualFeatureSynthesizer(min_sample_size=5, bootstrap_seed=7)
    feats = synth.synthesize(ctx, query_materiality=0.75, query_surprise_category="POSITIVE_SURPRISE")

    assert feats.overall_evidence_status == EvidenceStatus.SUFFICIENT
    assert feats.insufficient_evidence_flag is False
    assert feats.recency_days_latest == 10.0
    assert feats.recency_days_median == 35.0

    d1 = feats.horizons["1d"]
    assert d1.evidence_status == EvidenceStatus.SUFFICIENT
    assert d1.matured_count == 6
    assert d1.mean_return == pytest.approx(0.02, abs=1e-5)
    assert d1.positive_frequency == pytest.approx(5 / 6, abs=1e-4)
    assert d1.negative_frequency == pytest.approx(1 / 6, abs=1e-4)
    assert d1.quantiles is not None and d1.quantiles.p50 == pytest.approx(0.02, abs=1e-5)
    assert d1.bootstrap_ci_95 is not None and d1.bootstrap_ci_95.lower < d1.mean_return < d1.bootstrap_ci_95.upper
    assert d1.mean_benchmark_adjusted_return == pytest.approx(0.098 / 6, abs=1e-4)
    # Unobserved 5m horizon has 0 matured outcomes
    assert feats.horizons["5m"].evidence_status == EvidenceStatus.NO_MATCHED_OUTCOMES

    # Conditional slices
    assert feats.conditional_slices["INSTRUMENT_SPECIFIC_1d"].evidence_status == EvidenceStatus.SUFFICIENT
    assert feats.conditional_slices["INSTRUMENT_SPECIFIC_1d"].matured_count == 5
    assert feats.conditional_slices["SURPRISE_MATCHED_1d"].evidence_status == EvidenceStatus.SUFFICIENT


def test_contextual_features_gates_small_sample_and_missing_expectations():
    decision_ts = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    # Only 3 valid matured matches + 1 corrupted ROLLOVER_INVALID that must be excluded
    matches = [
        _make_match("s1", decision_ts, 5, 0.04, 0.03),
        _make_match("s2", decision_ts, 12, 0.02, 0.01),
        _make_match("s3", decision_ts, 20, -0.01, -0.02),
        _make_match("s4_bad", decision_ts, 25, 0.99, 0.99, quality_status="ROLLOVER_INVALID"),
    ]
    ctx = EventRetrievalContext(
        query_event_id="q_small",
        decision_timestamp=decision_ts,
        matches=matches,
        total_comparable_count=4,
    )
    synth = ContextualFeatureSynthesizer(min_sample_size=5)
    feats = synth.synthesize(ctx, query_surprise_category="UNAVAILABLE_EXPECTATION")

    assert feats.insufficient_evidence_flag is True
    assert feats.overall_evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE
    d1 = feats.horizons["1d"]
    assert d1.matured_count == 3  # ROLLOVER_INVALID filtered out!
    assert d1.insufficient_evidence is True
    assert d1.evidence_status == EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert d1.mean_return is None
    assert d1.quantiles is None
    assert d1.bootstrap_ci_95 is None
    assert feats.conditional_slices["SURPRISE_MATCHED_1d"].evidence_status == EvidenceStatus.EXPECTATION_UNAVAILABLE
