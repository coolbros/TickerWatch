"""End-to-End Predictive Research Pipeline Orchestrator (Phase 5 WP-J).

Orchestrates causal feature assembly, target construction, purged walk-forward splitting,
ablation training across Models A-E and baselines, validation-only probability/conformal
calibration, cost-aware backtesting on EXECUTABLE labels, Holm-Bonferroni alpha attribution,
and provenance registration into LocalModelRegistry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from pydantic import BaseModel, Field

from backend.alpha.data_readiness import RealDataReadinessAuditor, ReadinessStatus, RepositoryDataInventory
from backend.alpha.feature_registry import ModelConfiguration
from backend.alpha.historical_intelligence import HistoricalIntelligenceRepresentation
from backend.alpha.model_registry import LocalModelRegistry, compute_dataset_manifest_hash
from backend.alpha.nlp_pipeline import FinancialEventRepresentation
from backend.alpha.outcomes import EventOutcomeLabel, OutcomeHorizon, OutcomeLabelType
from backend.alpha.predictive_evaluation import (
    AlphaAttributionReport,
    AlphaAttributor,
    CrossFoldStabilitySummary,
    PredictiveMetricSummary,
    aggregate_fold_metrics,
    compute_predictive_metrics,
)
from backend.alpha.predictive_features import (
    FuturesBasisMetadata,
    PointInTimeFeatureBuilder,
    PointInTimeFeatureRow,
)
from backend.alpha.predictive_models import ModelAlgorithm, TabularEventPredictor
from backend.alpha.predictive_targets import (
    CostAssumptionSpec,
    PredictiveTargetBuilder,
    PredictiveTargetRecord,
)
from backend.alpha.prediction_uncertainty import (
    CalibratedPrediction,
    CalibrationMethod,
    UncertaintyCalibratorAndGate,
)
from backend.alpha.research_backtest import (
    BacktestSensitivityPoint,
    ResearchBacktestReport,
    ResearchBacktester,
)
from backend.alpha.walk_forward import PurgedWalkForwardSplitter, WalkForwardFold, WalkForwardMode


class ModelExperimentSpec(BaseModel):
    """Named model + feature-ablation configuration tested identically across walk-forward folds."""

    label: str
    algorithm: ModelAlgorithm
    config: ModelConfiguration
    calibration_method: CalibrationMethod = CalibrationMethod.PLATT
    ridge_alpha: float = 5.0
    rf_n_estimators: int = 20
    gbdt_n_estimators: int = 25
    gbdt_lr: float = 0.08
    min_directional_prob: float = 0.52
    min_edge_over_cost_bps: float = 1.5
    max_predictive_std: float = 0.25


class ModelPooledEvaluation(BaseModel):
    """Pooled out-of-sample statistical metrics, fold stability, and executable backtest."""

    spec: ModelExperimentSpec
    pooled_oos_metrics: PredictiveMetricSummary
    cross_fold_stability: CrossFoldStabilitySummary
    backtest_report: Optional[ResearchBacktestReport] = None
    cost_sensitivity: List[BacktestSensitivityPoint] = Field(default_factory=list)
    registered_model_ids: List[str] = Field(default_factory=list)


class Phase5ExperimentReport(BaseModel):
    """Full reproducible research output for one horizon and experiment matrix."""

    experiment_name: str
    generated_at_utc: str
    horizon: str
    is_synthetic: bool
    real_data_readiness_status: Optional[ReadinessStatus] = None
    total_candidate_samples: int
    usable_trainable_samples: int
    excluded_sample_count: int
    n_folds: int
    all_leakage_audits_passed: bool
    reference_model_label: str
    model_results: List[ModelPooledEvaluation] = Field(default_factory=list)
    alpha_attribution: Optional[AlphaAttributionReport] = None
    caveats: List[str] = Field(default_factory=list)


def default_ablation_matrix(
    primary_algo: ModelAlgorithm = ModelAlgorithm.RIDGE,
    include_tree_models: bool = True,
) -> List[ModelExperimentSpec]:
    """Canonical Phase 5 experimental matrix covering Baselines and Models A-E."""
    specs = [
        ModelExperimentSpec(
            label="BASELINE_ZERO",
            algorithm=ModelAlgorithm.ZERO_RETURN,
            config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
            calibration_method=CalibrationMethod.NONE,
        ),
        ModelExperimentSpec(
            label="BASELINE_MEAN",
            algorithm=ModelAlgorithm.UNCONDITIONAL_MEAN,
            config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
            calibration_method=CalibrationMethod.NONE,
        ),
        ModelExperimentSpec(
            label="BASELINE_ROLLING_INST",
            algorithm=ModelAlgorithm.ROLLING_INSTRUMENT,
            config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
            calibration_method=CalibrationMethod.NONE,
        ),
        ModelExperimentSpec(
            label="BASELINE_SUBTYPE_SHRINK",
            algorithm=ModelAlgorithm.EVENT_SUBTYPE_SHRINKAGE,
            config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
            calibration_method=CalibrationMethod.NONE,
        ),
        ModelExperimentSpec(
            label=f"MODEL_A_MARKET_ONLY_{primary_algo.value}",
            algorithm=primary_algo,
            config=ModelConfiguration.MODEL_A_MARKET_ONLY,
        ),
        ModelExperimentSpec(
            label=f"MODEL_B_EVENT_ONLY_{primary_algo.value}",
            algorithm=primary_algo,
            config=ModelConfiguration.MODEL_B_EVENT_ONLY,
        ),
        ModelExperimentSpec(
            label=f"MODEL_C_EVENT_HIST_{primary_algo.value}",
            algorithm=primary_algo,
            config=ModelConfiguration.MODEL_C_EVENT_PLUS_HIST,
        ),
        ModelExperimentSpec(
            label=f"MODEL_E_EVENT_MARKET_{primary_algo.value}",
            algorithm=primary_algo,
            config=ModelConfiguration.MODEL_E_EVENT_PLUS_MARKET,
        ),
        ModelExperimentSpec(
            label=f"MODEL_D_FULL_{primary_algo.value}",
            algorithm=primary_algo,
            config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
        ),
    ]
    if include_tree_models:
        specs.append(
            ModelExperimentSpec(
                label="MODEL_D_FULL_GBDT",
                algorithm=ModelAlgorithm.GBDT,
                config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
            )
        )
        specs.append(
            ModelExperimentSpec(
                label="MODEL_D_FULL_RF",
                algorithm=ModelAlgorithm.RANDOM_FOREST,
                config=ModelConfiguration.MODEL_D_FULL_SYSTEM,
            )
        )
    return specs


class PredictiveResearchPipeline:
    """Orchestrates feature/target construction, walk-forward validation, calibration, and attribution."""

    @staticmethod
    def build_features_and_targets(
        horizon: OutcomeHorizon,
        event_reps: Sequence[FinancialEventRepresentation],
        hist_reps: Sequence[HistoricalIntelligenceRepresentation],
        outcome_labels: Sequence[EventOutcomeLabel],
        futures_metas: Optional[Sequence[Optional[FuturesBasisMetadata]]] = None,
        cost_spec: Optional[CostAssumptionSpec] = None,
        use_market_adjusted: bool = False,
        evaluation_cutoff_ts: Optional[datetime] = None,
        is_synthetic: bool = False,
    ) -> Tuple[List[PointInTimeFeatureRow], List[PredictiveTargetRecord]]:
        n = len(event_reps)
        if not (len(hist_reps) == n and len(outcome_labels) == n):
            raise ValueError("event_reps, hist_reps, and outcome_labels must have equal length.")
        fb_feat = PointInTimeFeatureBuilder(horizon=horizon)
        tb = PredictiveTargetBuilder(cost_spec=cost_spec, use_market_adjusted_as_continuous=use_market_adjusted)

        frows: List[PointInTimeFeatureRow] = []
        tgts: List[PredictiveTargetRecord] = []
        for idx in range(n):
            fmeta = futures_metas[idx] if futures_metas is not None else None
            frow = fb_feat.build_row(
                rep=event_reps[idx],
                hist_rep=hist_reps[idx],
                futures_meta=fmeta,
                is_synthetic=is_synthetic or hist_reps[idx].provenance.is_synthetic,
            )
            # Attach exact subtype metadata cleanly into provenance for subtype baselines & breakdowns
            frow.provenance["primary_family"] = event_reps[idx].classification.primary_family.value
            frow.provenance["primary_subtype"] = event_reps[idx].classification.primary_subtype.value
            frows.append(frow)
            tgts.append(
                tb.build_target(
                    outcome_label=outcome_labels[idx],
                    jurisdiction=hist_reps[idx].jurisdiction,
                    evaluation_cutoff_ts=evaluation_cutoff_ts,
                )
            )
        return frows, tgts

    @staticmethod
    def audit_real_data_readiness(
        legacy_db_path: Optional[str | Path] = Path("tickerwatch.db"),
        historical_store_path: Optional[str | Path] = Path("data/alpha_historical_store.db"),
        data_dir: Optional[str | Path] = Path("data"),
    ) -> RepositoryDataInventory:
        auditor = RealDataReadinessAuditor(
            legacy_db_path=Path(legacy_db_path) if legacy_db_path else None,
            historical_store_path=Path(historical_store_path) if historical_store_path else None,
            data_dir=Path(data_dir) if data_dir else None,
        )
        return auditor.audit()

    @classmethod
    def run_experiment(
        cls,
        experiment_name: str,
        horizon: OutcomeHorizon,
        feature_rows: Sequence[PointInTimeFeatureRow],
        target_records: Sequence[PredictiveTargetRecord],
        executable_targets: Optional[Sequence[PredictiveTargetRecord]] = None,
        model_specs: Optional[Sequence[ModelExperimentSpec]] = None,
        reference_model_label: Optional[str] = None,
        n_folds: int = 3,
        mode: WalkForwardMode = WalkForwardMode.EXPANDING,
        embargo_seconds: Optional[float] = None,
        issuer_cycle_purge_seconds: float = 0.0,
        allow_synthetic: bool = False,
        seed: int = 42,
        registry: Optional[LocalModelRegistry] = None,
        real_data_inventory: Optional[RepositoryDataInventory] = None,
        n_bootstrap: int = 600,
    ) -> Phase5ExperimentReport:
        if len(feature_rows) != len(target_records) or len(feature_rows) == 0:
            raise ValueError("feature_rows and target_records must be non-empty and aligned.")

        exec_tgts = executable_targets if executable_targets is not None else target_records
        if len(exec_tgts) != len(target_records):
            raise ValueError("executable_targets length must equal target_records length.")

        is_synth = any(r.is_synthetic or t.is_synthetic for r, t in zip(feature_rows, target_records))
        splitter = PurgedWalkForwardSplitter(
            horizon=horizon,
            n_folds=n_folds,
            mode=mode,
            val_fraction_within_fold=0.25,
            embargo_seconds=embargo_seconds,
            issuer_cycle_purge_seconds=issuer_cycle_purge_seconds,
            allow_synthetic=allow_synthetic,
        )
        folds, usable_indices = splitter.generate_folds(feature_rows, target_records)
        all_audits_passed = all(f.leakage_audit.passed for f in folds)

        specs = list(model_specs or default_ablation_matrix())
        d_specs = [s for s in specs if s.config == ModelConfiguration.MODEL_D_FULL_SYSTEM and s.algorithm != ModelAlgorithm.ZERO_RETURN and not s.label.startswith("BASELINE_")]
        ref_label = reference_model_label or (d_specs[0].label if d_specs else specs[-1].label)
        if not any(s.label == ref_label for s in specs):
            ref_label = specs[-1].label

        subtypes_all = [str((r.provenance or {}).get("primary_subtype", "UNKNOWN")) for r in feature_rows]

        # Collect pooled OOS predictions mapped by spec.label to keep exact alignment
        pooled_preds: Dict[str, List[CalibratedPrediction]] = {s.label: [] for s in specs}
        pooled_test_indices: List[int] = []
        fold_idx_recorded = False

        results: List[ModelPooledEvaluation] = []
        dhash = compute_dataset_manifest_hash(
            [r.event_id for r in feature_rows],
            [r.decision_timestamp for r in feature_rows],
            is_synth,
        )

        for spec in specs:
            fold_summaries: List[PredictiveMetricSummary] = []
            spec_oos_preds: List[CalibratedPrediction] = []
            spec_oos_exec_tgts: List[PredictiveTargetRecord] = []
            spec_oos_frows: List[PointInTimeFeatureRow] = []
            spec_oos_y_cont: List[float] = []
            spec_oos_y_pos: List[int] = []
            reg_ids: List[str] = []

            for fold in folds:
                tr_idx = fold.train_indices
                val_idx = fold.val_indices
                te_idx = fold.test_indices

                if not fold_idx_recorded:
                    pooled_test_indices.extend(te_idx)

                tr_x = [feature_rows[i].subset(spec.config) for i in tr_idx]
                tr_y_c = [float(target_records[i].y_continuous) for i in tr_idx]
                tr_y_p = [int(target_records[i].y_positive_material or 0) for i in tr_idx]
                tr_y_n = [int(target_records[i].y_negative_material or 0) for i in tr_idx]
                tr_inst = [feature_rows[i].instrument_id for i in tr_idx]
                tr_sub = [subtypes_all[i] for i in tr_idx]

                predictor = TabularEventPredictor(
                    algorithm=spec.algorithm,
                    config=spec.config,
                    seed=seed + fold.fold_id,
                    ridge_alpha=spec.ridge_alpha,
                    rf_n_estimators=spec.rf_n_estimators,
                    gbdt_n_estimators=spec.gbdt_n_estimators,
                    gbdt_lr=spec.gbdt_lr,
                ).fit(
                    tr_x,
                    tr_y_c,
                    tr_y_p,
                    tr_y_n,
                    instrument_ids=tr_inst,
                    subtypes=tr_sub,
                )

                calibrator = UncertaintyCalibratorAndGate(
                    method=spec.calibration_method,
                    model_config=spec.config,
                    min_directional_prob=spec.min_directional_prob,
                    min_edge_over_cost_bps=spec.min_edge_over_cost_bps,
                    max_predictive_std=spec.max_predictive_std,
                )
                if val_idx:
                    val_x = [feature_rows[i].subset(spec.config) for i in val_idx]
                    val_raw = predictor.predict_raw(
                        val_x,
                        instrument_ids=[feature_rows[i].instrument_id for i in val_idx],
                        subtypes=[subtypes_all[i] for i in val_idx],
                    )
                    calibrator.fit_calibration(
                        val_raw_preds=val_raw,
                        val_y_continuous=[float(target_records[i].y_continuous) for i in val_idx],
                        val_y_pos=[int(target_records[i].y_positive_material or 0) for i in val_idx],
                        val_y_neg=[int(target_records[i].y_negative_material or 0) for i in val_idx],
                    )

                te_x = [feature_rows[i].subset(spec.config) for i in te_idx]
                te_raw = predictor.predict_raw(
                    te_x,
                    instrument_ids=[feature_rows[i].instrument_id for i in te_idx],
                    subtypes=[subtypes_all[i] for i in te_idx],
                )
                te_costs = [
                    float(exec_tgts[i].transaction_cost_assumption_bps or 10.5) for i in te_idx
                ]
                te_cal = calibrator.calibrate_and_gate(
                    event_ids=[feature_rows[i].event_id for i in te_idx],
                    instrument_ids=[feature_rows[i].instrument_id for i in te_idx],
                    raw_preds=te_raw,
                    feature_dicts=te_x,
                    round_trip_cost_bps_list=te_costs,
                )

                te_yc = [float(target_records[i].y_continuous) for i in te_idx]
                te_yp = [int(target_records[i].y_positive_material or 0) for i in te_idx]

                f_sum = compute_predictive_metrics(
                    model_label=spec.label,
                    horizon_label=horizon.value,
                    predictions=te_cal,
                    y_continuous=te_yc,
                    y_positive=te_yp,
                )
                fold_summaries.append(f_sum)
                spec_oos_preds.extend(te_cal)
                spec_oos_y_cont.extend(te_yc)
                spec_oos_y_pos.extend(te_yp)
                spec_oos_frows.extend(feature_rows[i] for i in te_idx)
                spec_oos_exec_tgts.extend(exec_tgts[i] for i in te_idx)

                if registry is not None:
                    # Also compute in-sample train metric safely for provenance
                    tr_raw = predictor.predict_raw(tr_x, instrument_ids=tr_inst, subtypes=tr_sub)
                    tr_cal = calibrator.calibrate_and_gate(
                        event_ids=[feature_rows[i].event_id for i in tr_idx],
                        instrument_ids=tr_inst,
                        raw_preds=tr_raw,
                        feature_dicts=tr_x,
                    )
                    tr_sum = compute_predictive_metrics(spec.label, horizon.value, tr_cal, tr_y_c, tr_y_p)
                    mid = f"{experiment_name}__{spec.label}__fold{fold.fold_id}"
                    rec = registry.register_run(
                        model_id=mid,
                        model_version="5.0.0-alpha",
                        model_config=spec.config,
                        horizon_label=horizon.value,
                        predictor=predictor,
                        calibrator=calibrator,
                        dataset_manifest_hash=dhash,
                        training_cutoff_utc=fold.train_end,
                        validation_period=(
                            f"{fold.val_start.isoformat()}..{fold.val_end.isoformat()}"
                            if (fold.val_start and fold.val_end)
                            else "NONE"
                        ),
                        test_period=f"{fold.test_start.isoformat()}..{fold.test_end.isoformat()}",
                        train_metrics={"rmse": tr_sum.rmse, "spearman_ic": tr_sum.spearman_ic, "brier": tr_sum.brier_score},
                        oos_metrics={"rmse": f_sum.rmse, "spearman_ic": f_sum.spearman_ic, "brier": f_sum.brier_score, "roc_auc": f_sum.roc_auc},
                        calibration_summary={"ece": f_sum.ece, "coverage_80pct": f_sum.interval_coverage_80pct},
                        leakage_audit_passed=fold.leakage_audit.passed,
                        is_synthetic=is_synth,
                        known_limitations=(
                            ["Synthetic experimental benchmark; NOT live empirical market alpha."]
                            if is_synth
                            else ["Experimental research run; verify empirical data prerequisites."]
                        ),
                    )
                    reg_ids.append(rec.model_id)

            fold_idx_recorded = True
            pooled_preds[spec.label] = spec_oos_preds

            pooled_metric = compute_predictive_metrics(
                model_label=spec.label,
                horizon_label=horizon.value,
                predictions=spec_oos_preds,
                y_continuous=spec_oos_y_cont,
                y_positive=spec_oos_y_pos,
            )
            stability = aggregate_fold_metrics(fold_summaries)

            bt_report: Optional[ResearchBacktestReport] = None
            sweep: List[BacktestSensitivityPoint] = []
            if all(t.label_type == OutcomeLabelType.EXECUTABLE for t in spec_oos_exec_tgts):
                bt_report = ResearchBacktester.run(
                    model_label=spec.label,
                    predictions=spec_oos_preds,
                    targets=spec_oos_exec_tgts,
                    feature_rows=spec_oos_frows,
                )
                sweep = ResearchBacktester.run_cost_sensitivity_sweep(
                    model_label=spec.label,
                    predictions=spec_oos_preds,
                    targets=spec_oos_exec_tgts,
                    feature_rows=spec_oos_frows,
                )

            results.append(
                ModelPooledEvaluation(
                    spec=spec,
                    pooled_oos_metrics=pooled_metric,
                    cross_fold_stability=stability,
                    backtest_report=bt_report,
                    cost_sensitivity=sweep,
                    registered_model_ids=reg_ids,
                )
            )

        # Incremental Alpha Attribution comparing reference model vs all other specs
        ref_preds = pooled_preds[ref_label]
        comps = {k: v for k, v in pooled_preds.items() if k != ref_label}
        y_oos_cont = [float(target_records[i].y_continuous) for i in pooled_test_indices]
        y_oos_pos = [int(target_records[i].y_positive_material or 0) for i in pooled_test_indices]
        attr: Optional[AlphaAttributionReport] = None
        if comps and y_oos_cont:
            attr = AlphaAttributor.compare_candidate_vs_comparators(
                candidate_label=ref_label,
                candidate_preds=ref_preds,
                comparators=comps,
                y_continuous=y_oos_cont,
                y_positive=y_oos_pos,
                horizon_label=horizon.value,
                is_synthetic=is_synth,
                n_bootstrap=n_bootstrap,
                seed=seed,
            )

        caveats = []
        if is_synth:
            caveats.append(
                "ALL METRICS DERIVED FROM LABELED SYNTHETIC SIMULATIONS (is_synthetic=True). "
                "Do NOT interpret as real-market empirical alpha."
            )
        if real_data_inventory is not None and real_data_inventory.overall_empirical_training_status != ReadinessStatus.READY:
            caveats.append(
                f"Real-data empirical status: {real_data_inventory.overall_empirical_training_status.value}. "
                + "; ".join(real_data_inventory.blocking_gaps[:3])
            )

        return Phase5ExperimentReport(
            experiment_name=experiment_name,
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            horizon=horizon.value,
            is_synthetic=is_synth,
            real_data_readiness_status=(
                real_data_inventory.overall_empirical_training_status if real_data_inventory else None
            ),
            total_candidate_samples=len(feature_rows),
            usable_trainable_samples=len(usable_indices),
            excluded_sample_count=len(feature_rows) - len(usable_indices),
            n_folds=len(folds),
            all_leakage_audits_passed=all_audits_passed,
            reference_model_label=ref_label,
            model_results=results,
            alpha_attribution=attr,
            caveats=caveats,
        )
