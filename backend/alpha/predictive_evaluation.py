"""Predictive Evaluation & Incremental Alpha Attribution (Phase 5 WP-H).

Computes statistical regression, directional classification, probability calibration,
and interval sharpness metrics using pure vectorized NumPy/stdlib (no external ML packages),
and executes formal incremental ablation attribution (Models A, B, C, E vs Model D and baselines)
with Newey-West/HAC Diebold-Mariano tests, moving-block bootstrap confidence intervals,
and Holm-Bonferroni multiple-testing correction.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.feature_registry import ModelConfiguration
from backend.alpha.prediction_uncertainty import (
    CalibrationReport,
    CalibratedPrediction,
    compute_calibration_metrics,
)


def _rankdata_average(arr: np.ndarray) -> np.ndarray:
    """Tie-aware average rank (1-based) equivalent to scipy.stats.rankdata(method='average')."""
    n = arr.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    order = np.argsort(arr, kind="mergesort")
    sorted_vals = arr[order]
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i + 1
        while j < n and sorted_vals[j] == sorted_vals[i]:
            j += 1
        avg_rank = 0.5 * ((i + 1) + j)
        ranks[order[i:j]] = avg_rank
        i = j
    return ranks


def _pearson_r(x: np.ndarray, y: np.ndarray) -> float:
    if x.shape[0] < 2:
        return 0.0
    xc = x - float(np.mean(x))
    yc = y - float(np.mean(y))
    denom = float(np.sqrt(np.sum(xc * xc) * np.sum(yc * yc)))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(np.sum(xc * yc) / denom, -1.0, 1.0))


def _spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    if x.shape[0] < 2:
        return 0.0
    return _pearson_r(_rankdata_average(x), _rankdata_average(y))


def _roc_auc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Exact tie-aware Mann-Whitney U ROC-AUC. Returns 0.5 when single-class."""
    pos_mask = y_true == 1
    n_pos = int(np.sum(pos_mask))
    n_neg = int(y_true.shape[0] - n_pos)
    if n_pos == 0 or n_neg == 0:
        return 0.5
    ranks = _rankdata_average(scores)
    sum_pos_ranks = float(np.sum(ranks[pos_mask]))
    auc = (sum_pos_ranks - n_pos * (n_pos + 1) / 2.0) / float(n_pos * n_neg)
    return float(np.clip(auc, 0.0, 1.0))


