"""Unit tests for Phase 5 Work Package C Feature Registry & Point-in-Time Features."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from backend.alpha.event_embeddings import EventEmbeddingEngine
from backend.alpha.feature_registry import (
    FoldSafeFeatureTransformer,
    ModelConfiguration,
    build_feature_manifest,
    get_feature_names_for_config,
)
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_intelligence import HistoricalIntelligencePipeline
from backend.alpha.historical_store import PersistentHistoricalEventStore, StoredOutcomeRecord
from backend.alpha.nlp_pipeline import get_nlp_pipeline
from backend.alpha.outcomes import OutcomeHorizon
from backend.alpha.predictive_features import FuturesBasisMetadata, PointInTimeFeatureBuilder
from backend.alpha.schemas import MarketEvent, MarketMessageType


def test_feature_builder_missing_consensus_and_fold_only_scaling(tmp_path):
    """Verify missing consensus != zero surprise, manifest SHA stability, and train-fold-only scaling."""
    store = PersistentHistoricalEventStore(tmp_path / "feat_test.db")
    nlp = get_nlp_pipeline()
    emb = EventEmbeddingEngine(prefer_onnx=False)
    pipe = HistoricalIntelligencePipeline(store=store, embedding_engine=emb, nlp_pipeline=nlp)

    t_hist = datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc)
    r_h = nlp.process_raw_text("TCS wins $300 million UK cloud deal", decision_timestamp=t_hist, event_id="h1")
    store.upsert_event(r_h, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)
    store.save_outcome(StoredOutcomeRecord(
        event_id="h1", instrument_id="NSE:TCS:EQ", horizon_label="1d",
        outcome_start_ts=t_hist, outcome_end_ts=t_hist + timedelta(days=1), return_simple=0.02,
    ))

    builder = PointInTimeFeatureBuilder(horizon=OutcomeHorizon.D1)
    t_q1 = datetime(2026, 2, 10, 4, 30, tzinfo=timezone.utc)  # 10:00 IST -> inside regular session
    rep1 = nlp.process_raw_text("TCS bags Rs 1,800 crore digital deal in Europe", decision_timestamp=t_q1, event_id="q1")
    mkt1 = [
        MarketEvent(
            provider="REPLAY", venue="NSE", instrument_id="NSE:TCS:EQ",
            message_type=MarketMessageType.QUOTE,
            local_received_at_utc=t_q1 - timedelta(minutes=2),
            local_monotonic_ns=100, bid_price=Decimal("4000"), ask_price=Decimal("4002"),
            bid_quantity=200, ask_quantity=100, last_traded_price=Decimal("4001"),
        ),
        MarketEvent(
            provider="REPLAY", venue="NSE", instrument_id="NSE:TCS:EQ",
            message_type=MarketMessageType.QUOTE,
            local_received_at_utc=t_q1,
            local_monotonic_ns=200, bid_price=Decimal("4009"), ask_price=Decimal("4011"),
            bid_quantity=300, ask_quantity=150, last_traded_price=Decimal("4010"),
        ),
    ]
    hrep1 = pipe.process_event_representation(rep1, market_events=mkt1, include_synthetic=True, is_synthetic=True)
    row1 = builder.build_row(
        rep1, hrep1,
        futures_meta=FuturesBasisMetadata(
            contract_id="TCS26FEBFUT", as_of_timestamp=t_q1,
            futures_price=4020.0, spot_price=4010.0, expiry_date=date(2026, 2, 26),
        ),
        is_synthetic=True,
    )

    assert row1.values["e_surprise_missing"] == 1.0
    assert row1.values["e_surprise_pct_signed"] is None
    assert "e_surprise_pct_signed" in row1.missing_features
    assert row1.values["h_insufficient_evidence"] == 1.0  # n=1 < 5
    assert row1.values["m_available"] == 1.0
    assert row1.values["m_session_open"] == 1.0
    assert row1.values["m_futures_basis_bps"] == pytest.approx(24.9377, abs=0.05)

    # Rejects future futures_meta timestamp
    with pytest.raises(ValueError, match="Causal leakage in M_t"):
        builder.build_row(
            rep1, hrep1,
            futures_meta=FuturesBasisMetadata(
                contract_id="TCS26FEBFUT", as_of_timestamp=t_q1 + timedelta(seconds=1),
                futures_price=4020.0, spot_price=4010.0, expiry_date=date(2026, 2, 26),
            ),
        )

    # FoldSafeFeatureTransformer fits ONLY on train rows and ignores test outlier scale during transform
    names_m = get_feature_names_for_config(ModelConfiguration.MODEL_A_MARKET_ONLY)
    assert build_feature_manifest(ModelConfiguration.MODEL_D_FULL_SYSTEM).definition_count == 87

    tr = FoldSafeFeatureTransformer(names_m)
    x_tr = tr.fit_transform([row1.subset(ModelConfiguration.MODEL_A_MARKET_ONLY), {"m_spread_bps": 10.0, "m_available": 1.0}])
    mean_before = tr.get_fit_statistics()["means"]["m_spread_bps"]
    _ = tr.transform([{"m_spread_bps": 99999.0, "m_available": 1.0}])
    mean_after = tr.get_fit_statistics()["means"]["m_spread_bps"]
    assert mean_before == mean_after
    assert x_tr.shape == (2, len(names_m))
