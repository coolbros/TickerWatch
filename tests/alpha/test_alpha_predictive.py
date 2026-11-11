"""Comprehensive 19-Gate Verification Suite for Phase 5 Predictive Modeling (WP-J)."""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
import numpy as np
import pytest

from backend.alpha.event_classifier import EventSubtype
from backend.alpha.event_extraction import CurrencyUnit
from backend.alpha.feature_registry import (
    FoldSafeFeatureTransformer,
    ModelConfiguration,
    build_feature_manifest,
    get_feature_names_for_config,
)
from backend.alpha.historical_import import (
    IST_ZONE,
    US_EASTERN_ZONE,
    MarketJurisdiction,
    parse_source_timestamp_to_utc,
)
from backend.alpha.historical_intelligence import HistoricalIntelligenceRepresentation
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.leakage_audit import LeakageAuditor
from backend.alpha.market_context import MarketDirection, MarketRegimeContext, VolatilityRegime, LiquidityRegime
from backend.alpha.model_registry import LocalModelRegistry, compute_dataset_manifest_hash
from backend.alpha.nlp_pipeline import get_nlp_pipeline
from backend.alpha.outcomes import (
    BenchmarkAdjustmentMethod,
    EventOutcomeLabel,
    OutcomeDataQualityStatus,
    OutcomeHorizon,
    OutcomeLabelType,
)
from backend.alpha.predictive_evaluation import compute_predictive_metrics
from backend.alpha.predictive_features import FuturesBasisMetadata, PointInTimeFeatureBuilder, PointInTimeFeatureRow
from backend.alpha.predictive_models import ModelAlgorithm, RawModelPrediction, TabularEventPredictor
from backend.alpha.predictive_pipeline import (
    ModelExperimentSpec,
    PredictiveResearchPipeline,
)
from backend.alpha.predictive_targets import (
    ActionableDirectionClass,
    CostAssumptionSpec,
    PredictiveTargetBuilder,
    PredictiveTargetRecord,
)
from backend.alpha.prediction_uncertainty import (
    AbstentionReason,
    CalibrationMethod,
    CalibratedPrediction,
    UncertaintyCalibratorAndGate,
)
from backend.alpha.research_backtest import ResearchBacktester
from backend.alpha.walk_forward import PurgedWalkForwardSplitter, WalkForwardMode


def _minimal_target(
    event_id: str,
    inst_id: str,
    dec_ts: datetime,
    ret: float = 0.008,
    label_type: OutcomeLabelType = OutcomeLabelType.EXECUTABLE,
    horizon: OutcomeHorizon = OutcomeHorizon.D1,
    is_synthetic: bool = True,
) -> PredictiveTargetRecord:
    return PredictiveTargetRecord(
        event_id=event_id,
        instrument_id=inst_id,
        jurisdiction=MarketJurisdiction.IN,
        decision_timestamp=dec_ts,
        horizon=horizon,
        label_type=label_type,
        entry_timestamp=dec_ts + timedelta(seconds=1),
        entry_price=100.0,
        exit_timestamp=dec_ts + timedelta(hours=12),
        exit_price=100.0 * (1.0 + ret),
        simple_return=ret,
        log_return=ret,
        benchmark_return=0.0,
        market_adjusted_return=ret,
        benchmark_adjustment_method=BenchmarkAdjustmentMethod.SIMPLE_MARKET_ADJUSTED,
        cost_scenario="IN_NIFTY_FUT_CONSERVATIVE",
        transaction_cost_assumption_bps=9.1,
        spread_bps=2.0,
        estimated_slippage_bps=2.0,
        actionable_hurdle_bps=10.6,
        y_continuous=ret,
        y_direction_3class=(
            ActionableDirectionClass.LONG_POSITIVE if ret > 0.00106 else (
                ActionableDirectionClass.SHORT_NEGATIVE if ret < -0.00106 else ActionableDirectionClass.NEUTRAL_NO_TRADE
            )
        ),
        y_positive_material=1 if ret > 0.00106 else 0,
        y_negative_material=1 if ret < -0.00106 else 0,
        y_material_move=1 if abs(ret) > 0.00106 else 0,
        y_positive_raw=1 if ret > 0 else 0,
        data_quality_status=OutcomeDataQualityStatus.VERIFIED,
        is_matured=True,
        is_trainable=True,
        source_provenance="UNIT_TEST",
        is_synthetic=is_synthetic,
    )