def _pr_auc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Exact Average Precision / PR-AUC under descending score sorting."""
    n_pos = int(np.sum(y_true == 1))
    if n_pos == 0:
        return 0.0
    order = np.argsort(-scores, kind="mergesort")
    y_sorted = y_true[order]
    tp = np.cumsum(y_sorted == 1)
    fp = np.cumsum(y_sorted == 0)
    precision = tp / np.maximum(tp + fp, 1)
    # Standard average precision across positive occurrences
    return float(np.sum(precision * (y_sorted == 1)) / float(n_pos))


class PredictiveMetricSummary(BaseModel):
    """Complete out-of-sample statistical metric summary for one horizon/fold/model."""

    model_label: str
    horizon_label: str
    n_samples: int
    n_active_signals: int
    abstention_rate: float

    # Regression
    mae: float
    rmse: float
    pearson_r: float
    spearman_ic: float

    # Classification (positive directional outcome y_positive)
    positive_base_rate: float
    balanced_accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    pr_auc: float
    brier_score: float
    log_loss: float

    # Calibration & Quantiles
    ece: float
    pinball_loss_q10: Optional[float] = None
    pinball_loss_q50: Optional[float] = None
    pinball_loss_q90: Optional[float] = None
    interval_coverage_80pct: Optional[float] = None
    interval_coverage_50pct: Optional[float] = None
    calibration_report: CalibrationReport


class CrossFoldStabilitySummary(BaseModel):
    """Across-fold stability statistics verifying edge is not a single lucky fold."""

    model_label: str
    horizon_label: str
    n_folds: int
    total_oos_samples: int
    mean_rmse: float
    std_rmse: float
    mean_spearman_ic: float
    std_spearman_ic: float
    mean_brier_score: float
    std_brier_score: float
    mean_roc_auc: float
    min_fold_roc_auc: float
    max_fold_roc_auc: float
    positive_ic_fold_fraction: float


class PairwiseAttributionResult(BaseModel):
    """Paired statistical comparison of Candidate Model against Reference/Baseline."""

    candidate_label: str
    comparator_label: str
    n_paired: int
    delta_rmse: float          # comparator_rmse - candidate_rmse (>0 means candidate better)
    delta_brier: float         # comparator_brier - candidate_brier (>0 means candidate better)
    delta_spearman_ic: float   # candidate_ic - comparator_ic (>0 means candidate better)
    mean_sq_err_diff: float    # E[e_comp^2 - e_cand^2] (>0 means candidate lower error)
    ci_95_low: float
    ci_95_high: float
    dm_hac_z_stat: float
    raw_p_value: float
    holm_adjusted_p_value: float
    statistically_significant_at_05: bool
    candidate_beats_comparator: bool


class AlphaAttributionReport(BaseModel):
    """Formal empirical / synthetic ablation report answering the core thesis."""

    reference_model_label: str
    is_synthetic: bool
    horizon_label: str
    pairwise_comparisons: List[PairwiseAttributionResult] = Field(default_factory=list)
    headline_conclusion: str
    honest_evidence_caveat: str


def compute_predictive_metrics(
    model_label: str,
    horizon_label: str,
    predictions: Sequence[CalibratedPrediction],
    y_continuous: Sequence[float],
    y_positive: Sequence[int],
    prob_threshold: float = 0.5,
) -> PredictiveMetricSummary:
    """Compute regression, classification, calibration, and quantile metrics."""
    n = len(predictions)
    if n == 0 or len(y_continuous) != n or len(y_positive) != n:
        raise ValueError("Mismatched or empty lengths passed to compute_predictive_metrics.")

    y_cont = np.asarray(y_continuous, dtype=np.float64)
    y_bin = np.asarray([1 if int(v) > 0 else 0 for v in y_positive], dtype=np.int64)
    y_hat = np.asarray([p.expected_return for p in predictions], dtype=np.float64)
    p_pos = np.asarray([p.p_positive for p in predictions], dtype=np.float64)
    q10 = np.asarray([p.q10 for p in predictions], dtype=np.float64)
    q25 = np.asarray([p.q25 for p in predictions], dtype=np.float64)
    q50 = np.asarray([p.q50 for p in predictions], dtype=np.float64)
    q75 = np.asarray([p.q75 for p in predictions], dtype=np.float64)
    q90 = np.asarray([p.q90 for p in predictions], dtype=np.float64)

    err = y_hat - y_cont
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err * err)))
    pr = _pearson_r(y_hat, y_cont)
    sic = _spearman_ic(y_hat, y_cont)

    pred_pos = p_pos >= prob_threshold
    tp = int(np.sum((pred_pos == 1) & (y_bin == 1)))
    fp = int(np.sum((pred_pos == 1) & (y_bin == 0)))
    tn = int(np.sum((pred_pos == 0) & (y_bin == 0)))
    fn = int(np.sum((pred_pos == 0) & (y_bin == 1)))

    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    specificity = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0
    bal_acc = 0.5 * (recall + specificity)
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    roc = _roc_auc(y_bin, p_pos)
    pr_a = _pr_auc(y_bin, p_pos)

    cal_rep = compute_calibration_metrics(
        probs=p_pos.tolist(),
        y_binary=y_bin.tolist(),
        y_continuous=y_cont.tolist(),
        q10_preds=q10.tolist(),
        q25_preds=q25.tolist(),
        q50_preds=q50.tolist(),
        q75_preds=q75.tolist(),
        q90_preds=q90.tolist(),
        n_bins=10,
    )

    n_active = sum(1 for p in predictions if not p.abstain and p.signal_direction != 0)
    abstain_rate = 1.0 - (n_active / float(n))

    return PredictiveMetricSummary(
        model_label=model_label,
        horizon_label=horizon_label,
        n_samples=n,
        n_active_signals=n_active,
        abstention_rate=round(abstain_rate, 6),
        mae=round(mae, 8),
        rmse=round(rmse, 8),
        pearson_r=round(pr, 6),
        spearman_ic=round(sic, 6),
        positive_base_rate=round(float(np.mean(y_bin)), 6),
        balanced_accuracy=round(bal_acc, 6),
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        roc_auc=round(roc, 6),
        pr_auc=round(pr_a, 6),
        brier_score=cal_rep.brier_score,
        log_loss=cal_rep.log_loss,
        ece=cal_rep.ece,
        pinball_loss_q10=cal_rep.pinball_loss_by_quantile.get("q10"),
        pinball_loss_q50=cal_rep.pinball_loss_by_quantile.get("q50"),
        pinball_loss_q90=cal_rep.pinball_loss_by_quantile.get("q90"),
        interval_coverage_80pct=cal_rep.interval_coverage_80pct,
        interval_coverage_50pct=cal_rep.interval_coverage_50pct,
        calibration_report=cal_rep,
    )


def aggregate_fold_metrics(
    fold_summaries: Sequence[PredictiveMetricSummary],
) -> CrossFoldStabilitySummary:
    """Aggregate per-fold metrics to verify stability across walk-forward splits."""
    if not fold_summaries:
        raise ValueError("Cannot aggregate empty fold_summaries.")
    rmses = np.asarray([s.rmse for s in fold_summaries], dtype=np.float64)
    ics = np.asarray([s.spearman_ic for s in fold_summaries], dtype=np.float64)
    briers = np.asarray([s.brier_score for s in fold_summaries], dtype=np.float64)
    rocs = np.asarray([s.roc_auc for s in fold_summaries], dtype=np.float64)

    return CrossFoldStabilitySummary(
        model_label=fold_summaries[0].model_label,
        horizon_label=fold_summaries[0].horizon_label,
        n_folds=len(fold_summaries),
        total_oos_samples=sum(s.n_samples for s in fold_summaries),
        mean_rmse=round(float(np.mean(rmses)), 8),
        std_rmse=round(float(np.std(rmses)), 8),
        mean_spearman_ic=round(float(np.mean(ics)), 6),
        std_spearman_ic=round(float(np.std(ics)), 6),
        mean_brier_score=round(float(np.mean(briers)), 6),
        std_brier_score=round(float(np.std(briers)), 6),
        mean_roc_auc=round(float(np.mean(rocs)), 6),
        min_fold_roc_auc=round(float(np.min(rocs)), 6),
        max_fold_roc_auc=round(float(np.max(rocs)), 6),
        positive_ic_fold_fraction=round(float(np.mean(ics > 0.0)), 4),
    )


def block_bootstrap_ci(
    diff_series: Sequence[float],
    n_bootstrap: int = 1000,
    block_length: int = 5,
    seed: int = 42,
) -> Tuple[float, float]:
    """Circular moving-block bootstrap 95% confidence interval for mean(diff_series)."""
    arr = np.asarray(diff_series, dtype=np.float64)
    n = arr.shape[0]
    if n == 0:
        return 0.0, 0.0
    if n < 3 or np.allclose(arr, arr[0]):
        m = float(np.mean(arr))
        return m, m

    blk = max(1, min(block_length, n))
    n_blocks = int(math.ceil(n / float(blk)))
    rng = np.random.default_rng(seed)

    # Precompute circular block matrix so sampling is fast & vectorized
    extended = np.concatenate([arr, arr[: blk - 1]])
    starts = np.arange(n)
    offsets = np.arange(blk)
    blocks = extended[starts[:, None] + offsets[None, :]]  # (n, blk)

    sampled_starts = rng.integers(0, n, size=(n_bootstrap, n_blocks))
    boot_samples = blocks[sampled_starts].reshape(n_bootstrap, n_blocks * blk)[:, :n]
    boot_means = np.mean(boot_samples, axis=1)
    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    return float(lo), float(hi)


def newey_west_dm_test(
    loss_diff: Sequence[float],
    max_lag: Optional[int] = None,
) -> Tuple[float, float]:
    """Diebold-Mariano test with Newey-West Bartlett HAC variance & normal two-sided p-value."""
    d = np.asarray(loss_diff, dtype=np.float64)
    n = d.shape[0]
    if n < 5:
        return 0.0, 1.0
    d_mean = float(np.mean(d))
    dc = d - d_mean

    lag = max_lag if max_lag is not None else max(1, int(round(n ** (1.0 / 3.0))))
    lag = min(lag, n - 1)

    gamma0 = float(np.dot(dc, dc) / float(n))
    hac_var = gamma0
    for k in range(1, lag + 1):
        weight = 1.0 - (k / float(lag + 1))
        gamma_k = float(np.dot(dc[k:], dc[:-k]) / float(n))
        hac_var += 2.0 * weight * gamma_k

    # Guard against near-zero or negative HAC numerical estimate in short series
    hac_var = max(hac_var, 1e-18)
    se = math.sqrt(hac_var / float(n))
    z_stat = d_mean / se
    p_val = math.erfc(abs(z_stat) / math.sqrt(2.0))
    return float(z_stat), float(min(1.0, max(0.0, p_val)))


def holm_bonferroni_adjust(p_values: Sequence[float]) -> List[float]:
    """Step-down Holm-Bonferroni family-wise error rate adjustment."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda kv: kv[1])
    adjusted = [0.0] * m
    running_max = 0.0
    for rank, (orig_idx, p) in enumerate(indexed):
        factor = float(m - rank)
        cand = min(1.0, max(0.0, float(p) * factor))
        running_max = max(running_max, cand)
        adjusted[orig_idx] = running_max
    return adjusted


