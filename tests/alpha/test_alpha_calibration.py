"""Unit tests for Phase 5 Work Package F Calibration & Abstention."""

import numpy as np
import pytest

from backend.alpha.feature_registry import ModelConfiguration
from backend.alpha.prediction_uncertainty import (
    AbstentionReason,
    CalibrationMethod,
    UncertaintyCalibratorAndGate,
    compute_calibration_metrics,
)
from backend.alpha.predictive_models import RawModelPrediction


def test_calibration_reduces_ece_and_enforces_no_trade_abstention():
    """Verify validation Platt calibration, interval coverage, and all NO_TRADE abstention triggers."""
    rng = np.random.default_rng(77)
    n_val = 80
    val_raw = []
    val_y = []
    val_yp = []
    val_yn = []

    # Deliberately overconfident raw probabilities (pushed toward 0.05 / 0.95) for true rate ~0.70 / 0.30
    for i in range(n_val):
        p_raw = 0.94 if i % 2 == 0 else 0.06
        true_pos = 1 if (rng.random() < (0.70 if i % 2 == 0 else 0.30)) else 0
        y_ret = 0.01 if true_pos else -0.01
        val_raw.append(
            RawModelPrediction(
                expected_return=0.004 if true_pos else -0.004,
                median_return=0.004 if true_pos else -0.004,
                p_positive=p_raw,
                p_negative=1.0 - p_raw,
                p_material_move=0.80,
                q10=-0.002, q25=-0.001, q50=0.0, q75=0.001, q90=0.002,  # overly narrow before conformal padding!
                predictive_std=0.005,
            )
        )
        val_y.append(y_ret)
        val_yp.append(true_pos)
        val_yn.append(1 - true_pos)

    raw_report = compute_calibration_metrics(
        [r.p_positive for r in val_raw],
        val_yp,
        val_y,
        [r.q10 for r in val_raw],
        [r.q25 for r in val_raw],
        [r.q50 for r in val_raw],
        [r.q75 for r in val_raw],
        [r.q90 for r in val_raw],
    )
    assert raw_report.interval_coverage_80pct == 0.0  # Too narrow initially!

    gate = UncertaintyCalibratorAndGate(
        method=CalibrationMethod.PLATT,
        model_config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
        min_edge_over_cost_bps=2.0,
    ).fit_calibration(val_raw, val_y, val_yp, val_yn)

    # Holdout identical distribution to verify calibrated ECE drops and conformal interval widens!
    test_feats = [{"e_cls_confidence": 0.9, "e_entity_resolved": 1.0, "e_entity_confidence": 0.9, "h_insufficient_evidence": 0.0, "m_available": 1.0, "m_is_stale": 0.0}] * n_val
    cal_preds = gate.calibrate_and_gate(
        [f"ev_{i}" for i in range(n_val)],
        ["NSE:TCS:EQ"] * n_val,
        val_raw,
        test_feats,
        round_trip_cost_bps_list=[8.0] * n_val,
    )
    cal_report = compute_calibration_metrics(
        [c.p_positive for c in cal_preds],
        val_yp,
        val_y,
        [c.q10 for c in cal_preds],
        [c.q25 for c in cal_preds],
        [c.q50 for c in cal_preds],
        [c.q75 for c in cal_preds],
        [c.q90 for c in cal_preds],
    )
    assert cal_report.ece < raw_report.ece
    assert cal_report.brier_score < raw_report.brier_score
    assert cal_report.interval_coverage_80pct is not None and cal_report.interval_coverage_80pct >= 0.75

    # Explicit abstention trigger checks
    tiny_edge_pred = [RawModelPrediction(
        expected_return=0.0005, median_return=0.0005,  # 5 bps < 8 bps cost!
        p_positive=0.95, p_negative=0.05, p_material_move=0.60,
        q10=-0.01, q25=-0.005, q50=0.0005, q75=0.005, q90=0.01, predictive_std=0.01,
    )]
    bad_feats = [{
        "e_cls_confidence": 0.30,
        "e_entity_resolved": 0.0,
        "e_entity_confidence": 0.20,
        "h_insufficient_evidence": 1.0,
        "m_available": 0.0,
        "m_is_stale": 1.0,
    }]
    gated = gate.calibrate_and_gate(["bad_1"], ["NSE:TCS:EQ"], tiny_edge_pred, bad_feats, [8.0])[0]
    assert gated.abstain is True
    assert gated.action_state == "NO_TRADE"
    assert gated.signal_direction == 0
    assert AbstentionReason.LOW_CLASSIFICATION_CONFIDENCE in gated.abstain_reasons
    assert AbstentionReason.AMBIGUOUS_ENTITY_RESOLUTION in gated.abstain_reasons
    assert AbstentionReason.INSUFFICIENT_HISTORICAL_EVIDENCE in gated.abstain_reasons
    assert AbstentionReason.UNAVAILABLE_OR_STALE_MARKET in gated.abstain_reasons
    assert AbstentionReason.EXPECTED_RETURN_BELOW_COSTS in gated.abstain_reasons
