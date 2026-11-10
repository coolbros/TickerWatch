"""Contextual Intelligence Feature Synthesizer from Causally Retrieved Historical Analogues.

Aggregates matured post-event market responses across comparable historical events
into distribution-level contextual features (means, medians, dispersion, frequencies,
quantiles, bootstrap confidence intervals, recency, and economic conditioning slices).

Strictly enforces:
- Minimum sample gating (`insufficient_evidence=True` when matured count < `min_sample_size`).
- Outcome maturity and quality filtering (excludes immature, halted, missing, or invalid rollovers).
- Expectation integrity (surprise-conditioned slicing suppressed when consensus is unavailable).
- Explicit non-predictive disclaimer (historical frequencies are descriptive context, not causal proof).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.event_retrieval import EventRetrievalContext, RetrievedHistoricalMatch
from backend.alpha.outcomes import OutcomeHorizon


VALID_OUTCOME_STATUSES = {
    "VERIFIED",
    "CORPORATE_ACTION_ADJUSTED",
    "ROLLOVER_ADJUSTED",
}


class EvidenceStatus(str, Enum):
    """Statistical reliability classification of an aggregated historical slice."""

    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_MATCHED_OUTCOMES = "NO_MATCHED_OUTCOMES"
    EXPECTATION_UNAVAILABLE = "EXPECTATION_UNAVAILABLE"


class ReturnQuantiles(BaseModel):
    """Empirical quantiles of observed post-event returns."""

    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


class BootstrapConfidenceInterval(BaseModel):
    """Non-parametric deterministic bootstrap confidence interval for the mean return."""

    lower: float
    upper: float
    confidence_level: float = 0.95
    n_bootstrap: int = 1000
    method: str = "SEEDED_PERCENTILE_BOOTSTRAP"


class HorizonResponseDistribution(BaseModel):
    """Sample-count-aware empirical distribution for a single forward horizon."""

    horizon: str
    comparable_event_count: int
    matured_count: int
    evidence_status: EvidenceStatus
    insufficient_evidence: bool
    data_quality_status: str
    mean_return: Optional[float] = None
    similarity_weighted_mean_return: Optional[float] = None
    median_return: Optional[float] = None
    std_dev: Optional[float] = None
    positive_count: int = 0
    negative_count: int = 0
    flat_count: int = 0
    positive_frequency: Optional[float] = None
    negative_frequency: Optional[float] = None
    quantiles: Optional[ReturnQuantiles] = None
    bootstrap_ci_95: Optional[BootstrapConfidenceInterval] = None
    mean_benchmark_adjusted_return: Optional[float] = None
    mean_realized_volatility: Optional[float] = None
    notes: Optional[str] = None


class ConditionalResponseSlice(BaseModel):
    """Stratified contextual distribution conditioned on instrument, sector, materiality, or surprise."""

    condition_name: str  # e.g., "INSTRUMENT_SPECIFIC", "SECTOR_SPECIFIC", "MATERIALITY_MATCHED", "SURPRISE_MATCHED"
    horizon: str
    subset_event_count: int
    matured_count: int
    evidence_status: EvidenceStatus
    insufficient_evidence: bool
    mean_return: Optional[float] = None
    median_return: Optional[float] = None
    positive_frequency: Optional[float] = None
    bootstrap_ci_95: Optional[BootstrapConfidenceInterval] = None
    notes: Optional[str] = None


class ContextualIntelligenceFeatures(BaseModel):
    """Complete empirical historical response profile for a financial event at its decision timestamp."""

    query_event_id: Optional[str] = None
    decision_timestamp: datetime
    min_sample_size_threshold: int
    total_comparable_events: int
    matched_events_returned: int
    recency_days_latest: Optional[float] = None
    recency_days_median: Optional[float] = None
    mean_match_similarity: Optional[float] = None
    horizons: Dict[str, HorizonResponseDistribution] = Field(default_factory=dict)
    conditional_slices: Dict[str, ConditionalResponseSlice] = Field(default_factory=dict)
    overall_evidence_status: EvidenceStatus
    insufficient_evidence_flag: bool
    explanatory_disclaimer: str = (
        "Empirical historical analogue statistics are conditional descriptive context; "
        "do not interpret small-sample means as causal effects or deterministic return predictions."
    )


class ContextualFeatureSynthesizer:
    """
    Aggregates causally retrieved matches and their matured outcome labels into
    statistically honest, uncertainty-aware contextual features.
    """

    def __init__(
        self,
        min_sample_size: int = 5,
        bootstrap_resamples: int = 1000,
        bootstrap_seed: int = 42,
    ) -> None:
        if min_sample_size < 1:
            raise ValueError("min_sample_size must be >= 1")
        self.min_sample_size = min_sample_size
        self.bootstrap_resamples = bootstrap_resamples
        self.bootstrap_seed = bootstrap_seed

    @staticmethod
    def _extract_matured_return(
        match: RetrievedHistoricalMatch,
        horizon_val: str,
        decision_ts: datetime,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], float]:
        """
        Safely extract `(simple_return, benchmark_adjusted_return, realized_vol, sim_weight)`
        ONLY when outcome has matured strictly <= `decision_ts` AND passed data-quality validation.
        """
        sim_w = max(0.01, float(match.similarity_score))
        rec = match.matured_outcomes.get(horizon_val)
        if rec is not None:
            if rec.outcome_end_ts > decision_ts:
                return None, None, None, sim_w
            if rec.data_quality_status not in VALID_OUTCOME_STATUSES:
                return None, None, None, sim_w
            if rec.return_simple is None:
                return None, None, None, sim_w
            return (
                float(rec.return_simple),
                (float(rec.benchmark_adjusted_return) if rec.benchmark_adjusted_return is not None else None),
                (float(rec.realized_volatility) if rec.realized_volatility is not None else None),
                sim_w,
            )

        # Backwards-compatible fallback for in-memory HistoricalEventRecord matches (1d / 5d)
        if horizon_val == "1d" and match.is_1d_outcome_matured and match.post_event_return_1d is not None:
            if (match.timestamp.timestamp() + 86400.0) <= decision_ts.timestamp() + 1e-6:
                return float(match.post_event_return_1d), None, None, sim_w
        if horizon_val == "5d" and match.is_5d_outcome_matured and match.post_event_return_5d is not None:
            if (match.timestamp.timestamp() + 5.0 * 86400.0) <= decision_ts.timestamp() + 1e-6:
                return float(match.post_event_return_5d), None, None, sim_w

        return None, None, None, sim_w

    def _compute_bootstrap_ci(self, values: List[float]) -> Optional[BootstrapConfidenceInterval]:
        if len(values) < self.min_sample_size:
            return None
        rng = random.Random(self.bootstrap_seed)
        n = len(values)
        means: List[float] = []
        for _ in range(self.bootstrap_resamples):
            sample = [values[rng.randrange(n)] for _ in range(n)]
            means.append(sum(sample) / n)
        means.sort()
        lo_idx = max(0, int(0.025 * self.bootstrap_resamples))
        hi_idx = min(self.bootstrap_resamples - 1, int(0.975 * self.bootstrap_resamples))
        return BootstrapConfidenceInterval(
            lower=round(means[lo_idx], 6),
            upper=round(means[hi_idx], 6),
            confidence_level=0.95,
            n_bootstrap=self.bootstrap_resamples,
        )

    def _summarize_horizon(
        self,
        horizon_val: str,
        matches: Sequence[RetrievedHistoricalMatch],
        decision_ts: datetime,
    ) -> HorizonResponseDistribution:
        returns: List[float] = []
        weights: List[float] = []
        bench_rets: List[float] = []
        vols: List[float] = []

        for m in matches:
            r, b_r, rv, w = self._extract_matured_return(m, horizon_val, decision_ts)
            if r is not None:
                returns.append(r)
                weights.append(w)
            if b_r is not None:
                bench_rets.append(b_r)
            if rv is not None:
                vols.append(rv)

        matured_n = len(returns)
        pos_n = sum(1 for r in returns if r > 1e-6)
        neg_n = sum(1 for r in returns if r < -1e-6)
        flat_n = matured_n - pos_n - neg_n

        if matured_n == 0:
            return HorizonResponseDistribution(
                horizon=horizon_val,
                comparable_event_count=len(matches),
                matured_count=0,
                evidence_status=EvidenceStatus.NO_MATCHED_OUTCOMES,
                insufficient_evidence=True,
                data_quality_status="NO_DATA",
                notes="Zero matured verified historical outcomes available at decision timestamp.",
            )

        if matured_n < self.min_sample_size:
            return HorizonResponseDistribution(
                horizon=horizon_val,
                comparable_event_count=len(matches),
                matured_count=matured_n,
                evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
                insufficient_evidence=True,
                data_quality_status="SPARSE_SAMPLE",
                positive_count=pos_n,
                negative_count=neg_n,
                flat_count=flat_n,
                notes=(
                    f"Only {matured_n} matured observations (< min threshold {self.min_sample_size}); "
                    "point estimates, quantiles, and CIs suppressed to prevent small-sample illusion."
                ),
            )

        arr = np.array(returns, dtype=np.float64)
        w_arr = np.array(weights, dtype=np.float64)
        w_sum = float(np.sum(w_arr))
        weighted_mean = float(np.sum(arr * w_arr) / w_sum) if w_sum > 0 else float(np.mean(arr))

        q10, q25, q50, q75, q90 = [float(x) for x in np.percentile(arr, [10, 25, 50, 75, 90])]
        std_val = float(np.std(arr, ddof=1)) if matured_n > 1 else 0.0

        return HorizonResponseDistribution(
            horizon=horizon_val,
            comparable_event_count=len(matches),
            matured_count=matured_n,
            evidence_status=EvidenceStatus.SUFFICIENT,
            insufficient_evidence=False,
            data_quality_status="VERIFIED",
            mean_return=round(float(np.mean(arr)), 6),
            similarity_weighted_mean_return=round(weighted_mean, 6),
            median_return=round(q50, 6),
            std_dev=round(std_val, 6),
            positive_count=pos_n,
            negative_count=neg_n,
            flat_count=flat_n,
            positive_frequency=round(pos_n / matured_n, 4),
            negative_frequency=round(neg_n / matured_n, 4),
            quantiles=ReturnQuantiles(
                p10=round(q10, 6),
                p25=round(q25, 6),
                p50=round(q50, 6),
                p75=round(q75, 6),
                p90=round(q90, 6),
            ),
            bootstrap_ci_95=self._compute_bootstrap_ci(returns),
            mean_benchmark_adjusted_return=(
                round(sum(bench_rets) / len(bench_rets), 6) if len(bench_rets) >= self.min_sample_size else None
            ),
            mean_realized_volatility=(
                round(sum(vols) / len(vols), 6) if len(vols) >= self.min_sample_size else None
            ),
        )

    def _build_conditional_slice(
        self,
        condition_name: str,
        subset_matches: Sequence[RetrievedHistoricalMatch],
        horizon_val: str,
        decision_ts: datetime,
        expectation_available: bool = True,
    ) -> ConditionalResponseSlice:
        if not expectation_available:
            return ConditionalResponseSlice(
                condition_name=condition_name,
                horizon=horizon_val,
                subset_event_count=len(subset_matches),
                matured_count=0,
                evidence_status=EvidenceStatus.EXPECTATION_UNAVAILABLE,
                insufficient_evidence=True,
                notes="Consensus expectations unavailable for query event; surprise-conditioned slicing suppressed.",
            )

        returns: List[float] = []
        for m in subset_matches:
            r, _, _, _ = self._extract_matured_return(m, horizon_val, decision_ts)
            if r is not None:
                returns.append(r)

        matured_n = len(returns)
        if matured_n == 0:
            return ConditionalResponseSlice(
                condition_name=condition_name,
                horizon=horizon_val,
                subset_event_count=len(subset_matches),
                matured_count=0,
                evidence_status=EvidenceStatus.NO_MATCHED_OUTCOMES,
                insufficient_evidence=True,
                notes="No matured outcomes within conditioned subset.",
            )

        if matured_n < self.min_sample_size:
            return ConditionalResponseSlice(
                condition_name=condition_name,
                horizon=horizon_val,
                subset_event_count=len(subset_matches),
                matured_count=matured_n,
                evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
                insufficient_evidence=True,
                notes=f"Subset matured count {matured_n} < {self.min_sample_size}; estimates suppressed.",
            )

        arr = np.array(returns, dtype=np.float64)
        pos_n = sum(1 for r in returns if r > 1e-6)
        return ConditionalResponseSlice(
            condition_name=condition_name,
            horizon=horizon_val,
            subset_event_count=len(subset_matches),
            matured_count=matured_n,
            evidence_status=EvidenceStatus.SUFFICIENT,
            insufficient_evidence=False,
            mean_return=round(float(np.mean(arr)), 6),
            median_return=round(float(np.median(arr)), 6),
            positive_frequency=round(pos_n / matured_n, 4),
            bootstrap_ci_95=self._compute_bootstrap_ci(returns),
        )

    def synthesize(
        self,
        retrieval_context: EventRetrievalContext,
        query_materiality: float = 0.5,
        query_surprise_category: str = "UNAVAILABLE_EXPECTATION",
        horizons: Optional[Sequence[OutcomeHorizon]] = None,
        slice_horizon: OutcomeHorizon = OutcomeHorizon.D1,
    ) -> ContextualIntelligenceFeatures:
        """
        Synthesize ContextualIntelligenceFeatures from a causal `EventRetrievalContext`.
        """
        decision_ts = retrieval_context.decision_timestamp
        matches = retrieval_context.matches
        target_horizons = horizons or list(OutcomeHorizon)

        horizon_dists: Dict[str, HorizonResponseDistribution] = {}
        for h in target_horizons:
            horizon_dists[h.value] = self._summarize_horizon(h.value, matches, decision_ts)

        # Conditional Slicing on `slice_horizon` (default 1d)
        sh = slice_horizon.value
        inst_matches = [m for m in matches if m.instrument_match >= 0.95]
        sector_matches = [
            m for m in matches
            if (m.score_breakdown is not None and m.score_breakdown.sector_match >= 0.99)
            or (0.35 <= m.instrument_match < 0.95)
        ]
        mat_matches = [m for m in matches if abs(m.materiality_score - query_materiality) <= 0.20]

        expectation_avail = query_surprise_category not in ("UNAVAILABLE_EXPECTATION", "", None)
        surprise_matches = (
            [
                m for m in matches
                if m.score_breakdown is not None and m.score_breakdown.surprise_alignment >= 0.90
            ]
            if expectation_avail
            else []
        )

        conditional_slices = {
            f"INSTRUMENT_SPECIFIC_{sh}": self._build_conditional_slice(
                "INSTRUMENT_SPECIFIC", inst_matches, sh, decision_ts
            ),
            f"SECTOR_SPECIFIC_{sh}": self._build_conditional_slice(
                "SECTOR_SPECIFIC", sector_matches, sh, decision_ts
            ),
            f"MATERIALITY_MATCHED_{sh}": self._build_conditional_slice(
                "MATERIALITY_MATCHED", mat_matches, sh, decision_ts
            ),
            f"SURPRISE_MATCHED_{sh}": self._build_conditional_slice(
                "SURPRISE_MATCHED",
                surprise_matches,
                sh,
                decision_ts,
                expectation_available=expectation_avail,
            ),
        }

        days_list = sorted(m.days_prior_to_decision for m in matches)
        recency_latest = days_list[0] if days_list else None
        recency_median = round(float(np.median(days_list)), 2) if days_list else None
        sim_mean = round(sum(m.similarity_score for m in matches) / len(matches), 4) if matches else None

        has_sufficient_horizon = any(d.evidence_status == EvidenceStatus.SUFFICIENT for d in horizon_dists.values())
        overall_status = (
            EvidenceStatus.SUFFICIENT
            if has_sufficient_horizon
            else (EvidenceStatus.NO_MATCHED_OUTCOMES if not matches else EvidenceStatus.INSUFFICIENT_EVIDENCE)
        )

        return ContextualIntelligenceFeatures(
            query_event_id=retrieval_context.query_event_id,
            decision_timestamp=decision_ts,
            min_sample_size_threshold=self.min_sample_size,
            total_comparable_events=retrieval_context.total_comparable_count,
            matched_events_returned=len(matches),
            recency_days_latest=recency_latest,
            recency_days_median=recency_median,
            mean_match_similarity=sim_mean,
            horizons=horizon_dists,
            conditional_slices=conditional_slices,
            overall_evidence_status=overall_status,
            insufficient_evidence_flag=not has_sufficient_horizon,
        )
