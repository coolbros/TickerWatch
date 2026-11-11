"""Reproducible Phase 5 Experiment Runner (WP-J).

Executes two strictly separated workflows and persists verifiable JSON outputs to
`docs/alpha/benchmark_phase5_synthetic_results.json`:

1. Real-Data Empirical Readiness Check against `tickerwatch.db`, `data/alpha_historical_store.db`, and `data/`
   confirming whether empirical training is `READY`, `INSUFFICIENT_DATA`, or `BLOCKED`.
2. Labeled Synthetic Software Verification Suites (`is_synthetic=True`):
   - Suite A (`SYNTHETIC_MULTIMODAL_STRUCTURE`): Controlled multi-modal regime where
     M_t, E_t, and H_t carry complementary causal predictive structure. Evaluates
     Baselines + Models A, B, C, E, D (Ridge, GBDT, RF) on identical purged walk-forward
     folds, with validation-only Platt calibration, Holm-Bonferroni attribution, and
     cost-adjusted EXECUTABLE backtests + cost stress sweeps.
   - Suite B (`SYNTHETIC_PURE_NOISE_NULL`): Pure zero-signal noise stress test confirming
     abstention gating, zero false Holm-Bonferroni discoveries, and honest negative net PnL
     when no predictive signal exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Dict, List, Tuple
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.alpha.feature_registry import ModelConfiguration, get_feature_names_for_config
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.outcomes import (
    BenchmarkAdjustmentMethod,
    EventOutcomeLabel,
    OutcomeDataQualityStatus,
    OutcomeHorizon,
    OutcomeLabelType,
)
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_models import ModelAlgorithm
from backend.alpha.predictive_pipeline import (
    Phase5ExperimentReport,
    PredictiveResearchPipeline,
    default_ablation_matrix,
)
from backend.alpha.predictive_targets import CostAssumptionSpec, PredictiveTargetBuilder, PredictiveTargetRecord


def _generate_synthetic_cohort(
    n_events: int,
    seed: int,
    null_signal: bool = False,
) -> Tuple[List[PointInTimeFeatureRow], List[PredictiveTargetRecord], List[PredictiveTargetRecord]]:
    """Generate chronologically ordered causal feature rows + paired REACTION & EXECUTABLE targets."""
    rng = np.random.default_rng(seed)
    all_feat_names = get_feature_names_for_config(ModelConfiguration.MODEL_D_FULL_SYSTEM)
    cost_spec = CostAssumptionSpec.indian_nifty_fut_conservative()
    tb = PredictiveTargetBuilder(cost_spec=cost_spec)

    # Space events 28 hours apart so 1d (24h) horizon has clear train/val/test separation
    t0 = datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
    cutoff_ts = t0 + timedelta(hours=28 * (n_events + 10))
    instruments = ["NSE_INFY", "NSE_TCS", "NSE_RELIANCE", "NSE_HDFCBANK"]
    subtypes = [
        ("CORPORATE_FINANCIAL", "EARNINGS_ANNOUNCEMENT", "e_sub_earnings", "e_fam_corp_fin"),
        ("CORPORATE_FINANCIAL", "GUIDANCE_REVISION", "e_sub_guidance", "e_fam_corp_fin"),
        ("CORPORATE_OPERATIONAL", "CONTRACT_ORDER_WIN", "e_sub_contract_win", "e_fam_corp_op"),
        ("MARKET_SYSTEMIC", "REGULATORY_INVESTIGATION", "e_sub_reg_legal", "e_fam_market"),
    ]

    frows: List[PointInTimeFeatureRow] = []
    exec_targets: List[PredictiveTargetRecord] = []
    react_targets: List[PredictiveTargetRecord] = []

    for i in range(n_events):
        dec_ts = t0 + timedelta(hours=28 * i)
        inst = instruments[i % len(instruments)]
        fam_name, sub_name, sub_feat, fam_feat = subtypes[int(rng.integers(0, len(subtypes)))]

        vals: Dict[str, float | None] = {fname: 0.0 for fname in all_feat_names}
        # E_t latent causal drivers
        surp_z = float(rng.normal(0.0, 1.4))
        mat_comp = float(rng.uniform(0.35, 0.95))
        cls_conf = float(rng.uniform(0.68, 0.96))
        ent_conf = float(rng.uniform(0.75, 0.98))

        vals["e_cls_confidence"] = cls_conf
        vals["e_severity_score"] = float(min(1.0, mat_comp * 0.9))
        vals[fam_feat] = 1.0
        vals[sub_feat] = 1.0
        vals["e_entity_resolved"] = 1.0
        vals["e_entity_confidence"] = ent_conf
        vals["e_entity_count"] = 1.0
        vals["e_direct_inst_count"] = 1.0
        vals["e_mat_composite"] = mat_comp
        vals["e_mat_magnitude"] = float(rng.uniform(0.3, 0.9))
        vals["e_mat_forward"] = 1.0 if "GUIDANCE" in sub_name else 0.0
        vals["e_mat_novelty"] = float(rng.uniform(0.4, 0.95))
        vals["e_mat_spillover"] = float(rng.uniform(0.15, 0.55))
        vals["e_surprise_missing"] = 0.0
        vals["e_surprise_pct_signed"] = surp_z * 6.0
        vals["e_surprise_z"] = surp_z
        vals["e_surprise_dir_positive"] = 1.0 if surp_z > 0.35 else 0.0
        vals["e_surprise_dir_negative"] = 1.0 if surp_z < -0.35 else 0.0
        vals["e_composite_confidence"] = round(0.5 * (cls_conf + ent_conf), 4)

        # H_t historical analogue signals
        hist_bias = (0.0065 * surp_z + rng.normal(0.0, 0.0025)) if not null_signal else float(rng.normal(0.0, 0.006))
        matured_cnt = int(rng.integers(10, 35))
        pos_freq = float(np.clip(0.5 + hist_bias * 25.0 + rng.normal(0.0, 0.06), 0.1, 0.9))
        vals["h_comparable_count"] = float(matured_cnt + 2)
        vals["h_matched_count"] = float(matured_cnt + 1)
        vals["h_matured_count"] = float(matured_cnt)
        vals["h_sample_weight_saturation"] = min(1.0, matured_cnt / 30.0)
        vals["h_insufficient_evidence"] = 0.0
        vals["h_outcomes_missing"] = 0.0
        vals["h_mean_return"] = hist_bias
        vals["h_sim_weighted_return"] = hist_bias * 1.08
        vals["h_median_return"] = hist_bias * 0.95
        vals["h_return_std"] = float(rng.uniform(0.007, 0.015))
        vals["h_pos_freq"] = pos_freq
        vals["h_neg_freq"] = 1.0 - pos_freq
        vals["h_iqr_spread"] = 0.012
        vals["h_bootstrap_ci_width"] = 0.008
        vals["h_mean_similarity"] = float(rng.uniform(0.55, 0.88))
        vals["h_top1_semantic_sim"] = float(rng.uniform(0.60, 0.92))
        vals["h_inst_slice_mean"] = hist_bias * 0.9
        vals["h_inst_slice_matured_count"] = float(max(3, matured_cnt // 3))

        # M_t market microstructure & regime
        pre_ret_5m = float(rng.normal(0.0, 0.0025))
        pre_ret_30m = float(rng.normal(0.0, 0.0055))
        vol_elev = 1.0 if float(rng.random()) < 0.25 else 0.0
        spread_bps = float(rng.uniform(1.5, 4.5) + 1.5 * vol_elev)
        imb = float(rng.uniform(-0.5, 0.5))
        vals["m_available"] = 1.0
        vals["m_is_stale"] = 0.0
        vals["m_feed_freshness_ms"] = float(rng.uniform(25.0, 250.0))
        vals["m_ret_1m"] = float(rng.normal(0.0, 0.0012))
        vals["m_ret_5m"] = pre_ret_5m
        vals["m_ret_30m"] = pre_ret_30m
        vals["m_bench_ret_30m"] = float(rng.normal(0.0, 0.004))
        vals["m_realized_vol_bps"] = float(12.0 + 18.0 * vol_elev)
        vals["m_spread_bps"] = spread_bps
        vals["m_top_depth_log1p"] = float(rng.uniform(6.5, 9.0))
        vals["m_depth_imbalance"] = imb
        vals["m_microprice_offset_bps"] = float(imb * 0.6)
        vals["m_dir_bullish"] = 1.0 if pre_ret_30m > 0.002 else 0.0
        vals["m_dir_bearish"] = 1.0 if pre_ret_30m < -0.002 else 0.0
        vals["m_vol_elevated"] = vol_elev
        vals["m_liq_thin"] = 0.0
        vals["m_session_open"] = 1.0
        vals["m_session_progress"] = float(rng.uniform(0.1, 0.9))
        vals["m_is_first_30m"] = 1.0 if (i % 5 == 0) else 0.0
        vals["m_is_last_30m"] = 1.0 if (i % 7 == 0) else 0.0
        vals["m_futures_basis_missing"] = 1.0

        # Guard against typos/unregistered synthetic feature keys
        unknown_keys = set(vals.keys()) - set(all_feat_names)
        if unknown_keys:
            raise KeyError(f"Unregistered synthetic feature keys: {sorted(unknown_keys)}")

        # True future return generation
        eps = float(rng.normal(0.0, 0.0065 if not null_signal else 0.0110))
        if null_signal:
            exec_ret = eps
            react_ret = eps + float(rng.normal(0.0, 0.0015))
        else:
            # Multi-modal structural DGP: M contributes mild reversion/imbalance (~10% variance),
            # E contributes primary surprise + materiality interaction (~45%), H adds analogue persistence (~25%)
            m_component = 0.18 * pre_ret_30m + 0.0020 * imb - 0.0015 * vol_elev * np.sign(surp_z)
            e_component = 0.0062 * surp_z + 0.0045 * mat_comp * np.tanh(surp_z)
            h_component = 0.60 * hist_bias + 0.0060 * (pos_freq - 0.5)
            exec_ret = float(m_component + e_component + h_component + eps)
            # Reaction includes immediate pre-fill jump lost during latency
            react_ret = float(exec_ret + 0.0015 * np.sign(surp_z) if abs(surp_z) > 0.2 else exec_ret)

        frows.append(
            PointInTimeFeatureRow(
                event_id=f"synth_ev_{seed}_{i:04d}",
                instrument_id=inst,
                jurisdiction=MarketJurisdiction.IN,
                decision_timestamp=dec_ts,
                horizon_label=OutcomeHorizon.D1.value,
                is_synthetic=True,
                primary_issuer_id=inst,
                content_hash=f"synth_hash_{seed}_{i:04d}",
                values=vals,
                provenance={
                    "primary_family": fam_name,
                    "primary_subtype": sub_name,
                    "source": "SYNTHETIC_BENCHMARK",
                },
            )
        )

        # Build paired EventOutcomeLabels -> PredictiveTargetRecord
        exec_lbl = EventOutcomeLabel(
            event_id=f"synth_ev_{seed}_{i:04d}",
            instrument_id=inst,
            horizon=OutcomeHorizon.D1,
            label_type=OutcomeLabelType.EXECUTABLE,
            event_timestamp_utc=dec_ts - timedelta(seconds=2),
            decision_timestamp_utc=dec_ts,
            outcome_start_price_ts=dec_ts + timedelta(milliseconds=500),
            outcome_end_price_ts=dec_ts + timedelta(hours=6),
            pipeline_latency_ms=500.0,
            entry_quote_type="EXEC_HALF_SPREAD",
            entry_spread_bps_cost=spread_bps * 0.5,
            start_price=1000.0,
            end_price=1000.0 * (1.0 + exec_ret),
            raw_log_return=float(math.log(max(0.05, 1.0 + exec_ret))),
            simple_return=exec_ret,
            benchmark_instrument="NIFTY50",
            benchmark_return=0.0,
            benchmark_adjusted_return=exec_ret,
            benchmark_adjustment_method=BenchmarkAdjustmentMethod.SIMPLE_MARKET_ADJUSTED,
            avg_spread_bps=spread_bps,
            market_data_source="SYNTHETIC_MARKET_SIMULATOR",
            anchor_mode="POST_DECISION_EXECUTABLE",
            data_quality_status=OutcomeDataQualityStatus.VERIFIED,
            is_synthetic=True,
        )
        react_lbl = exec_lbl.model_copy(
            update={
                "label_type": OutcomeLabelType.REACTION,
                "outcome_start_price_ts": dec_ts - timedelta(seconds=1),
                "entry_quote_type": "PRE_EVENT_REF",
                "anchor_mode": "PRE_EVENT_AS_OF",
                "end_price": 1000.0 * (1.0 + react_ret),
                "raw_log_return": float(math.log(max(0.05, 1.0 + react_ret))),
                "simple_return": react_ret,
                "benchmark_adjusted_return": react_ret,
            }
        )
        exec_targets.append(tb.build_target(exec_lbl, MarketJurisdiction.IN, evaluation_cutoff_ts=cutoff_ts))
        react_targets.append(tb.build_target(react_lbl, MarketJurisdiction.IN, evaluation_cutoff_ts=cutoff_ts))

    return frows, exec_targets, react_targets


def _compact_report_dict(rep: Phase5ExperimentReport) -> Dict[str, object]:
    models_out = []
    for m in rep.model_results:
        bt = m.backtest_report
        models_out.append(
            {
                "label": m.spec.label,
                "algorithm": m.spec.algorithm.value,
                "config": m.spec.config.value,
                "oos_metrics": {
                    "n_samples": m.pooled_oos_metrics.n_samples,
                    "n_active_signals": m.pooled_oos_metrics.n_active_signals,
                    "abstention_rate": m.pooled_oos_metrics.abstention_rate,
                    "rmse": m.pooled_oos_metrics.rmse,
                    "mae": m.pooled_oos_metrics.mae,
                    "pearson_r": m.pooled_oos_metrics.pearson_r,
                    "spearman_ic": m.pooled_oos_metrics.spearman_ic,
                    "roc_auc": m.pooled_oos_metrics.roc_auc,
                    "pr_auc": m.pooled_oos_metrics.pr_auc,
                    "balanced_accuracy": m.pooled_oos_metrics.balanced_accuracy,
                    "brier_score": m.pooled_oos_metrics.brier_score,
                    "log_loss": m.pooled_oos_metrics.log_loss,
                    "ece": m.pooled_oos_metrics.ece,
                    "interval_coverage_80pct": m.pooled_oos_metrics.interval_coverage_80pct,
                },
                "fold_stability": m.cross_fold_stability.model_dump(),
                "executable_backtest": (
                    {
                        "trade_count": bt.trade_count,
                        "abstention_rate": bt.abstention_rate,
                        "gross_return_total": bt.gross_return_total,
                        "net_return_total": bt.net_return_total,
                        "gross_return_mean_bps": bt.gross_return_mean_bps,
                        "net_return_mean_bps": bt.net_return_mean_bps,
                        "win_rate": bt.win_rate,
                        "profit_factor": bt.profit_factor,
                        "annualized_sharpe_ratio": bt.annualized_sharpe_ratio,
                        "max_drawdown": bt.max_drawdown,
                        "tail_loss_p05_bps": bt.tail_loss_p05_bps,
                    }
                    if bt
                    else None
                ),
                "cost_sensitivity": [s.model_dump() for s in m.cost_sensitivity],
            }
        )
    return {
        "experiment_name": rep.experiment_name,
        "generated_at_utc": rep.generated_at_utc,
        "horizon": rep.horizon,
        "is_synthetic": rep.is_synthetic,
        "total_candidate_samples": rep.total_candidate_samples,
        "usable_trainable_samples": rep.usable_trainable_samples,
        "n_folds": rep.n_folds,
        "all_leakage_audits_passed": rep.all_leakage_audits_passed,
        "reference_model_label": rep.reference_model_label,
        "models": models_out,
        "alpha_attribution": rep.alpha_attribution.model_dump() if rep.alpha_attribution else None,
        "caveats": rep.caveats,
    }


def main() -> None:
    print("[1/3] Auditing real-data empirical readiness against tickerwatch.db and data/...")
    inventory = PredictiveResearchPipeline.audit_real_data_readiness()
    print(
        f"      -> IN={inventory.readiness_status_in.value}, "
        f"US={inventory.readiness_status_us.value}, "
        f"OVERALL={inventory.overall_empirical_training_status.value} "
        f"(real_events={inventory.real_events_count}, real_mkt_obs={inventory.valid_real_market_observations})"
    )

    print("[2/3] Running Suite A (SYNTHETIC multi-modal structural ablation: N=220, 3 purged folds)...")
    f_a, exec_a, _ = _generate_synthetic_cohort(n_events=220, seed=42, null_signal=False)
    rep_a = PredictiveResearchPipeline.run_experiment(
        experiment_name="SYNTHETIC_SUITE_A_MULTIMODAL",
        horizon=OutcomeHorizon.D1,
        feature_rows=f_a,
        target_records=exec_a,
        executable_targets=exec_a,
        model_specs=default_ablation_matrix(primary_algo=ModelAlgorithm.RIDGE, include_tree_models=True),
        reference_model_label="MODEL_D_FULL_RIDGE",
        n_folds=3,
        allow_synthetic=True,
        seed=42,
        real_data_inventory=inventory,
        n_bootstrap=500,
    )

    print("[3/3] Running Suite B (SYNTHETIC pure noise null-hypothesis stress test: N=180, 3 purged folds)...")
    f_b, exec_b, _ = _generate_synthetic_cohort(n_events=180, seed=99, null_signal=True)
    rep_b = PredictiveResearchPipeline.run_experiment(
        experiment_name="SYNTHETIC_SUITE_B_ZERO_SIGNAL_NULL",
        horizon=OutcomeHorizon.D1,
        feature_rows=f_b,
        target_records=exec_b,
        executable_targets=exec_b,
        model_specs=default_ablation_matrix(primary_algo=ModelAlgorithm.RIDGE, include_tree_models=False),
        reference_model_label="MODEL_D_FULL_RIDGE",
        n_folds=3,
        allow_synthetic=True,
        seed=99,
        real_data_inventory=inventory,
        n_bootstrap=500,
    )

    out_path = REPO_ROOT / "docs" / "alpha" / "benchmark_phase5_synthetic_results.json"
    payload = {
        "schema_version": "5.0.0-alpha",
        "real_data_inventory_summary": {
            "readiness_status_in": inventory.readiness_status_in.value,
            "readiness_status_us": inventory.readiness_status_us.value,
            "overall_empirical_training_status": inventory.overall_empirical_training_status.value,
            "real_events_count": inventory.real_events_count,
            "synthetic_events_count": inventory.synthetic_events_count,
            "valid_real_market_observations": inventory.valid_real_market_observations,
            "observed_timestamps_are_batch_backfill": inventory.observed_timestamps_are_batch_backfill,
            "events_with_real_consensus": inventory.events_with_real_consensus,
            "blocking_gaps": inventory.blocking_gaps,
        },
        "suite_a_synthetic_multimodal": _compact_report_dict(rep_a),
        "suite_b_synthetic_zero_signal_null": _compact_report_dict(rep_b),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved complete benchmark output to {out_path}")


if __name__ == "__main__":
    main()