def _minimal_hist_rep(event_id: str, dec_ts: datetime, inst_id: str = "NSE:TCS:EQ") -> HistoricalIntelligenceRepresentation:
    from backend.alpha.contextual_features import ContextualIntelligenceFeatures, EvidenceStatus, HorizonResponseDistribution
    from backend.alpha.event_retrieval import RetrievalMode
    from backend.alpha.historical_intelligence import ConfidenceUncertaintySummary, HistoricalIntelligenceProvenance
    from backend.alpha.nlp_pipeline import TimestampProvenance

    return HistoricalIntelligenceRepresentation(
        query_event_id=event_id,
        decision_timestamp=dec_ts,
        jurisdiction=MarketJurisdiction.IN,
        primary_instrument_id=inst_id,
        retrieval_model_version="bge-small-en-v1.5-onnx-int8-cls512-v2",
        retrieval_mode=RetrievalMode.HYBRID_MODULAR,
        comparable_event_count=12,
        matched_event_count=10,
        return_distributions={
            "1d": HorizonResponseDistribution(
                horizon="1d",
                comparable_event_count=12,
                matured_count=10,
                evidence_status=EvidenceStatus.SUFFICIENT,
                insufficient_evidence=False,
                data_quality_status="VERIFIED",
                mean_return=0.011,
                median_return=0.009,
                std_dev=0.006,
                similarity_weighted_mean_return=0.012,
                positive_frequency=0.80,
                negative_frequency=0.20,
            )
        },
        market_regime=MarketRegimeContext(
            instrument_id=inst_id,
            decision_timestamp=dec_ts,
            latest_market_timestamp=dec_ts - timedelta(seconds=2),
            pre_event_observation_count=5,
            is_stale=False,
            market_direction=MarketDirection.BULLISH,
            volatility_regime=VolatilityRegime.NORMAL,
            liquidity_regime=LiquidityRegime.NORMAL,
            bid_ask_spread_bps=2.5,
            realized_volatility_bps=14.0,
            depth_imbalance=0.2,
        ),
        contextual_features=ContextualIntelligenceFeatures(
            query_event_id=event_id,
            decision_timestamp=dec_ts,
            min_sample_size_threshold=5,
            total_comparable_events=12,
            matched_events_returned=10,
            mean_match_similarity=0.82,
            recency_days_latest=18.0,
            overall_evidence_status=EvidenceStatus.SUFFICIENT,
            insufficient_evidence_flag=False,
        ),
        confidence_and_uncertainty=ConfidenceUncertaintySummary(
            event_nlp_confidence=0.88,
            historical_evidence_status=EvidenceStatus.SUFFICIENT,
            insufficient_historical_evidence=False,
            market_regime_available=True,
            overall_contextual_confidence=0.86,
        ),
        provenance=HistoricalIntelligenceProvenance(
            store_location=":memory:",
            pipeline_version="4.1.0-alpha",
            nlp_pipeline_version="3.1.0",
            query_timestamp_provenance=TimestampProvenance(
                local_received_at_utc=dec_ts,
                processed_at_utc=dec_ts,
                decision_timestamp=dec_ts,
            ),
            store_event_count_at_query=12,
            market_data_available=True,
            is_synthetic=True,
        ),
        historical_context_source="PHASE_4_PERSISTENT_MATURITY_VERIFIED",
    )


