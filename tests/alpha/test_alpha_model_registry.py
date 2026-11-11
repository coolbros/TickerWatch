"""Unit tests for Work Package I: LocalModelRegistry provenance, SHA-256 verification, and reproducibility."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pytest

from backend.alpha.feature_registry import ModelConfiguration, get_feature_names_for_config
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.model_registry import LocalModelRegistry, compute_dataset_manifest_hash
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_models import ModelAlgorithm, TabularEventPredictor
from backend.alpha.prediction_uncertainty import CalibrationMethod, UncertaintyCalibratorAndGate


def test_model_registry_persists_verifies_sha256_and_reproduces_bit_for_bit(tmp_path: Path) -> None:
    rng = np.random.default_rng(123)
    feat_names = get_feature_names_for_config(ModelConfiguration.MODEL_D_FULL_SYSTEM)
    t0 = datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc)

    rows = []
    y_cont = []
    y_pos = []
    y_neg = []
    for i in range(45):
        vals = {fname: 0.0 for fname in feat_names}
        vals["e_cls_confidence"] = 0.85
        vals["e_entity_confidence"] = 0.90
        vals["e_mat_composite"] = 0.70
        vals["m_session_open"] = 1.0
        vals["h_insufficient_evidence"] = 0.0
        sig = float(rng.normal(0.0, 1.0))
        vals["e_surp_std_z"] = sig
        vals["h_sim_weighted_return"] = 0.008 * sig
        ret = 0.012 * sig + float(rng.normal(0.0, 0.002))

        rows.append(
            PointInTimeFeatureRow(
                event_id=f"ev_{i}",
                instrument_id="NSE_RELIANCE",
                jurisdiction=MarketJurisdiction.IN,
                decision_timestamp=t0 + timedelta(hours=i),
                horizon_label="1d",
                is_synthetic=True,
                values=vals,
            )
        )
        y_cont.append(ret)
        y_pos.append(1 if ret > 0.001 else 0)
        y_neg.append(1 if ret < -0.001 else 0)

    train_rows, val_rows, test_rows = rows[:25], rows[25:35], rows[35:]
    train_x = [r.subset(ModelConfiguration.MODEL_D_FULL_SYSTEM) for r in train_rows]
    val_x = [r.subset(ModelConfiguration.MODEL_D_FULL_SYSTEM) for r in val_rows]
    test_x = [r.subset(ModelConfiguration.MODEL_D_FULL_SYSTEM) for r in test_rows]

    pred = TabularEventPredictor(algorithm=ModelAlgorithm.RIDGE, config=ModelConfiguration.MODEL_D_FULL_SYSTEM, ridge_alpha=1.0, seed=42)
    pred.fit(
        train_x,
        y_cont[:25],
        y_pos[:25],
        y_neg[:25],
        instrument_ids=[r.instrument_id for r in train_rows],
    )
    cal = UncertaintyCalibratorAndGate(
        method=CalibrationMethod.PLATT,
        model_config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
    )
    cal.fit_calibration(
        pred.predict_raw(val_x, instrument_ids=[r.instrument_id for r in val_rows]),
        y_cont[25:35],
        y_pos[25:35],
        y_neg[25:35],
    )

    direct_preds = cal.calibrate_and_gate(
        [r.event_id for r in test_rows],
        [r.instrument_id for r in test_rows],
        pred.predict_raw(test_x, instrument_ids=[r.instrument_id for r in test_rows]),
        test_x,
    )

    reg = LocalModelRegistry(tmp_path / "reg")
    dhash = compute_dataset_manifest_hash([r.event_id for r in rows], [r.decision_timestamp for r in rows], True)
    rec = reg.register_run(
        model_id="synth_ridge_d_001",
        model_version="5.0.0-alpha",
        model_config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
        horizon_label="1d",
        predictor=pred,
        calibrator=cal,
        dataset_manifest_hash=dhash,
        training_cutoff_utc=val_rows[-1].decision_timestamp,
        validation_period="2026-06-02T06:00Z..2026-06-02T15:00Z",
        test_period="2026-06-02T16:00Z..2026-06-03T01:00Z",
        train_metrics={"rmse": 0.002},
        oos_metrics={"rmse": 0.003},
        calibration_summary={"ece": 0.05},
        leakage_audit_passed=True,
        is_synthetic=True,
        known_limitations=["Synthetic dataset verification run only."],
    )

    assert reg.verify_artifact_integrity(rec.model_id) is True
    assert len(reg.list_runs(include_synthetic=False)) == 0
    assert len(reg.list_runs(include_synthetic=True)) == 1

    reloaded_preds = reg.reproduce_predictions(rec.model_id, test_rows)
    assert len(reloaded_preds) == len(direct_preds)
    for a, b in zip(direct_preds, reloaded_preds):
        assert a.expected_return == pytest.approx(b.expected_return, abs=1e-12)
        assert a.p_positive == pytest.approx(b.p_positive, abs=1e-12)
        assert a.action_state == b.action_state

    # Tamper detection check
    art_path = (tmp_path / "reg") / rec.artifact_rel_path
    art_path.write_bytes(b"corrupted-payload")
    assert reg.verify_artifact_integrity(rec.model_id) is False
    with pytest.raises(ValueError, match="integrity check FAILED"):
        reg.load_bundle(rec.model_id)