class AlphaAttributor:
    """Compares candidate Model D against ablations A, B, C, E and simple baselines."""

    @staticmethod
    def compare_candidate_vs_comparators(
        candidate_label: str,
        candidate_preds: Sequence[CalibratedPrediction],
        comparators: Dict[str, Sequence[CalibratedPrediction]],
        y_continuous: Sequence[float],
        y_positive: Sequence[int],
        horizon_label: str,
        is_synthetic: bool,
        n_bootstrap: int = 1000,
        block_length: int = 5,
        seed: int = 42,
    ) -> AlphaAttributionReport:
        n = len(candidate_preds)
        if n == 0:
            raise ValueError("Cannot run attribution with zero test samples.")
        y_cont = np.asarray(y_continuous, dtype=np.float64)
        y_bin = np.asarray([1 if int(v) > 0 else 0 for v in y_positive], dtype=np.int64)
        cand_hat = np.asarray([p.expected_return for p in candidate_preds], dtype=np.float64)
        cand_prob = np.asarray([p.p_positive for p in candidate_preds], dtype=np.float64)

        cand_sq_err = (cand_hat - y_cont) ** 2
        cand_brier = (cand_prob - y_bin) ** 2
        cand_rmse = float(np.sqrt(np.mean(cand_sq_err)))
        cand_brier_mean = float(np.mean(cand_brier))
        cand_ic = _spearman_ic(cand_hat, y_cont)

        raw_rows = []
        raw_pvals = []
        for comp_label, comp_preds in comparators.items():
            if len(comp_preds) != n:
                raise ValueError(f"Comparator '{comp_label}' length {len(comp_preds)} != {n}.")
            comp_hat = np.asarray([p.expected_return for p in comp_preds], dtype=np.float64)
            comp_prob = np.asarray([p.p_positive for p in comp_preds], dtype=np.float64)

            comp_sq_err = (comp_hat - y_cont) ** 2
            comp_brier = (comp_prob - y_bin) ** 2
            comp_rmse = float(np.sqrt(np.mean(comp_sq_err)))
            comp_brier_mean = float(np.mean(comp_brier))
            comp_ic = _spearman_ic(comp_hat, y_cont)

            # Positive diff means comparator had LARGER squared error => candidate outperformed
            sq_diff = (comp_sq_err - cand_sq_err).tolist()
            ci_lo, ci_hi = block_bootstrap_ci(
                sq_diff, n_bootstrap=n_bootstrap, block_length=block_length, seed=seed
            )
            z_stat, raw_p = newey_west_dm_test(sq_diff)
            raw_pvals.append(raw_p)
            raw_rows.append(
                {
                    "comparator_label": comp_label,
                    "delta_rmse": comp_rmse - cand_rmse,
                    "delta_brier": comp_brier_mean - cand_brier_mean,
                    "delta_spearman_ic": cand_ic - comp_ic,
                    "mean_sq_err_diff": float(np.mean(sq_diff)),
                    "ci_lo": ci_lo,
                    "ci_hi": ci_hi,
                    "z_stat": z_stat,
                    "raw_p": raw_p,
                }
            )

        adj_pvals = holm_bonferroni_adjust(raw_pvals)
        results: List[PairwiseAttributionResult] = []
        for row, adj_p in zip(raw_rows, adj_pvals):
            sig = (adj_p < 0.05) and (row["ci_lo"] > 0.0) and (row["mean_sq_err_diff"] > 0.0)
            results.append(
                PairwiseAttributionResult(
                    candidate_label=candidate_label,
                    comparator_label=row["comparator_label"],
                    n_paired=n,
                    delta_rmse=round(row["delta_rmse"], 8),
                    delta_brier=round(row["delta_brier"], 6),
                    delta_spearman_ic=round(row["delta_spearman_ic"], 6),
                    mean_sq_err_diff=round(row["mean_sq_err_diff"], 10),
                    ci_95_low=round(row["ci_lo"], 10),
                    ci_95_high=round(row["ci_hi"], 10),
                    dm_hac_z_stat=round(row["z_stat"], 4),
                    raw_p_value=round(row["raw_p"], 6),
                    holm_adjusted_p_value=round(adj_p, 6),
                    statistically_significant_at_05=sig,
                    candidate_beats_comparator=(row["mean_sq_err_diff"] > 0.0 and row["delta_rmse"] > 0.0),
                )
            )

        sig_wins = [r.comparator_label for r in results if r.statistically_significant_at_05]
        if is_synthetic:
            conclusion = (
                f"SYNTHETIC_BENCHMARK: {candidate_label} achieved Holm-significant OOS error reduction vs "
                f"{len(sig_wins)}/{len(results)} comparators ({', '.join(sig_wins) if sig_wins else 'none'})."
            )
            caveat = (
                "SYNTHETIC DATA ONLY: This attribution tests software pipeline separation across E/H/M groups; "
                "it does NOT establish live empirical market alpha."
            )
        else:
            conclusion = (
                f"EMPIRICAL_EVALUATION: {candidate_label} outperformed {len(sig_wins)}/{len(results)} "
                f"comparators at Holm-adjusted p < 0.05."
            )
            caveat = (
                "Evaluated strictly on held-out out-of-sample purged folds; ensure full real-market dataset "
                "adequacy before drawing economic conclusions."
            )

        return AlphaAttributionReport(
            reference_model_label=candidate_label,
            is_synthetic=is_synthetic,
            horizon_label=horizon_label,
            pairwise_comparisons=results,
            headline_conclusion=conclusion,
            honest_evidence_caveat=caveat,
        )