def test_01_timezone_correction() -> None:
    dt_ist, meta_ist = parse_source_timestamp_to_utc("2026-05-12 09:15:00", IST_ZONE, ["%Y-%m-%d %H:%M:%S"])
    assert dt_ist == datetime(2026, 5, 12, 3, 45, tzinfo=timezone.utc)
    assert meta_ist["interpretation"] == "LOCALIZED_DEFAULT(Asia/Kolkata)"

    dt_est, _ = parse_source_timestamp_to_utc("2026-01-15 16:00:00", US_EASTERN_ZONE, ["%Y-%m-%d %H:%M:%S"])
    dt_edt, _ = parse_source_timestamp_to_utc("2026-07-15 16:00:00", US_EASTERN_ZONE, ["%Y-%m-%d %H:%M:%S"])
    assert dt_est == datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc)
    assert dt_edt == datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)

    dt_sec_date, meta_date = parse_source_timestamp_to_utc(
        "2026-06-15", US_EASTERN_ZONE, ["%Y-%m-%d"], date_only_time_local=dt_time(17, 30)
    )
    assert dt_sec_date == datetime(2026, 6, 15, 21, 30, tzinfo=timezone.utc)
    assert meta_date["date_only"] is True


def test_02_revision_as_of_reconstruction(tmp_path: Path) -> None:
    store = PersistentHistoricalEventStore(tmp_path / "rev.db")
    nlp = get_nlp_pipeline()
    t1 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)

    r1 = nlp.process_raw_text("Infosys wins $200 million UK banking contract", decision_timestamp=t1, event_id="infy_rev")
    r2 = nlp.process_raw_text("Infosys revises UK banking contract to $450 million", decision_timestamp=t2, event_id="infy_rev")
    store.upsert_event(r1, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)
    store.upsert_event(r2, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)

    as_of_mid = store.get_event_as_of("infy_rev", decision_timestamp=t1 + timedelta(hours=12))
    assert as_of_mid is not None and as_of_mid.revision_number == 1
    assert "200 million" in as_of_mid.headline

    as_of_after = store.get_event_as_of("infy_rev", decision_timestamp=t2 + timedelta(hours=1))
    assert as_of_after is not None and as_of_after.revision_number == 2
    assert "450 million" in as_of_after.headline


def test_03_feature_timestamp_validation() -> None:
    nlp = get_nlp_pipeline()
    fb = PointInTimeFeatureBuilder(horizon=OutcomeHorizon.D1)
    t_dec = datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc)
    rep = nlp.process_raw_text("TCS wins Rs 1000 crore deal", decision_timestamp=t_dec, event_id="ev_ts")
    hist = _minimal_hist_rep("ev_ts", t_dec)

    # Future market timestamp must be blocked
    hist.market_regime.latest_market_timestamp = t_dec + timedelta(seconds=1)
    with pytest.raises(ValueError, match="Causal leakage in M_t"):
        fb.build_row(rep, hist)

    # Fix market timestamp, leak futures metadata timestamp
    hist.market_regime.latest_market_timestamp = t_dec - timedelta(seconds=1)
    bad_fut = FuturesBasisMetadata(
        contract_id="TCS26APRFUT",
        as_of_timestamp=t_dec + timedelta(seconds=5),
        futures_price=4020.0,
        spot_price=4010.0,
        expiry_date=date(2026, 4, 30),
    )
    with pytest.raises(ValueError, match="Causal leakage in M_t futures"):
        fb.build_row(rep, hist, futures_meta=bad_fut)


def test_04_outcome_maturity() -> None:
    t_dec = datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc)
    tb = PredictiveTargetBuilder(min_trainable_samples_per_horizon=2)

    lbl = EventOutcomeLabel(
        event_id="ev_mat",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.D1,
        label_type=OutcomeLabelType.EXECUTABLE,
        event_timestamp_utc=t_dec - timedelta(seconds=2),
        decision_timestamp_utc=t_dec,
        outcome_start_price_ts=t_dec + timedelta(seconds=1),
        outcome_end_price_ts=t_dec + timedelta(hours=24),
        start_price=100.0,
        end_price=102.0,
        simple_return=0.02,
        data_quality_status=OutcomeDataQualityStatus.VERIFIED,
    )
    immature_rec = tb.build_target(lbl, MarketJurisdiction.IN, evaluation_cutoff_ts=t_dec + timedelta(hours=12))
    assert immature_rec.is_matured is False
    assert immature_rec.is_trainable is False
    assert immature_rec.y_continuous is None

    low_res = lbl.model_copy(update={"data_quality_status": OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION})
    bad_res_rec = tb.build_target(low_res, MarketJurisdiction.IN, evaluation_cutoff_ts=t_dec + timedelta(hours=30))
    assert bad_res_rec.is_trainable is False
    ok, _, reason = tb.check_horizon_trainability([bad_res_rec], OutcomeHorizon.D1, OutcomeLabelType.EXECUTABLE)
    assert ok is False and "INSUFFICIENT_RESOLUTION" in reason


