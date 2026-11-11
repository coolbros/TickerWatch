"""Adversarial Leakage Audit Engine for Purged Walk-Forward Validation (Phase 5 WP-G).

Verifies zero look-ahead bias, overlap purge integrity, embargo adherence,
duplicate/revision exclusion, train-only scaler fitting provenance, and strict
segregation of synthetic fixtures from empirical performance evaluation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence
from pydantic import BaseModel, Field

from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_targets import PredictiveTargetRecord


class LeakageAuditReport(BaseModel):
    """Formal audit verdict for a walk-forward split or experiment run."""

    passed: bool
    empirical_mode: bool
    synthetic_count_total: int = 0
    synthetic_count_in_test: int = 0
    unique_events_total: int = 0
    unique_issuers_total: int = 0
    overlap_violations: int = 0
    embargo_violations: int = 0
    duplicate_or_revision_leaks: int = 0
    feature_timestamp_violations: int = 0
    scaler_leakage_violations: int = 0
    violations: List[str] = Field(default_factory=list)


class LeakageAuditor:
    """Stateless verifier asserting all causal and split hygiene invariants."""

    def audit_split(
        self,
        train_features: Sequence[PointInTimeFeatureRow],
        train_targets: Sequence[PredictiveTargetRecord],
        val_features: Sequence[PointInTimeFeatureRow],
        val_targets: Sequence[PredictiveTargetRecord],
        test_features: Sequence[PointInTimeFeatureRow],
        test_targets: Sequence[PredictiveTargetRecord],
        min_embargo_seconds: float,
        fitted_scaler_sample_count: Optional[int] = None,
        allow_synthetic_in_test: bool = False,
    ) -> LeakageAuditReport:
        violations: List[str] = []
        all_rows = list(train_features) + list(val_features) + list(test_features)
        all_tgts = list(train_targets) + list(val_targets) + list(test_targets)

        syn_total = sum(1 for r in all_rows if r.is_synthetic) + sum(1 for t in all_tgts if t.is_synthetic)
        syn_test = sum(1 for r in test_features if r.is_synthetic)
        if not allow_synthetic_in_test and syn_total > 0:
            violations.append(
                f"EMPIRICAL_SYNTHETIC_CONTAMINATION: {syn_total} synthetic records present in empirical evaluation."
            )

        if not train_targets or not test_targets:
            violations.append("EMPTY_SPLIT: Train and test splits must both be non-empty.")

        # 1. Check train/val outcome maturity vs downstream split start
        val_start_ts = min((r.decision_timestamp for r in val_features), default=None)
        test_start_ts = min((r.decision_timestamp for r in test_features), default=None)

        overlap_v = 0
        embargo_v = 0
        dup_v = 0
        feat_ts_v = 0
        scaler_v = 0

        for t in train_targets:
            if not t.is_matured:
                overlap_v += 1
                violations.append(f"UNMATURED_TRAIN_TARGET: {t.event_id}")
            if val_start_ts is not None and t.exit_timestamp is not None and t.exit_timestamp > val_start_ts:
                overlap_v += 1
                violations.append(
                    f"TRAIN_VAL_OVERLAP: {t.event_id} exit {t.exit_timestamp.isoformat()} > val_start {val_start_ts.isoformat()}"
                )
            if test_start_ts is not None and t.exit_timestamp is not None and t.exit_timestamp > test_start_ts:
                overlap_v += 1
                violations.append(
                    f"TRAIN_TEST_OVERLAP: {t.event_id} exit {t.exit_timestamp.isoformat()} > test_start {test_start_ts.isoformat()}"
                )

        prior_end_ts = max(
            [r.decision_timestamp for r in (list(train_features) + list(val_features))],
            default=None,
        )
        prior_exit_end = max(
            [t.exit_timestamp for t in (list(train_targets) + list(val_targets)) if t.exit_timestamp is not None],
            default=None,
        )

        if test_start_ts is not None:
            for t in val_targets:
                if t.exit_timestamp is not None and t.exit_timestamp > test_start_ts:
                    overlap_v += 1
                    violations.append(
                        f"VAL_TEST_OVERLAP: {t.event_id} exit {t.exit_timestamp.isoformat()} > test_start {test_start_ts.isoformat()}"
                    )
            if prior_end_ts is not None and min_embargo_seconds > 0:
                gap_s = (test_start_ts - prior_end_ts).total_seconds()
                if gap_s + 1e-6 < min_embargo_seconds:
                    embargo_v += 1
                    violations.append(
                        f"EMBARGO_VIOLATION: gap between prior decision_ts and test_start ({gap_s:.1f}s) < {min_embargo_seconds:.1f}s"
                    )
            if prior_exit_end is not None and prior_exit_end > test_start_ts:
                overlap_v += 1
                violations.append("PRIOR_OUTCOME_EXIT_EXCEEDS_TEST_START")

        # 2. Check event_id & content_hash isolation (duplicate/revision leak checks)
        prior_eids = {r.event_id for r in list(train_features) + list(val_features)}
        test_eids = {r.event_id for r in test_features}
        shared_eids = prior_eids & test_eids
        if shared_eids:
            dup_v += len(shared_eids)
            violations.append(f"EVENT_ID_REVISION_LEAK: {sorted(shared_eids)[:5]}")

        prior_hashes = {r.content_hash for r in list(train_features) + list(val_features) if r.content_hash}
        test_hashes = {r.content_hash for r in test_features if r.content_hash}
        shared_hashes = prior_hashes & test_hashes
        if shared_hashes:
            dup_v += len(shared_hashes)
            violations.append(f"CONTENT_HASH_DUPLICATE_LEAK: {len(shared_hashes)} shared digests across boundary")

        # 3. Verify Group E/H/M feature timestamps <= decision_timestamp
        for r in all_rows:
            prov_ts_str = (r.provenance or {}).get("decision_timestamp")
            if prov_ts_str:
                try:
                    p_ts = datetime.fromisoformat(prov_ts_str.replace("Z", "+00:00"))
                    if p_ts > r.decision_timestamp:
                        feat_ts_v += 1
                        violations.append(f"FEATURE_PROV_FUTURE_TS: {r.event_id}")
                except Exception:
                    pass

        # 4. Verify transformer scaler fit count matches training fold only
        if fitted_scaler_sample_count is not None and fitted_scaler_sample_count != len(train_features):
            scaler_v += 1
            violations.append(
                f"SCALER_FIT_LEAKAGE: scaler fitted on {fitted_scaler_sample_count} samples != train fold {len(train_features)}"
            )

        unique_events = len({r.event_id for r in all_rows})
        unique_issuers = len({r.primary_issuer_id for r in all_rows if r.primary_issuer_id})

        return LeakageAuditReport(
            passed=(len(violations) == 0),
            empirical_mode=(not allow_synthetic_in_test),
            synthetic_count_total=syn_total,
            synthetic_count_in_test=syn_test,
            unique_events_total=unique_events,
            unique_issuers_total=unique_issuers,
            overlap_violations=overlap_v,
            embargo_violations=embargo_v,
            duplicate_or_revision_leaks=dup_v,
            feature_timestamp_violations=feat_ts_v,
            scaler_leakage_violations=scaler_v,
            violations=violations,
        )
