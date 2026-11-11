"""Phase 6 Offline Test Suite Part 2: Pipeline, Ledger, Execution, Eligibility, Parity & Monitoring (WP-K)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
import numpy as np
import pytest

from backend.alpha.event_embeddings import EventEmbeddingEngine
from backend.alpha.feature_registry import ModelConfiguration, get_feature_names_for_config
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.live_data_capture import LiveCaptureStore
from backend.alpha.live_replay_parity import LiveReplayParityVerifier
from backend.alpha.live_research_pipeline import LiveResearchPipeline, LoadedShadowModel
from backend.alpha.model_registry import LocalModelRegistry, compute_dataset_manifest_hash
from backend.alpha.outcomes import OutcomeHorizon
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_models import ModelAlgorithm, TabularEventPredictor
from backend.alpha.prediction_uncertainty import CalibrationMethod, UncertaintyCalibratorAndGate
from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType, NewsEvent
from backend.alpha.shadow_execution import (
    FillQualitySource,
    ShadowExecutionConfig,
    ShadowExecutionSimulator,
    ShadowOrderType,
    SyntheticBarOHLCV,
)
from backend.alpha.shadow_ledger import (
    ModelValidationStatus,
    ShadowLedgerEntry,
    ShadowOutcomeRecord,
    ShadowSignalLedger,
)
from backend.alpha.shadow_monitoring import ShadowOperationalMonitor
from backend.alpha.shadow_validation import ModelEligibilityAuditor, ShadowValidationEvaluator
from scripts.alpha_shadow_report import main as shadow_report_cli_main


def _register_test_model(
    reg: LocalModelRegistry,
    model_id: str,
    is_synthetic: bool,
    leakage_passed: bool = True,
) -> None:
    feat_names = get_feature_names_for_config(ModelConfiguration.MODEL_D_FULL_SYSTEM)
    t0 = datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc)
    rows = []
    yc, yp, yn = [], [], []
    rng = np.random.default_rng(7)
    for i in range(30):
        vals = {k: 0.0 for k in feat_names}
        vals["e_cls_confidence"] = 0.85
        vals["e_entity_resolved"] = 1.0
        vals["e_entity_confidence"] = 0.90
        vals["h_insufficient_evidence"] = 0.0
        vals["m_available"] = 1.0
        z = float(rng.normal(0.0, 1.0))
        vals["e_surprise_z"] = z
        ret = 0.01 * z + float(rng.normal(0.0, 0.002))
        rows.append(
            PointInTimeFeatureRow(
                event_id=f"tr_{i}", instrument_id="NSE:TCS:EQ", jurisdiction=MarketJurisdiction.IN,
                decision_timestamp=t0 + timedelta(hours=i), horizon_label="1d", is_synthetic=is_synthetic, values=vals,
            )
        )
        yc.append(ret)
        yp.append(1 if ret > 0.001 else 0)
        yn.append(1 if ret < -0.001 else 0)

    pred = TabularEventPredictor(ModelAlgorithm.RIDGE, ModelConfiguration.MODEL_D_FULL_SYSTEM, seed=42).fit(
        [r.subset(ModelConfiguration.MODEL_D_FULL_SYSTEM) for r in rows[:20]], yc[:20], yp[:20], yn[:20]
    )
    cal = UncertaintyCalibratorAndGate(CalibrationMethod.NONE, ModelConfiguration.MODEL_D_FULL_SYSTEM)
    dhash = compute_dataset_manifest_hash([r.event_id for r in rows], [r.decision_timestamp for r in rows], is_synthetic)
    reg.register_run(
        model_id=model_id,
        model_version="6.0.0-alpha",
        model_config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
        horizon_label="1d",
        predictor=pred,
        calibrator=cal,
        dataset_manifest_hash=dhash,
        training_cutoff_utc=rows[-1].decision_timestamp,
        validation_period="2026-06-01..2026-06-02",
        test_period="2026-06-02..2026-06-03",
        train_metrics={"rmse": 0.002},
        oos_metrics={"rmse": 0.003},
        calibration_summary={"ece": 0.05},
        leakage_audit_passed=leakage_passed,
        is_synthetic=is_synthetic,
        known_limitations=["Test suite artifact."],
    )


def test_13_14_model_eligibility_and_synthetic_quarantine(tmp_path: Path) -> None:
    reg = LocalModelRegistry(tmp_path / "reg")
    _register_test_model(reg, "synth_m", is_synthetic=True, leakage_passed=True)
    _register_test_model(reg, "real_unapproved", is_synthetic=False, leakage_passed=True)
    _register_test_model(reg, "real_leaked", is_synthetic=False, leakage_passed=False)

    # Synthetic model can NEVER become eligible even with explicit human approval flag!
    res_syn = ModelEligibilityAuditor.evaluate_registry_model(reg, "synth_m", explicit_human_approval=True)
    assert res_syn.eligible_for_real_shadow is False
    assert res_syn.validation_status == ModelValidationStatus.SYNTHETIC_DIAGNOSTIC_ONLY

    # Leaked real model rejected cleanly
    res_leak = ModelEligibilityAuditor.evaluate_registry_model(reg, "real_leaked", explicit_human_approval=True)
    assert res_leak.eligible_for_real_shadow is False
    assert "LEAKAGE_AUDIT_FAILED" in res_leak.failed_checks

    # Clean real model requires explicit approval -> becomes eligible only when approved
    res_no_app = ModelEligibilityAuditor.evaluate_registry_model(reg, "real_unapproved", explicit_human_approval=False)
    assert res_no_app.eligible_for_real_shadow is False and res_no_app.validation_status == ModelValidationStatus.MODEL_UNVALIDATED

    res_ok = ModelEligibilityAuditor.evaluate_registry_model(reg, "real_unapproved", explicit_human_approval=True)
    assert res_ok.eligible_for_real_shadow is True and res_ok.validation_status == ModelValidationStatus.MODEL_ELIGIBLE

    # LoadedShadowModel quarantines synthetic unless allow_diagnostic_synthetic=True
    lm_q = LoadedShadowModel.from_registry(reg, "synth_m", approved_for_real_shadow=True, allow_diagnostic_synthetic=False)
    assert lm_q.predictor is None and lm_q.validation_status == ModelValidationStatus.SYNTHETIC_DIAGNOSTIC_ONLY


def test_15_shadow_ledger_immutability_and_digest(tmp_path: Path) -> None:
    ledger = ShadowSignalLedger(tmp_path / "ledger.db")
    entry = ledger.append_prediction(
        ShadowLedgerEntry(
            event_id="ev_imm", instrument_id="NSE:TCS:EQ", news_source="test",
            decision_timestamp_utc=datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc),
            event_representation={"x": 1}, historical_intelligence_representation={}, market_regime_representation={},
            feature_manifest_version="5.0.0-alpha", feature_manifest_sha256="sha",
            model_id="m", model_version="1", model_training_dataset_hash="dh",
            model_validation_status=ModelValidationStatus.MODEL_UNVALIDATED, forecast_horizon="1d",
        )
    )
    assert ledger.verify_integrity(entry.prediction_id) is True

    # SQLite triggers block UPDATE and DELETE on both predictions and outcomes
    with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_SHADOW_LEDGER"):
        with ledger._connect() as conn:
            conn.execute("UPDATE shadow_predictions SET action_state='LONG'")
    with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_SHADOW_LEDGER"):
        with ledger._connect() as conn:
            conn.execute("DELETE FROM shadow_predictions")

    out = ledger.append_outcome(
        ShadowOutcomeRecord(prediction_id=entry.prediction_id, event_id="ev_imm", instrument_id="NSE:TCS:EQ", horizon_label="1d")
    )
    with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_SHADOW_LEDGER"):
        with ledger._connect() as conn:
            conn.execute("UPDATE shadow_outcomes SET actual_realized_return = 0.99 WHERE outcome_id = ?", (out.outcome_id,))


def test_16_17_shadow_execution_fills_limit_queue_costs_and_rollover() -> None:
    sim = ShadowExecutionSimulator()
    t0 = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    entry = ShadowLedgerEntry(
        event_id="ev_fill", instrument_id="NSE:NIFTY_FUT:CURRENT", news_source="rbi",
        decision_timestamp_utc=t0,
        event_representation={}, historical_intelligence_representation={}, market_regime_representation={},
        feature_manifest_version="5.0.0-alpha", feature_manifest_sha256="sha",
        model_id="m_elig", model_version="1.0", model_training_dataset_hash="dh",
        model_validation_status=ModelValidationStatus.MODEL_ELIGIBLE, forecast_horizon="5m",
        action_state="LONG", signal_direction=1, no_trade_status=False,
    )

    mkts = [
        MarketEvent(provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
            message_type=MarketMessageType.BOOK_SNAPSHOT, local_received_at_utc=t0 - timedelta(seconds=1), local_monotonic_ns=1,
            bid_price=Decimal("24500"), ask_price=Decimal("24501"), bid_quantity=200, ask_quantity=200),
        MarketEvent(provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
            message_type=MarketMessageType.BOOK_SNAPSHOT, local_received_at_utc=t0 + timedelta(milliseconds=100), local_monotonic_ns=2,
            bid_price=Decimal("24502"), ask_price=Decimal("24503"), bid_quantity=100, ask_quantity=100,
            depth_bids=[DepthLevel(price=Decimal("24502"), quantity=100)],
            depth_asks=[DepthLevel(price=Decimal("24503"), quantity=40), DepthLevel(price=Decimal("24505"), quantity=150)]),
        # Touch exact limit level 24502.50 without trading strictly through it
        MarketEvent(provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
            message_type=MarketMessageType.QUOTE, local_received_at_utc=t0 + timedelta(minutes=2), local_monotonic_ns=3,
            last_traded_price=Decimal("24502.5")),
        MarketEvent(provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
            message_type=MarketMessageType.BOOK_SNAPSHOT, local_received_at_utc=t0 + timedelta(minutes=5), local_monotonic_ns=4,
            bid_price=Decimal("24540"), ask_price=Decimal("24542"), bid_quantity=200, ask_quantity=200),
    ]

    # 1. L2 market partial-book fill
    t_mkt = sim.simulate_trade(entry, mkts, t0 + timedelta(minutes=5))
    assert t_mkt.filled is True and t_mkt.fill_quality_source == FillQualitySource.L2_DEPTH_BOOK
    assert t_mkt.gross_theoretical_return > t_mkt.estimated_executable_return > t_mkt.net_return_after_costs

    # 2. Strict anti-optimistic LIMIT: touching limit price does NOT fill under require_trade_through=True
    cfg_lim_strict = ShadowExecutionConfig(order_type=ShadowOrderType.LIMIT, limit_offset_bps=0.0, require_trade_through_for_full_limit_fill=True)
    t_lim_strict = sim.simulate_trade(entry, mkts, t0 + timedelta(minutes=5), config=cfg_lim_strict)
    assert t_lim_strict.filled is False and t_lim_strict.unfilled_reason == "LIMIT_TOUCHED_ONLY_NO_TRADE_THROUGH"

    # 3. Documented touch partial fraction when queue model allows partial fill on touch
    cfg_lim_part = ShadowExecutionConfig(order_type=ShadowOrderType.LIMIT, limit_offset_bps=0.0, require_trade_through_for_full_limit_fill=False, touch_only_fill_fraction=0.25)
    t_lim_part = sim.simulate_trade(entry, mkts, t0 + timedelta(minutes=5), config=cfg_lim_part)
    assert t_lim_part.filled is True and t_lim_part.filled_quantity == 19 and t_lim_part.fill_fraction == pytest.approx(19 / 75, abs=1e-4)

    # 4. OHLCV conservative fallback
    bars = [
        SyntheticBarOHLCV(timestamp_utc=t0 + timedelta(seconds=2), open=24502.0, high=24520.0, low=24500.0, close=24518.0),
        SyntheticBarOHLCV(timestamp_utc=t0 + timedelta(minutes=5), open=24518.0, high=24545.0, low=24515.0, close=24540.0),
    ]
    t_bar = sim.simulate_trade(entry, [], t0 + timedelta(minutes=5), fallback_ohlcv_bars=bars)
    assert t_bar.filled is True and t_bar.fill_quality_source == FillQualitySource.OHLCV_CONSERVATIVE_FALLBACK
    assert t_bar.gross_theoretical_return > t_bar.estimated_executable_return > t_bar.net_return_after_costs


def test_11_12_18_19_20_21_pipeline_alignment_abstention_maturity_parity_and_monitoring(tmp_path: Path) -> None:
    cap = LiveCaptureStore(tmp_path / "cap.db", tmp_path / "mkt")
    hstore = PersistentHistoricalEventStore(tmp_path / "hist.db")
    ledger = ShadowSignalLedger(tmp_path / "ledger.db")
    emb = EventEmbeddingEngine(prefer_onnx=False)

    reg = LocalModelRegistry(tmp_path / "mreg")
    _register_test_model(reg, "diag_synth", is_synthetic=True, leakage_passed=True)
    diag_model = LoadedShadowModel.from_registry(reg, "diag_synth", allow_diagnostic_synthetic=True)

    pipe = LiveResearchPipeline(
        capture_store=cap,
        historical_store=hstore,
        shadow_ledger=ledger,
        horizon=OutcomeHorizon.M5,
        embedding_engine=emb,
        loaded_model=diag_model,
    )

    t0 = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    # Causal pre-decision tick + FUTURE tick (> t0) verifying future tick NEVER leaks into M_t
    m_pre = MarketEvent(
        provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
        message_type=MarketMessageType.QUOTE, local_received_at_utc=t0 - timedelta(seconds=2),
        local_monotonic_ns=100, sequence_number=1,
        bid_price=Decimal("24500"), ask_price=Decimal("24501"), bid_quantity=200, ask_quantity=200, last_traded_price=Decimal("24500.5"),
    )
    m_future_leak = MarketEvent(
        provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
        message_type=MarketMessageType.QUOTE, local_received_at_utc=t0 + timedelta(seconds=2),
        local_monotonic_ns=200, sequence_number=2,
        bid_price=Decimal("24800"), ask_price=Decimal("24801"), bid_quantity=200, ask_quantity=200, last_traded_price=Decimal("24800.5"),
    )
    pipe.ingest_market_event(m_pre, is_synthetic=True)
    pipe.ingest_market_event(m_future_leak, is_synthetic=True)

    nev = NewsEvent(
        event_id="rbi_align_1", provider_id="rbi_press_rss", source_name="RBI", source_url="https://rbi.org.in/a1",
        headline="RBI cuts repo rate by 25 bps to support liquidity",
        published_at_utc=t0 - timedelta(seconds=3), local_received_at_utc=t0,
        local_monotonic_ns=150, content_hash="h_rbi_align_1",
    )
    res = pipe.process_live_news_event(nev, is_synthetic=True)

    # M_t saw pre_decision mid 24500.5, NOT future mid 24800.5!
    assert res.ledger_entry.market_regime_representation["mid_price"] == pytest.approx(24500.5)
    # Diagnostic synthetic model forces NO_TRADE abstention with reason tagged!
    assert res.ledger_entry.no_trade_status is True and res.ledger_entry.action_state == "NO_TRADE"
    assert any("SYNTHETIC_DIAGNOSTIC_ONLY" in r for r in res.ledger_entry.no_trade_reasons)

    # Immature outcome check (missing exit tick) vs Matured outcome check (exit tick +5m)
    out_imm = pipe.record_matured_outcome_from_market(
        res.ledger_entry.prediction_id, [m_pre], exit_timestamp_utc=t0 + timedelta(minutes=5)
    )
    assert out_imm.outcome_matured is False and "IMMATURE_OR_MISSING_EXIT_BAR" in out_imm.data_quality_flags

    m_exit = MarketEvent(
        provider="TEST", venue="NSE", segment="FUTIDX", instrument_id="NSE:NIFTY_FUT:CURRENT",
        message_type=MarketMessageType.QUOTE, local_received_at_utc=t0 + timedelta(minutes=5),
        local_monotonic_ns=300, sequence_number=3,
        bid_price=Decimal("24540"), ask_price=Decimal("24542"), bid_quantity=200, ask_quantity=200,
    )
    out_mat = pipe.record_matured_outcome_from_market(
        res.ledger_entry.prediction_id, [m_pre, m_future_leak, m_exit], exit_timestamp_utc=t0 + timedelta(minutes=5)
    )
    assert out_mat.outcome_matured is True and out_mat.executable_realized_return is not None

    # Live vs Replay deterministic parity check across clean replay workspace
    parity = LiveReplayParityVerifier.verify_capture_replay_parity(
        live_capture_store=cap, live_shadow_ledger=ledger, replay_dir=tmp_path / "replay",
        horizon=OutcomeHorizon.M5, loaded_model=diag_model, embedding_engine=emb, include_synthetic=True,
    )
    assert parity.all_parity_passed is True and parity.passed_count == 1
    assert parity.comparisons[0].feature_vector_max_abs_diff == pytest.approx(0.0)

    # Monitoring report & validation evaluator & CLI verification
    mon = ShadowOperationalMonitor(cap, ledger)
    op_rep = mon.build_report(source_status={"rbi_press_rss": "HEALTHY"}, include_synthetic=True)
    assert op_rep.news_health.articles_received == 1
    assert op_rep.ai_health.events_evaluated == 1
    assert op_rep.predictive_monitoring.matured_evaluations == 1

    # Empirical evaluator excludes synthetic rows cleanly -> NO_REAL_OBSERVATIONS
    val_real = ShadowValidationEvaluator.evaluate(ledger, include_synthetic_only_cohort=False)
    assert val_real.empirical_status == "NO_REAL_OBSERVATIONS" and val_real.real_observation_count == 0
    val_syn = ShadowValidationEvaluator.evaluate(ledger, include_synthetic_only_cohort=True)
    assert val_syn.empirical_status == "SYNTHETIC_DIAGNOSTIC_ONLY" and val_syn.real_observation_count == 1

    out_json = tmp_path / "op_rep.json"
    rc = shadow_report_cli_main(["--capture-db", str(tmp_path / "cap.db"), "--market-dir", str(tmp_path / "mkt"), "--ledger-db", str(tmp_path / "ledger.db"), "--output-json", str(out_json)])
    assert rc == 0 and out_json.exists()