def test_05_executable_vs_reaction_label_separation() -> None:
    t0 = datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc)
    preds = [
        CalibratedPrediction(
            event_id="e1", instrument_id="NSE:TCS:EQ",
            expected_return=0.01, median_return=0.01,
            p_positive=0.8, p_negative=0.2, p_material_move=0.8,
            q10=0.0, q25=0.005, q50=0.01, q75=0.015, q90=0.02,
            predictive_std=0.005, signal_direction=1, abstain=False, action_state="LONG",
        )
    ]
    react_tgt = [_minimal_target("e1", "NSE:TCS:EQ", t0, ret=0.01, label_type=OutcomeLabelType.REACTION)]
    with pytest.raises(ValueError, match="REJECTED non-executable"):
        ResearchBacktester.run("TEST", preds, react_tgt)

    exec_tgt = [_minimal_target("e1", "NSE:TCS:EQ", t0, ret=0.01, label_type=OutcomeLabelType.EXECUTABLE)]
    bt = ResearchBacktester.run("TEST", preds, exec_tgt)
    assert bt.label_type_enforced == OutcomeLabelType.EXECUTABLE.value
    assert bt.trade_count == 1


def test_06_feature_target_separation() -> None:
    all_names = set(get_feature_names_for_config(ModelConfiguration.MODEL_D_FULL_SYSTEM))
    forbidden_substrings = ("y_continuous", "simple_return", "exit_price", "entry_price", "profit", "outcome_end")
    for fname in all_names:
        assert not any(tok in fname for tok in forbidden_substrings)

    m_only = set(get_feature_names_for_config(ModelConfiguration.MODEL_A_MARKET_ONLY))
    assert all(k.startswith("m_") for k in m_only)
    assert not any(k.startswith("e_") or k.startswith("h_") for k in m_only)
    manifest = build_feature_manifest(ModelConfiguration.MODEL_A_MARKET_ONLY)
    assert manifest.groups_included == ["M"]


def test_07_instrument_mapping_and_alignment() -> None:
    nlp = get_nlp_pipeline()
    fb = PointInTimeFeatureBuilder(horizon=OutcomeHorizon.D1)
    t0 = datetime(2026, 5, 2, 6, 0, tzinfo=timezone.utc)
    rep = nlp.process_raw_text("TCS wins contract", decision_timestamp=t0, event_id="ev_a")
    hist = _minimal_hist_rep("ev_b", t0)
    with pytest.raises(ValueError, match="Mismatched event_id"):
        fb.build_row(rep, hist)


def test_08_currency_consistency() -> None:
    nlp = get_nlp_pipeline()
    fb = PointInTimeFeatureBuilder(horizon=OutcomeHorizon.D1)
    t0 = datetime(2026, 5, 3, 6, 0, tzinfo=timezone.utc)
    rep_inr = nlp.process_raw_text("TCS wins Rs 1,200 crore order in India", decision_timestamp=t0, event_id="ev_inr")
    rep_usd = nlp.process_raw_text("Infosys wins $500 million cloud contract", decision_timestamp=t0, event_id="ev_usd")
    row_inr = fb.build_row(rep_inr, _minimal_hist_rep("ev_inr", t0))
    row_usd = fb.build_row(rep_usd, _minimal_hist_rep("ev_usd", t0, "NSE:INFY:EQ"))
    assert row_inr.values["e_currency_inr"] == 1.0 and row_inr.values["e_currency_usd"] == 0.0
    assert row_usd.values["e_currency_usd"] == 1.0 and row_usd.values["e_currency_inr"] == 0.0


def test_09_missing_data_handling() -> None:
    nlp = get_nlp_pipeline()
    fb = PointInTimeFeatureBuilder(horizon=OutcomeHorizon.D1)
    t0 = datetime(2026, 5, 4, 6, 0, tzinfo=timezone.utc)
    rep = nlp.process_raw_text("Reliance launches retail expansion in Gujarat", decision_timestamp=t0, event_id="ev_miss")
    row = fb.build_row(rep, _minimal_hist_rep("ev_miss", t0, "NSE:RELIANCE:EQ"))
    assert row.values["e_surprise_missing"] == 1.0
    assert row.values["e_surprise_z"] is None
    assert "e_surprise_z" in row.missing_features


