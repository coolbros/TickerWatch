"""Real-World Shadow Validation Protocol & Model Eligibility Gate (Phase 6 WP-I).

Enforces strict eligibility verification on registered Phase 5 models, segregates
genuine live observations (`is_synthetic=False`) from synthetic diagnostic replays,
evaluates outcomes across instruments, event subtypes, horizons, market regimes,
confidence buckets, and time-of-day windows, and tracks a minimum 20-session /
preferred 30-session collection gate without falsely declaring statistical maturity early.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import math
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.feature_registry import FEATURE_SCHEMA_VERSION
from backend.alpha.model_registry import LocalModelRegistry, ModelArtifactRecord
from backend.alpha.predictive_evaluation import _roc_auc, _spearman_ic
from backend.alpha.prediction_uncertainty import compute_calibration_metrics
from backend.alpha.shadow_execution import SimulatedShadowTrade
from backend.alpha.shadow_ledger import (
    ModelValidationStatus,
    ShadowLedgerEntry,
    ShadowOutcomeRecord,
    ShadowSignalLedger,
)
from backend.alpha.timestamps import get_current_utc

IST_ZONE = ZoneInfo("Asia/Kolkata")


class ModelEligibilityResult(BaseModel):
    """Result of evaluating a Phase 5 registered artifact against Phase 6 promotion rules."""

    model_id: str
    model_version: str
    eligible_for_real_shadow: bool
    validation_status: ModelValidationStatus
    passed_checks: List[str] = Field(default_factory=list)
    failed_checks: List[str] = Field(default_factory=list)
    provenance_summary: Dict[str, Any] = Field(default_factory=dict)


class ModelEligibilityAuditor:
    """Strict 6-gate checker preventing synthetic or unvalidated models from mislabeling live runs."""

    @staticmethod
    def evaluate_registry_model(
        registry: LocalModelRegistry,
        model_id: str,
        explicit_human_approval: bool = False,
    ) -> ModelEligibilityResult:
        rec: ModelArtifactRecord = registry.get_run(model_id)
        passed: List[str] = []
        failed: List[str] = []

        if rec.is_synthetic:
            failed.append("TRAINED_ON_SYNTHETIC_DATA: strictly diagnostic only")
        else:
            passed.append("REAL_MARKET_TRAINING_DATA")

        if rec.dataset_manifest_hash and rec.training_cutoff_utc and rec.validation_period:
            passed.append("DATASET_AND_CUTOFF_DOCUMENTED")
        else:
            failed.append("MISSING_DATASET_OR_BOUNDARY_LINEAGE")

        if rec.oos_metrics and len(rec.oos_metrics) > 0:
            passed.append("OOS_WALK_FORWARD_METRICS_PRESENT")
        else:
            failed.append("MISSING_OOS_VALIDATION_METRICS")

        if rec.feature_schema_version == FEATURE_SCHEMA_VERSION and rec.feature_manifest_sha256:
            passed.append("PREPROCESSING_AND_FEATURE_SCHEMA_FROZEN")
        else:
            failed.append("FEATURE_SCHEMA_MISMATCH_OR_UNFROZEN")

        if registry.verify_artifact_integrity(model_id):
            passed.append("REGISTRY_ARTIFACT_SHA256_VERIFIED")
        else:
            failed.append("REGISTRY_ARTIFACT_INTEGRITY_FAILED")

        if rec.leakage_audit_passed:
            passed.append("ZERO_UNRESOLVED_LEAKAGE_AUDITS")
        else:
            failed.append("LEAKAGE_AUDIT_FAILED")

        if not explicit_human_approval:
            failed.append("EXPLICIT_SHADOW_APPROVAL_FLAG_FALSE")
        else:
            passed.append("EXPLICIT_SHADOW_APPROVAL_GRANTED")

        if rec.is_synthetic:
            return ModelEligibilityResult(
                model_id=rec.model_id,
                model_version=rec.model_version,
                eligible_for_real_shadow=False,
                validation_status=ModelValidationStatus.SYNTHETIC_DIAGNOSTIC_ONLY,
                passed_checks=passed,
                failed_checks=failed,
                provenance_summary={"dataset_hash": rec.dataset_manifest_hash, "git_commit": rec.git_commit_hash},
            )

        is_elig = len(failed) == 0
        return ModelEligibilityResult(
            model_id=rec.model_id,
            model_version=rec.model_version,
            eligible_for_real_shadow=is_elig,
            validation_status=ModelValidationStatus.MODEL_ELIGIBLE if is_elig else ModelValidationStatus.MODEL_UNVALIDATED,
            passed_checks=passed,
            failed_checks=failed,
            provenance_summary={"dataset_hash": rec.dataset_manifest_hash, "git_commit": rec.git_commit_hash},
        )


class HorizonMaturityStats(BaseModel):
    horizon_label: str
    total_linked: int
    matured_count: int
    immature_count: int


class ShadowStratifiedSlice(BaseModel):
    dimension: str
    bucket_key: str
    observation_count: int
    matured_count: int
    active_signals: int
    rmse: Optional[float] = None
    mean_executable_return_bps: Optional[float] = None
    mean_net_return_bps: Optional[float] = None
    win_rate: Optional[float] = None


class ShadowValidationReport(BaseModel):
    """Strict 20/30-session empirical or clearly labeled diagnostic evaluation report."""

    generated_at_utc: datetime = Field(default_factory=get_current_utc)
    is_synthetic_cohort: bool
    minimum_session_window_target: int = 20
    preferred_session_window_target: int = 30
    empirical_status: str  # "NO_REAL_OBSERVATIONS", "COLLECTING_OBSERVATION_WINDOW", "WINDOW_MET_READY_FOR_STAT_REVIEW", "SYNTHETIC_DIAGNOSTIC_ONLY"

    real_observation_count: int
    unique_independent_event_count: int
    event_families_observed: List[str] = Field(default_factory=list)
    issuers_observed: List[str] = Field(default_factory=list)
    instruments_observed: List[str] = Field(default_factory=list)
    market_sessions_observed: int
    market_session_dates_ist: List[str] = Field(default_factory=list)

    eligible_predictions_count: int
    diagnostic_or_unvalidated_count: int
    abstention_count: int
    abstention_rate: float

    outcome_maturity_by_horizon: List[HorizonMaturityStats] = Field(default_factory=list)
    matured_prediction_count: int = 0
    realized_mae: Optional[float] = None
    realized_rmse: Optional[float] = None
    spearman_ic: Optional[float] = None
    roc_auc: Optional[float] = None
    brier_score: Optional[float] = None
    calibration_ece: Optional[float] = None

    # Executable paper performance (when trades simulated or outcomes joined)
    simulated_trade_count: int = 0
    mean_gross_theoretical_bps: Optional[float] = None
    mean_executable_bps: Optional[float] = None
    mean_net_after_costs_bps: Optional[float] = None
    win_rate: Optional[float] = None
    data_quality_problems: Dict[str, int] = Field(default_factory=dict)

    by_instrument: List[ShadowStratifiedSlice] = Field(default_factory=list)
    by_event_subtype: List[ShadowStratifiedSlice] = Field(default_factory=list)
    by_horizon: List[ShadowStratifiedSlice] = Field(default_factory=list)
    by_market_regime: List[ShadowStratifiedSlice] = Field(default_factory=list)
    by_confidence_bucket: List[ShadowStratifiedSlice] = Field(default_factory=list)
    by_time_of_day: List[ShadowStratifiedSlice] = Field(default_factory=list)

    scientific_caveat: str


def _base_root_event_id(event_id: str) -> str:
    # Strip trailing '_v2', '_rev1' revision suffixes if appended
    if "_v" in event_id and event_id.rsplit("_v", 1)[1].isdigit():
        return event_id.rsplit("_v", 1)[0]
    return event_id


def _time_of_day_bucket(ts_utc: datetime) -> str:
    loc = ts_utc.astimezone(IST_ZONE)
    if loc.weekday() >= 5:
        return "WEEKEND_OFF_HOURS"
    mins = loc.hour * 60 + loc.minute
    if mins < 9 * 60 + 15 or mins > 15 * 60 + 30:
        return "OFF_SESSION"
    if mins <= 9 * 60 + 45:
        return "OPEN_FIRST_30M"
    if mins >= 15 * 60:
        return "CLOSE_LAST_30M"
    return "MID_SESSION"


def _confidence_bucket(entry: ShadowLedgerEntry) -> str:
    pp = (entry.predicted_direction_probabilities or {}).get("p_positive")
    pn = (entry.predicted_direction_probabilities or {}).get("p_negative")
    if pp is None or pn is None:
        return "NO_PROB_OR_ABSTAIN"
    c = max(float(pp), float(pn))
    if c >= 0.70:
        return "HIGH_>=0.70"
    if c >= 0.58:
        return "MEDIUM_0.58-0.70"
    return "LOW_<0.58"


class ShadowValidationEvaluator:
    """Evaluates accumulated shadow ledger entries & matured outcomes with strict real/synthetic segregation."""

    @classmethod
    def evaluate(
        cls,
        ledger: ShadowSignalLedger,
        include_synthetic_only_cohort: bool = False,
        simulated_trades_by_pred_id: Optional[Dict[str, SimulatedShadowTrade]] = None,
        min_sessions: int = 20,
        preferred_sessions: int = 30,
    ) -> ShadowValidationReport:
        # Strictly exclude synthetic samples when evaluating real empirical status
        all_entries = ledger.list_predictions(include_synthetic=include_synthetic_only_cohort)
        if include_synthetic_only_cohort:
            entries = [e for e in all_entries if e.is_synthetic]
        else:
            entries = [e for e in all_entries if not e.is_synthetic]

        if not entries:
            return ShadowValidationReport(
                is_synthetic_cohort=include_synthetic_only_cohort,
                minimum_session_window_target=min_sessions,
                preferred_session_window_target=preferred_sessions,
                empirical_status="SYNTHETIC_DIAGNOSTIC_ONLY" if include_synthetic_only_cohort else "NO_REAL_OBSERVATIONS",
                real_observation_count=0,
                unique_independent_event_count=0,
                market_sessions_observed=0,
                eligible_predictions_count=0,
                diagnostic_or_unvalidated_count=0,
                abstention_count=0,
                abstention_rate=0.0,
                scientific_caveat=(
                    "Zero non-synthetic real shadow observations recorded yet; continue real feed capture before empirical claims."
                    if not include_synthetic_only_cohort
                    else "Synthetic replay cohort empty."
                ),
            )

        trades_map = simulated_trades_by_pred_id or {}

        unique_roots = {_base_root_event_id(e.event_id) for e in entries}
        families = sorted({
            str((e.event_representation or {}).get("classification", {}).get("primary_family", "UNKNOWN"))
            for e in entries
        })
        issuers = sorted({
            str((e.event_representation or {}).get("primary_entity", {}).get("entity_id") or "UNRESOLVED")
            for e in entries
        })
        instruments = sorted({e.instrument_id for e in entries})
        session_dates = sorted({
            e.decision_timestamp_utc.astimezone(IST_ZONE).date().isoformat()
            for e in entries
            if e.decision_timestamp_utc.astimezone(IST_ZONE).weekday() < 5
        })
        n_sessions = len(session_dates)

        elig_cnt = sum(1 for e in entries if e.model_validation_status == ModelValidationStatus.MODEL_ELIGIBLE)
        diag_cnt = len(entries) - elig_cnt
        abst_cnt = sum(1 for e in entries if e.no_trade_status)
        q_counter: Counter[str] = Counter()

        mat_by_h: Dict[str, List[int]] = defaultdict(list)
        joined_rows: List[Dict[str, Any]] = []

        for e in entries:
            for fl in e.input_data_quality_flags:
                q_counter[fl] += 1
            outs = ledger.get_outcomes_for_prediction(e.prediction_id)
            for o in outs:
                for qf in o.data_quality_flags:
                    q_counter[qf] += 1
                mat_by_h[o.horizon_label].append(1 if o.outcome_matured else 0)

            matured_exec = [o for o in outs if o.outcome_matured and o.executable_realized_return is not None]
            latest_out: Optional[ShadowOutcomeRecord] = matured_exec[-1] if matured_exec else None
            trade: Optional[SimulatedShadowTrade] = trades_map.get(e.prediction_id)

            joined_rows.append(
                {
                    "entry": e,
                    "outcome": latest_out,
                    "trade": trade,
                    "instrument_id": e.instrument_id,
                    "subtype": str((e.event_representation or {}).get("classification", {}).get("primary_subtype", "UNKNOWN")),
                    "horizon": e.forecast_horizon,
                    "regime": str((e.market_regime_representation or {}).get("volatility_regime", "UNAVAILABLE")),
                    "confidence_bucket": _confidence_bucket(e),
                    "time_of_day": _time_of_day_bucket(e.decision_timestamp_utc),
                }
            )

        maturity_stats = [
            HorizonMaturityStats(
                horizon_label=h,
                total_linked=len(flags),
                matured_count=sum(flags),
                immature_count=len(flags) - sum(flags),
            )
            for h, flags in sorted(mat_by_h.items())
        ]

        y_hats: List[float] = []
        y_reals: List[float] = []
        p_positives: List[float] = []
        y_bins: List[int] = []

        gross_bps_list: List[float] = []
        exec_bps_list: List[float] = []
        net_bps_list: List[float] = []

        for jr in joined_rows:
            e = jr["entry"]
            out = jr["outcome"]
            tr = jr["trade"]
            if out is not None and out.executable_realized_return is not None:
                yr = float(out.executable_realized_return)
                if e.expected_return is not None:
                    y_hats.append(float(e.expected_return))
                    y_reals.append(yr)
                pp = (e.predicted_direction_probabilities or {}).get("p_positive")
                if pp is not None:
                    p_positives.append(float(pp))
                    y_bins.append(1 if yr > 0.0 else 0)
            if tr is not None and tr.filled and tr.net_return_after_costs is not None:
                if tr.gross_theoretical_return is not None:
                    gross_bps_list.append(tr.gross_theoretical_return * 10000.0)
                if tr.estimated_executable_return is not None:
                    exec_bps_list.append(tr.estimated_executable_return * 10000.0)
                net_bps_list.append(tr.net_return_after_costs * 10000.0)

        mae = rmse = sic = roc = brier = ece = None
        if len(y_reals) >= 2:
            yh = np.asarray(y_hats, dtype=np.float64)
            yr = np.asarray(y_reals, dtype=np.float64)
            mae = round(float(np.mean(np.abs(yh - yr))), 6)
            rmse = round(float(np.sqrt(np.mean((yh - yr) ** 2))), 6)
            sic = round(_spearman_ic(yh, yr), 4)
        if len(y_bins) >= 2:
            pb = np.asarray(p_positives, dtype=np.float64)
            yb = np.asarray(y_bins, dtype=np.int64)
            roc = round(_roc_auc(yb, pb), 4)
            cal = compute_calibration_metrics(pb.tolist(), yb.tolist())
            brier = cal.brier_score
            ece = cal.ece

        def _slice_dim(dim_key: str) -> List[ShadowStratifiedSlice]:
            buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for r in joined_rows:
                buckets[r[dim_key]].append(r)
            out_slices: List[ShadowStratifiedSlice] = []
            for bkey in sorted(buckets.keys()):
                sub = buckets[bkey]
                errs_sq: List[float] = []
                ebps: List[float] = []
                nbps: List[float] = []
                act = 0
                mat_c = 0
                for item in sub:
                    ent: ShadowLedgerEntry = item["entry"]
                    out_r: Optional[ShadowOutcomeRecord] = item["outcome"]
                    tr_r: Optional[SimulatedShadowTrade] = item["trade"]
                    if not ent.no_trade_status:
                        act += 1
                    if out_r is not None and out_r.executable_realized_return is not None:
                        mat_c += 1
                        if ent.expected_return is not None:
                            errs_sq.append((float(ent.expected_return) - float(out_r.executable_realized_return)) ** 2)
                    if tr_r is not None and tr_r.filled and tr_r.net_return_after_costs is not None:
                        if tr_r.estimated_executable_return is not None:
                            ebps.append(tr_r.estimated_executable_return * 10000.0)
                        nbps.append(tr_r.net_return_after_costs * 10000.0)
                out_slices.append(
                    ShadowStratifiedSlice(
                        dimension=dim_key,
                        bucket_key=bkey,
                        observation_count=len(sub),
                        matured_count=mat_c,
                        active_signals=act,
                        rmse=round(math.sqrt(sum(errs_sq) / len(errs_sq)), 6) if errs_sq else None,
                        mean_executable_return_bps=round(sum(ebps) / len(ebps), 3) if ebps else None,
                        mean_net_return_bps=round(sum(nbps) / len(nbps), 3) if nbps else None,
                        win_rate=round(sum(1 for v in nbps if v > 0) / float(len(nbps)), 4) if nbps else None,
                    )
                )
            return out_slices

        if include_synthetic_only_cohort:
            emp_status = "SYNTHETIC_DIAGNOSTIC_ONLY"
            caveat = (
                "SYNTHETIC REPLAY DIAGNOSTIC ONLY: Validates end-to-end shadow reporting & stratification plumbing; "
                "does NOT reflect empirical market alpha."
            )
        elif n_sessions >= min_sessions and elig_cnt > 0 and len(y_reals) >= 20:
            emp_status = "WINDOW_MET_READY_FOR_STAT_REVIEW"
            caveat = (
                f"Collected {n_sessions} real market sessions (>= {min_sessions} protocol minimum). "
                "Statistical significance must still be established via formal paired attribution tests."
            )
        else:
            emp_status = "COLLECTING_OBSERVATION_WINDOW"
            caveat = (
                f"Observed {n_sessions}/{min_sessions} minimum market sessions (preferred {preferred_sessions}), "
                f"{elig_cnt} eligible predictions, {len(y_reals)} matured outcomes. "
                "Empirical validation remains INCOMPLETE."
            )

        return ShadowValidationReport(
            is_synthetic_cohort=include_synthetic_only_cohort,
            minimum_session_window_target=min_sessions,
            preferred_session_window_target=preferred_sessions,
            empirical_status=emp_status,
            real_observation_count=len(entries),
            unique_independent_event_count=len(unique_roots),
            event_families_observed=families,
            issuers_observed=issuers,
            instruments_observed=instruments,
            market_sessions_observed=n_sessions,
            market_session_dates_ist=session_dates,
            eligible_predictions_count=elig_cnt,
            diagnostic_or_unvalidated_count=diag_cnt,
            abstention_count=abst_cnt,
            abstention_rate=round(abst_cnt / float(len(entries)), 4),
            outcome_maturity_by_horizon=maturity_stats,
            matured_prediction_count=len(y_reals),
            realized_mae=mae,
            realized_rmse=rmse,
            spearman_ic=sic,
            roc_auc=roc,
            brier_score=brier,
            calibration_ece=ece,
            simulated_trade_count=len(net_bps_list),
            mean_gross_theoretical_bps=round(sum(gross_bps_list) / len(gross_bps_list), 3) if gross_bps_list else None,
            mean_executable_bps=round(sum(exec_bps_list) / len(exec_bps_list), 3) if exec_bps_list else None,
            mean_net_after_costs_bps=round(sum(net_bps_list) / len(net_bps_list), 3) if net_bps_list else None,
            win_rate=round(sum(1 for v in net_bps_list if v > 0) / float(len(net_bps_list)), 4) if net_bps_list else None,
            data_quality_problems=dict(q_counter),
            by_instrument=_slice_dim("instrument_id"),
            by_event_subtype=_slice_dim("subtype"),
            by_horizon=_slice_dim("horizon"),
            by_market_regime=_slice_dim("regime"),
            by_confidence_bucket=_slice_dim("confidence_bucket"),
            by_time_of_day=_slice_dim("time_of_day"),
            scientific_caveat=caveat,
        )
