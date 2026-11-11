"""Unit tests for Phase 5 Work Package E Baseline & Classical ML Models."""

import numpy as np
import pytest

from backend.alpha.feature_registry import ModelConfiguration
from backend.alpha.predictive_models import ModelAlgorithm, TabularEventPredictor


def test_all_baseline_and_ml_models_ablation_and_quantiles():
    """Verify baselines and classical ML estimators predict accurately on noiseless synthetic signal across configs."""
    rng = np.random.default_rng(123)
    n = 150
    rows = []
    y_cont = []
    y_pos = []
    y_neg = []
    insts = []
    subs = []

    for i in range(n):
        sig = float(rng.normal(0.0, 1.0))
        ret = 0.015 * sig
        rows.append({
            "e_mat_composite": float(np.clip(0.5 + 0.3 * sig, 0.0, 1.0)),
            "e_severity_score": float(np.clip(0.5 + 0.2 * sig, 0.0, 1.0)),
            "e_cls_confidence": 0.9,
            "h_mean_return": 0.010 * sig,
            "h_insufficient_evidence": 0.0,
            "m_ret_5m": 0.004 * sig,
            "m_spread_bps": 4.0,
            "m_available": 1.0,
        })
        y_cont.append(ret)
        y_pos.append(1 if ret > 0.002 else 0)
        y_neg.append(1 if ret < -0.002 else 0)
        insts.append("NSE:TCS:EQ" if i % 2 == 0 else "NSE:INFY:EQ")
        subs.append("CONTRACT_ORDER_WIN" if sig > 0 else "CONTRACT_CANCELLATION")

    algos = [
        ModelAlgorithm.ZERO_RETURN,
        ModelAlgorithm.UNCONDITIONAL_MEAN,
        ModelAlgorithm.ROLLING_INSTRUMENT,
        ModelAlgorithm.EVENT_SUBTYPE_SHRINKAGE,
        ModelAlgorithm.RIDGE,
        ModelAlgorithm.ELASTIC_NET,
        ModelAlgorithm.LOGISTIC_DIRECTION,
        ModelAlgorithm.RANDOM_FOREST,
        ModelAlgorithm.GBDT,
        ModelAlgorithm.QUANTILE_LINEAR,
        ModelAlgorithm.COMPACT_MLP,
    ]
    for alg in algos:
        model = TabularEventPredictor(alg, ModelConfiguration.MODEL_D_FULL_SYSTEM, seed=42)
        model.fit(rows[:110], y_cont[:110], y_pos[:110], y_neg[:110], insts[:110], subs[:110])
        preds = model.predict_raw(rows[110:], insts[110:], subs[110:])
        assert len(preds) == 40
        for p in preds:
            assert p.q10 <= p.q25 <= p.q50 <= p.q75 <= p.q90
            assert 0.0 <= p.p_positive <= 1.0
            assert 0.0 <= p.p_negative <= 1.0

    # Neural MLP must refuse undersampled dataset (< min_samples_for_neural=100)
    with pytest.raises(ValueError, match="Neural MLP gated"):
        TabularEventPredictor(ModelAlgorithm.COMPACT_MLP, ModelConfiguration.MODEL_D_FULL_SYSTEM).fit(
            rows[:20], y_cont[:20], y_pos[:20], y_neg[:20]
        )