def test_10_purged_split_boundaries() -> None:
    t0 = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)
    rows = [
        PointInTimeFeatureRow(
            event_id=f"ev_{i}", instrument_id="NSE:TCS:EQ", jurisdiction=MarketJurisdiction.IN,
            decision_timestamp=t0 + timedelta(hours=12 * i), horizon_label="1d", is_synthetic=True, values={},
        )
        for i in range(40)
    ]
    tgts = [_minimal_target(f"ev_{i}", "NSE:TCS:EQ", t0 + timedelta(hours=12 * i), ret=0.005 * ((-1) ** i)) for i in range(40)]

    for mode in (WalkForwardMode.EXPANDING, WalkForwardMode.ROLLING):
        splitter = PurgedWalkForwardSplitter(horizon=OutcomeHorizon.D1, n_folds=2, mode=mode, allow_synthetic=True)
        folds, _ = splitter.generate_folds(rows, tgts)
        assert len(folds) >= 1
        for f in folds:
            assert f.leakage_audit.passed is True
            te_start = f.test_start
            for ti in f.train_indices + f.val_indices:
                exit_ts = tgts[ti].exit_timestamp or (rows[ti].decision_timestamp + timedelta(days=1))
                assert exit_ts <= te_start


def test_11_embargo_rules() -> None:
    with pytest.raises(ValueError, match="Embargo duration"):
        PurgedWalkForwardSplitter(horizon=OutcomeHorizon.D1, embargo_seconds=3600.0, allow_synthetic=True)

    splitter = PurgedWalkForwardSplitter(horizon=OutcomeHorizon.M30, embargo_seconds=1800.0, allow_synthetic=True)
    assert splitter.embargo_seconds == 1800.0


def test_12_duplicate_and_revision_leakage() -> None:
    t0 = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
    rows = [
        PointInTimeFeatureRow(
            event_id=f"ev_{i}", instrument_id="NSE:INFY:EQ", jurisdiction=MarketJurisdiction.IN,
            decision_timestamp=t0 + timedelta(days=2 * i), horizon_label="1d",
            is_synthetic=True, content_hash=f"h_{i}", values={},
        )
        for i in range(20)
    ]
    tgts = [_minimal_target(f"ev_{i}", "NSE:INFY:EQ", t0 + timedelta(days=2 * i)) for i in range(20)]
    # Plant exact duplicate content hash + identical event_id across train/test boundary
    rows[15].content_hash = rows[5].content_hash
    rep = LeakageAuditor().audit_split(
        train_features=rows[:10],
        train_targets=tgts[:10],
        val_features=[],
        val_targets=[],
        test_features=rows[10:],
        test_targets=tgts[10:],
        min_embargo_seconds=86400.0,
        allow_synthetic_in_test=True,
    )
    assert rep.passed is False
    assert any("DUPLICATE_OR_REVISION_LEAK" in v or "hash" in v.lower() for v in rep.violations)


def test_13_train_fold_only_preprocessing() -> None:
    tx = FoldSafeFeatureTransformer(["e_surprise_z", "e_surprise_missing"])
    train_d = [{"e_surprise_z": 1.0, "e_surprise_missing": 0.0}, {"e_surprise_z": 3.0, "e_surprise_missing": 0.0}]
    test_outlier = [{"e_surprise_z": 1e8, "e_surprise_missing": 0.0}]
    X_tr = tx.fit_transform(train_d)
    assert tx.get_fit_statistics()["means"]["e_surprise_z"] == pytest.approx(2.0)
    X_te = tx.transform(test_outlier)
    # Transform applied train mean/std (not test 1e8)
    assert tx.get_fit_statistics()["means"]["e_surprise_z"] == pytest.approx(2.0)
    assert X_te[0, 0] == pytest.approx(8.0) and X_tr.shape == (2, 2)


