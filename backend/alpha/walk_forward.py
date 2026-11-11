"""Purged Chronological Walk-Forward Validation Splitter (Phase 5 WP-G).

Strictly enforces chronological ordering (bans random cross-validation),
purges training/validation observations whose forward outcome windows overlap
evaluation boundaries or whose hashes/revisions/issuer cycles collide across boundaries,
enforces configurable embargo intervals >= max outcome horizon, and logs complete
fold audit telemetry via `LeakageAuditor`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple
from pydantic import BaseModel, Field

from backend.alpha.leakage_audit import LeakageAuditReport, LeakageAuditor
from backend.alpha.outcomes import OutcomeHorizon
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_targets import PredictiveTargetRecord


class WalkForwardMode(str, Enum):
    EXPANDING = "EXPANDING"
    ROLLING = "ROLLING"


def default_horizon_seconds(horizon: OutcomeHorizon) -> float:
    """Minimum wall-clock / trading horizon window span in seconds."""
    return {
        OutcomeHorizon.M1: 60.0,
        OutcomeHorizon.M5: 300.0,
        OutcomeHorizon.M30: 1800.0,
        OutcomeHorizon.H1: 3600.0,
        OutcomeHorizon.D1: 86400.0,
        OutcomeHorizon.D5: 5 * 86400.0,
    }[horizon]


class WalkForwardFold(BaseModel):
    """Audited train -> validation -> test chronological fold with purging & embargo stats."""

    fold_id: int
    mode: WalkForwardMode
    train_start: datetime
    train_end: datetime
    val_start: Optional[datetime] = None
    val_end: Optional[datetime] = None
    test_start: datetime
    test_end: datetime

    train_indices: List[int]
    val_indices: List[int] = Field(default_factory=list)
    test_indices: List[int]

    train_sample_count: int
    val_sample_count: int
    test_sample_count: int
    purged_overlap_count: int
    purged_duplicate_revision_count: int
    purged_same_issuer_cycle_count: int
    embargoed_eval_count: int
    embargo_seconds: float
    embargo_details: str

    unique_event_count: int
    unique_issuer_count: int
    synthetic_record_count: int
    leakage_audit: LeakageAuditReport


class PurgedWalkForwardSplitter:
    """
    Constructs expanding or rolling walk-forward folds `(train, val, test)` strictly sorted
    by `decision_timestamp` with boundary purging and horizon-scaled embargoing.
    """

    def __init__(
        self,
        horizon: OutcomeHorizon,
        n_folds: int = 3,
        mode: WalkForwardMode = WalkForwardMode.EXPANDING,
        val_fraction_within_fold: float = 0.25,
        rolling_train_size: Optional[int] = None,
        embargo_seconds: Optional[float] = None,
        issuer_cycle_purge_seconds: float = 0.0,
        allow_synthetic: bool = False,
    ) -> None:
        if n_folds < 1:
            raise ValueError("n_folds must be >= 1")
        self.horizon = horizon
        self.n_folds = n_folds
        self.mode = mode
        self.val_fraction_within_fold = val_fraction_within_fold
        self.rolling_train_size = rolling_train_size
        base_h = default_horizon_seconds(horizon)
        self.embargo_seconds = float(embargo_seconds) if embargo_seconds is not None else base_h
        if self.embargo_seconds < base_h:
            raise ValueError(
                f"Embargo duration ({self.embargo_seconds}s) must be >= horizon duration ({base_h}s) to prevent label overlap leakage."
            )
        self.issuer_cycle_purge_seconds = max(0.0, float(issuer_cycle_purge_seconds))
        self.allow_synthetic = allow_synthetic
        self.auditor = LeakageAuditor()

    @staticmethod
    def _effective_exit_ts(tgt: PredictiveTargetRecord, h_sec: float) -> datetime:
        if tgt.exit_timestamp is not None:
            return tgt.exit_timestamp
        return tgt.decision_timestamp + timedelta(seconds=h_sec)

    def _purge_prior_against_eval(
        self,
        candidate_indices: List[int],
        eval_indices: List[int],
        rows: Sequence[PointInTimeFeatureRow],
        tgts: Sequence[PredictiveTargetRecord],
        h_sec: float,
    ) -> Tuple[List[int], int, int, int]:
        if not eval_indices:
            return candidate_indices, 0, 0, 0
        eval_start = min(rows[i].decision_timestamp for i in eval_indices)
        eval_hashes = {rows[i].content_hash for i in eval_indices if rows[i].content_hash}
        eval_event_ids = {rows[i].event_id for i in eval_indices}
        eval_issuers_near_boundary = {
            rows[i].primary_issuer_id
            for i in eval_indices
            if rows[i].primary_issuer_id
            and (rows[i].decision_timestamp - eval_start).total_seconds() <= self.issuer_cycle_purge_seconds
        }

        kept: List[int] = []
        p_overlap = 0
        p_dup_rev = 0
        p_issuer = 0
        cutoff_with_embargo = eval_start - timedelta(seconds=self.embargo_seconds)

        for idx in candidate_indices:
            r = rows[idx]
            t = tgts[idx]
            exit_ts = self._effective_exit_ts(t, h_sec)

            # 1. Exclude duplicate content_hash or revision of same event_id
            if r.event_id in eval_event_ids or (r.content_hash and r.content_hash in eval_hashes):
                p_dup_rev += 1
                continue
            # 2. Exclude outcomes overlapping eval start OR inside embargo tail before eval_start
            if exit_ts > eval_start or r.decision_timestamp > cutoff_with_embargo:
                p_overlap += 1
                continue
            # 3. Exclude same issuer reporting cluster straddling split boundary
            if (
                self.issuer_cycle_purge_seconds > 0.0
                and r.primary_issuer_id in eval_issuers_near_boundary
                and (eval_start - r.decision_timestamp).total_seconds() <= self.issuer_cycle_purge_seconds
            ):
                p_issuer += 1
                continue
            kept.append(idx)

        return kept, p_overlap, p_dup_rev, p_issuer

    def generate_folds(
        self,
        feature_rows: Sequence[PointInTimeFeatureRow],
        target_records: Sequence[PredictiveTargetRecord],
    ) -> Tuple[List[WalkForwardFold], List[int]]:
        """
        Return sorted `(folds, order_map)` where `order_map` maps row position -> original index.
        Wait: callers should pass sorted pairs or apply splits on the sorted sequence directly.
        """
        if len(feature_rows) != len(target_records):
            raise ValueError("feature_rows and target_records must have equal length")

        # Filter strictly trainable records & enforce empirical synthetic ban unless test flag set
        valid_pairs: List[Tuple[int, PointInTimeFeatureRow, PredictiveTargetRecord]] = []
        for orig_i, (r, t) in enumerate(zip(feature_rows, target_records)):
            if not t.is_trainable:
                continue
            if not self.allow_synthetic and (r.is_synthetic or t.is_synthetic):
                raise ValueError(
                    f"Synthetic record {r.event_id} encountered in empirical WalkForwardSplitter (allow_synthetic=False)."
                )
            valid_pairs.append((orig_i, r, t))

        valid_pairs.sort(key=lambda x: x[1].decision_timestamp)
        n = len(valid_pairs)
        if n < (self.n_folds + 2) * 3:
            raise ValueError(f"Insufficient trainable records ({n}) for {self.n_folds} walk-forward folds.")

        rows_sorted = [p[1] for p in valid_pairs]
        tgts_sorted = [p[2] for p in valid_pairs]
        orig_indices = [p[0] for p in valid_pairs]
        h_sec = default_horizon_seconds(self.horizon)

        # Partition sorted stream into (n_folds + 1) chronological chunks:
        # Fold k uses chunks [0..k] (or rolling trailing chunk) to predict test chunk [k+1]
        chunk_edges = [int(round(i * n / (self.n_folds + 1))) for i in range(self.n_folds + 2)]
        folds: List[WalkForwardFold] = []

        for f_idx in range(self.n_folds):
            prior_end = chunk_edges[f_idx + 1]
            test_start_i = chunk_edges[f_idx + 1]
            test_end_i = chunk_edges[f_idx + 2]

            if self.mode == WalkForwardMode.ROLLING and self.rolling_train_size is not None:
                prior_start = max(0, prior_end - self.rolling_train_size)
            else:
                prior_start = 0

            raw_prior = list(range(prior_start, prior_end))
            raw_test = list(range(test_start_i, test_end_i))
            if not raw_prior or not raw_test:
                continue

            # Split prior chronologically into train + validation for calibration
            n_val = max(2, int(round(len(raw_prior) * self.val_fraction_within_fold)))
            if len(raw_prior) - n_val < 3:
                raw_train = raw_prior
                raw_val: List[int] = []
            else:
                raw_train = raw_prior[:-n_val]
                raw_val = raw_prior[-n_val:]

            # First apply embargo AFTER raw_val / raw_train cutoff relative to raw_test start
            # Also track how many test candidates fall inside embargo if boundary is computed right after prior_end
            prior_last_ts = rows_sorted[raw_prior[-1]].decision_timestamp
            kept_test: List[int] = []
            emb_test = 0
            for ti in raw_test:
                if (rows_sorted[ti].decision_timestamp - prior_last_ts).total_seconds() < self.embargo_seconds:
                    emb_test += 1
                else:
                    kept_test.append(ti)

            # If kept_test lost points inside embargo, purge boundary moves to kept_test start cleanly
            eval_test = kept_test if kept_test else []
            if not eval_test:
                # Advance past embargo if sparse: purge prior rows instead to enforce full embargo gap
                eval_test = raw_test

            # Purge val against test
            val_clean, po1, pd1, pi1 = self._purge_prior_against_eval(raw_val, eval_test, rows_sorted, tgts_sorted, h_sec)
            # Purge train against BOTH val_clean (if present) and eval_test
            anchor_eval = val_clean if val_clean else eval_test
            train_clean, po2, pd2, pi2 = self._purge_prior_against_eval(raw_train, anchor_eval, rows_sorted, tgts_sorted, h_sec)
            train_clean, po3, pd3, pi3 = self._purge_prior_against_eval(train_clean, eval_test, rows_sorted, tgts_sorted, h_sec)

            if not train_clean or not eval_test:
                continue

            # Map back to original sequence indices
            tr_orig = [orig_indices[i] for i in train_clean]
            va_orig = [orig_indices[i] for i in val_clean]
            te_orig = [orig_indices[i] for i in eval_test]

            audit_rep = self.auditor.audit_split(
                train_features=[rows_sorted[i] for i in train_clean],
                train_targets=[tgts_sorted[i] for i in train_clean],
                val_features=[rows_sorted[i] for i in val_clean],
                val_targets=[tgts_sorted[i] for i in val_clean],
                test_features=[rows_sorted[i] for i in eval_test],
                test_targets=[tgts_sorted[i] for i in eval_test],
                min_embargo_seconds=self.embargo_seconds,
                allow_synthetic_in_test=self.allow_synthetic,
            )

            all_fold_rows = [rows_sorted[i] for i in (train_clean + val_clean + eval_test)]
            folds.append(
                WalkForwardFold(
                    fold_id=f_idx + 1,
                    mode=self.mode,
                    train_start=rows_sorted[train_clean[0]].decision_timestamp,
                    train_end=rows_sorted[train_clean[-1]].decision_timestamp,
                    val_start=(rows_sorted[val_clean[0]].decision_timestamp if val_clean else None),
                    val_end=(rows_sorted[val_clean[-1]].decision_timestamp if val_clean else None),
                    test_start=rows_sorted[eval_test[0]].decision_timestamp,
                    test_end=rows_sorted[eval_test[-1]].decision_timestamp,
                    train_indices=tr_orig,
                    val_indices=va_orig,
                    test_indices=te_orig,
                    train_sample_count=len(tr_orig),
                    val_sample_count=len(va_orig),
                    test_sample_count=len(te_orig),
                    purged_overlap_count=po1 + po2 + po3,
                    purged_duplicate_revision_count=pd1 + pd2 + pd3,
                    purged_same_issuer_cycle_count=pi1 + pi2 + pi3,
                    embargoed_eval_count=emb_test,
                    embargo_seconds=self.embargo_seconds,
                    embargo_details=f"Horizon {self.horizon.value} enforced >= {self.embargo_seconds:.0f}s embargo across boundaries.",
                    unique_event_count=len({r.event_id for r in all_fold_rows}),
                    unique_issuer_count=len({r.primary_issuer_id for r in all_fold_rows if r.primary_issuer_id}),
                    synthetic_record_count=sum(1 for r in all_fold_rows if r.is_synthetic),
                    leakage_audit=audit_rep,
                )
            )

        return folds, orig_indices