def test_14_model_reproducibility(tmp_path: Path) -> None:
    feat_names = get_feature_names_for_config(ModelConfiguration.MODEL_B_EVENT_ONLY)
    t0 = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(20):
        vals = {f: 0.0 for f in feat_names}
        vals["e_cls_confidence"] = 0.85
        vals["e_entity_confidence"] = 0.85
        vals["e_surprise_z"] = float(i - 10) * 0.2
        rows.append(
            PointInTimeFeatureRow(
                event_id=f"r_{i}", instrument_id="NSE:TCS:EQ", jurisdiction=MarketJurisdiction.IN,
                decision_timestamp=t0 + timedelta(days=i), horizon_label="1d", is_synthetic=True, values=vals,
            )
        )
    y = [0.008 * float(r.values["e_surprise_z"]) for r in rows]
    yp = [1 if v > 0.001 else 0 for v in y]
    yn = [1 if v < -0.001 else 0 for v in y]
    pred = TabularEventPredictor(ModelAlgorithm.RIDGE, ModelConfiguration.MODEL_B_EVENT_ONLY, seed=7).fit(
        [r.subset(ModelConfiguration.MODEL_B_EVENT_ONLY) for r in rows], y, yp, yn
    )
    cal = UncertaintyCalibratorAndGate(CalibrationMethod.NONE, ModelConfiguration.MODEL_B_EVENT_ONLY)
    reg = LocalModelRegistry(tmp_path / "mreg")
    dhash = compute_dataset_manifest_hash([r.event_id for r in rows], [r.decision_timestamp for r in rows], True)
    rec = reg.register_run(
        model_id="repro_b", model_version="5.0.0-alpha", model_config=ModelConfiguration.MODEL_B_EVENT_ONLY,
        horizon_label="1d", predictor=pred, calibrator=cal, dataset_manifest_hash=dhash,
        training_cutoff_utc=rows[-1].decision_timestamp, validation_period="NONE", test_period="NONE",
        train_metrics={}, oos_metrics={}, calibration_summary={}, leakage_audit_passed=True,
        is_synthetic=True, known_limitations=["Unit test reproducibility artifact."],
    )
    p1 = reg.reproduce_predictions(rec.model_id, rows[-4:])
    p2 = reg.reproduce_predictions(rec.model_id, rows[-4:])
    assert [x.expected_return for x in p1] == pytest.approx([x.expected_return for x in p2], abs=1e-12)


def test_15_baseline_metrics() -> None:
    feat_names = get_feature_names_for_config(ModelConfiguration.MODEL_D_FULL_SYSTEM)
    rng = np.random.default_rng(11)
    feat_dicts = [{k: float(rng.normal(0, 1)) for k in feat_names} for _ in range(30)]
    y = [float(rng.normal(0.002, 0.01)) for _ in range(30)]
    yp = [1 if v > 0.001 else 0 for v in y]
    yn = [1 if v < -0.001 else 0 for v in y]
    insts = ["NSE:TCS:EQ" if i % 2 == 0 else "NSE:INFY:EQ" for i in range(30)]
    subs = ["EARNINGS_ANNOUNCEMENT" if i % 3 == 0 else "GUIDANCE_REVISION" for i in range(30)]

    cal = UncertaintyCalibratorAndGate(CalibrationMethod.NONE, ModelConfiguration.MODEL_D_FULL_SYSTEM)
    for algo in (
        ModelAlgorithm.ZERO_RETURN,
        ModelAlgorithm.UNCONDITIONAL_MEAN,
        ModelAlgorithm.ROLLING_INSTRUMENT,
        ModelAlgorithm.EVENT_SUBTYPE_SHRINKAGE,
    ):
        est = TabularEventPredictor(algo, ModelConfiguration.MODEL_D_FULL_SYSTEM).fit(
            feat_dicts, y, yp, yn, instrument_ids=insts, subtypes=subs
        )
        raw = est.predict_raw(feat_dicts, instrument_ids=insts, subtypes=subs)
        cal_p = cal.calibrate_and_gate([f"b_{i}" for i in range(30)], insts, raw, feat_dicts)
        rep = compute_predictive_metrics(algo.value, "1d", cal_p, y, yp)
        assert rep.n_samples == 30 and np.isfinite(rep.rmse) and np.isfinite(rep.brier_score)


def test_16_prediction_calibration() -> None:
    rng = np.random.default_rng(5)
    raw_val = []
    yc = []
    yp = []
    yn = []
    for i in range(40):
        sig = float(rng.normal(0, 1))
        ret = 0.01 * sig + float(rng.normal(0, 0.004))
        p_raw = float(np.clip(0.5 + 0.45 * np.tanh(sig), 0.05, 0.95))
        raw_val.append(
            RawModelPrediction(
                expected_return=0.01 * sig, median_return=0.01 * sig,
                p_positive=p_raw, p_negative=1.0 - p_raw, p_material_move=0.7,
                q10=0.01 * sig - 0.003, q25=0.01 * sig - 0.001, q50=0.01 * sig,
                q75=0.01 * sig + 0.001, q90=0.01 * sig + 0.003, predictive_std=0.005,
            )
        )
        yc.append(ret)
        yp.append(1 if ret > 0.001 else 0)
        yn.append(1 if ret < -0.001 else 0)

    cal = UncertaintyCalibratorAndGate(CalibrationMethod.PLATT, ModelConfiguration.MODEL_D_FULL_SYSTEM)
    cal.fit_calibration(raw_val, yc, yp, yn)
    assert cal.is_calibrated is True
    assert cal._conformal_q_pad >= 0.0


def test_17_backtest_costs() -> None:
    t0 = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)
    preds = []
    tgts = []
    for i in range(10):
        ts = t0 + timedelta(days=i)
        preds.append(
            CalibratedPrediction(
                event_id=f"c_{i}", instrument_id="NSE:TCS:EQ",
                expected_return=0.006, median_return=0.006,
                p_positive=0.78, p_negative=0.22, p_material_move=0.80,
                q10=-0.002, q25=0.002, q50=0.006, q75=0.010, q90=0.014,
                predictive_std=0.005, signal_direction=1, abstain=False, action_state="LONG",
            )
        )
        tgts.append(_minimal_target(f"c_{i}", "NSE:TCS:EQ", ts, ret=0.004, label_type=OutcomeLabelType.EXECUTABLE))

    bt = ResearchBacktester.run("COST_TEST", preds, tgts)
    assert bt.gross_return_total > bt.net_return_total
    assert bt.total_cost_paid_bps == pytest.approx(10 * 9.1, rel=1e-4)
    sweep = ResearchBacktester.run_cost_sensitivity_sweep("COST_TEST", preds, tgts)
    assert sweep[0].net_return_total > sweep[1].net_return_total > sweep[2].net_return_total


def test_18_no_trade_logic() -> None:
    cal = UncertaintyCalibratorAndGate(
        method=CalibrationMethod.NONE,
        model_config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
        min_cls_confidence=0.60,
        min_entity_confidence=0.60,
        min_directional_prob=0.58,
        max_predictive_std=0.05,
        min_edge_over_cost_bps=2.0,
    )
    raw = RawModelPrediction(
        expected_return=0.0005,  # 5 bps < 10.5 cost + 2 buffer
        median_return=0.0005,
        p_positive=0.54, p_negative=0.46, p_material_move=0.3,
        q10=-0.04, q25=-0.01, q50=0.0005, q75=0.01, q90=0.04,
        predictive_std=0.08,  # exceeds max_predictive_std
    )
    fdict = {
        "e_cls_confidence": 0.45,        # LOW_CLASSIFICATION_CONFIDENCE
        "e_entity_resolved": 0.0,        # AMBIGUOUS_ENTITY_RESOLUTION
        "e_entity_confidence": 0.40,
        "h_insufficient_evidence": 1.0,  # INSUFFICIENT_HISTORICAL_EVIDENCE
        "h_outcomes_missing": 0.0,
        "m_available": 0.0,              # UNAVAILABLE_OR_STALE_MARKET
        "m_is_stale": 1.0,
        "m_liq_thin": 0.0,
    }
    res = cal.calibrate_and_gate(["ev_nt"], ["NSE:TCS:EQ"], [raw], [fdict], [10.5])[0]
    assert res.abstain is True and res.action_state == "NO_TRADE" and res.signal_direction == 0
    expected_reasons = {
        AbstentionReason.LOW_CLASSIFICATION_CONFIDENCE,
        AbstentionReason.AMBIGUOUS_ENTITY_RESOLUTION,
        AbstentionReason.INSUFFICIENT_HISTORICAL_EVIDENCE,
        AbstentionReason.UNAVAILABLE_OR_STALE_MARKET,
        AbstentionReason.HIGH_PREDICTIVE_UNCERTAINTY,
        AbstentionReason.EXPECTED_RETURN_BELOW_COSTS,
        AbstentionReason.SUB_THRESHOLD_DIRECTIONAL_PROB,
    }
    assert set(res.abstain_reasons) == expected_reasons


def test_19_out_of_sample_isolation_and_pipeline() -> None:
    rng = np.random.default_rng(77)
    feat_names = get_feature_names_for_config(ModelConfiguration.MODEL_D_FULL_SYSTEM)
    t0 = datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc)
    frows = []
    tgts = []
    for i in range(60):
        dec_ts = t0 + timedelta(days=2 * i)
        vals = {k: 0.0 for k in feat_names}
        z = float(rng.normal(0.0, 1.2))
        vals["e_cls_confidence"] = 0.85
        vals["e_entity_resolved"] = 1.0
        vals["e_entity_confidence"] = 0.90
        vals["h_insufficient_evidence"] = 0.0
        vals["m_available"] = 1.0
        vals["e_surprise_z"] = z
        vals["h_sim_weighted_return"] = 0.008 * z
        ret = 0.010 * z + float(rng.normal(0.0, 0.004))

        frows.append(
            PointInTimeFeatureRow(
                event_id=f"pipe_{i}", instrument_id="NSE:TCS:EQ", jurisdiction=MarketJurisdiction.IN,
                decision_timestamp=dec_ts, horizon_label="1d", is_synthetic=True,
                primary_issuer_id="NSE:TCS:EQ", content_hash=f"ph_{i}", values=vals,
                provenance={"primary_family": "CORPORATE_FINANCIAL", "primary_subtype": "EARNINGS_ANNOUNCEMENT"},
            )
        )
        tgts.append(_minimal_target(f"pipe_{i}", "NSE:TCS:EQ", dec_ts, ret=ret, is_synthetic=True))

    # Reject synthetic runs unless explicitly allowed
    with pytest.raises(ValueError, match="allow_synthetic=False"):
        PredictiveResearchPipeline.run_experiment(
            experiment_name="BLOCKED_SYNTH",
            horizon=OutcomeHorizon.D1,
            feature_rows=frows,
            target_records=tgts,
            allow_synthetic=False,
        )

    specs = [
        ModelExperimentSpec(label="BASE_ZERO", algorithm=ModelAlgorithm.ZERO_RETURN, config=ModelConfiguration.MODEL_D_FULL_SYSTEM, calibration_method=CalibrationMethod.NONE),
        ModelExperimentSpec(label="MOD_A", algorithm=ModelAlgorithm.RIDGE, config=ModelConfiguration.MODEL_A_MARKET_ONLY),
        ModelExperimentSpec(label="MOD_D", algorithm=ModelAlgorithm.RIDGE, config=ModelConfiguration.MODEL_D_FULL_SYSTEM),
    ]
    rep = PredictiveResearchPipeline.run_experiment(
        experiment_name="UNIT_OOS_PIPE",
        horizon=OutcomeHorizon.D1,
        feature_rows=frows,
        target_records=tgts,
        executable_targets=tgts,
        model_specs=specs,
        reference_model_label="MOD_D",
        n_folds=2,
        allow_synthetic=True,
        n_bootstrap=150,
    )
    assert rep.all_leakage_audits_passed is True
    assert rep.is_synthetic is True
    assert rep.alpha_attribution is not None
    assert len(rep.model_results) == 3
    mod_d = [m for m in rep.model_results if m.spec.label == "MOD_D"][0]
    mod_a = [m for m in rep.model_results if m.spec.label == "MOD_A"][0]
    assert mod_d.pooled_oos_metrics.rmse < mod_a.pooled_oos_metrics.rmse
