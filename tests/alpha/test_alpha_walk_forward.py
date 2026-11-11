"""Unit tests for Phase 5 Work Package G Purged Walk-Forward Validation & Leakage Audit."""

from datetime import datetime, timedelta, timezone
import pytest

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.leakage_audit import LeakageAuditor
from backend.alpha.outcomes import OutcomeDataQualityStatus, OutcomeHorizon, OutcomeLabelType
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_targets import ActionableDirectionClass, PredictiveTargetRecord
from backend.alpha.walk_forward import PurgedWalkForwardSplitter, WalkForwardMode


def _make_item(idx: int, ts: datetime, eid: str = None, chash: str = None, issuer: str = "TCS", is_synthetic: bool = True):
    event_id = eid or f"evt_{idx}"
    r = PointInTimeFeatureRow(
        event_id=event_id,
        instrument_id=f"NSE:{issuer}:EQ",
        jurisdiction=MarketJurisdiction.IN,
        decision_timestamp=ts,
        horizon_label="1d",
        is_synthetic=is_synthetic,
        content_hash=chash or f"hash_{idx}",
        primary_issuer_id=issuer,
        values={"e_mat_composite": 0.6, "m_available": 1.0},
    )
    t = PredictiveTargetRecord(
        event_id=event_id,
        instrument_id=f"NSE:{issuer}:EQ",
        jurisdiction=MarketJurisdiction.IN,
        decision_timestamp=ts,
        horizon=OutcomeHorizon.D1,
        label_type=OutcomeLabelType.EXECUTABLE,
        entry_timestamp=ts,
        exit_timestamp=ts + timedelta(days=1),
        simple_return=0.01 if idx % 2 == 0 else -0.01,
        cost_scenario="TEST",
        y_continuous=0.01 if idx % 2 == 0 else -0.01,
        y_direction_3class=ActionableDirectionClass.LONG_POSITIVE if idx % 2 == 0 else ActionableDirectionClass.SHORT_NEGATIVE,
        y_positive_material=1 if idx % 2 == 0 else 0,
        y_negative_material=0 if idx % 2 == 0 else 1,
        y_material_move=1,
        y_positive_raw=1 if idx % 2 == 0 else 0,
        data_quality_status=OutcomeDataQualityStatus.VERIFIED,
        is_matured=True,
        is_trainable=True,
        source_provenance="SYN",
        is_synthetic=is_synthetic,
    )
    return r, t


def test_purged_walk_forward_prevents_overlap_duplicates_revisions_and_synthetic():
    """Verify chronological walk-forward folds purge boundary overlaps/duplicates and reject synthetic in empirical mode."""
    t0 = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
    rows = []
    tgts = []
    # Space 40 events 2 days apart so clean folds + boundary cases exist
    for i in range(40):
        ts = t0 + timedelta(days=i * 2)
        # Inject deliberate duplicate hash from index 5 onto index 35 (later test block)
        chash = "dup_shared_hash" if i in (5, 35) else f"h_{i}"
        # Inject same event_id revision across fold cut (index 6 vs index 36)
        eid = "shared_rev_evt" if i in (6, 36) else f"evt_{i}"
        r, t = _make_item(i, ts, eid=eid, chash=chash, is_synthetic=True)
        rows.append(r)
        tgts.append(t)

    # Empirical mode (`allow_synthetic=False`) MUST refuse synthetic fixtures!
    empirical_splitter = PurgedWalkForwardSplitter(horizon=OutcomeHorizon.D1, n_folds=3, allow_synthetic=False)
    with pytest.raises(ValueError, match="Synthetic record"):
        empirical_splitter.generate_folds(rows, tgts)

    # Embargo shorter than horizon MUST raise
    with pytest.raises(ValueError, match="Embargo duration"):
        PurgedWalkForwardSplitter(horizon=OutcomeHorizon.D1, embargo_seconds=60.0)

    splitter = PurgedWalkForwardSplitter(
        horizon=OutcomeHorizon.D1,
        n_folds=3,
        mode=WalkForwardMode.EXPANDING,
        embargo_seconds=86400.0,
        issuer_cycle_purge_seconds=3 * 86400.0,
        allow_synthetic=True,
    )
    folds, _ = splitter.generate_folds(rows, tgts)
    assert len(folds) >= 2
    for f in folds:
        assert f.leakage_audit.passed is True
        assert f.leakage_audit.overlap_violations == 0
        assert f.leakage_audit.embargo_violations == 0
        assert f.leakage_audit.duplicate_or_revision_leaks == 0
        assert f.train_end < f.test_start
        # Duplicate hash / revision at test index 35/36 must never stay inside train/val of Fold 3
        prior_ids = set(f.train_indices) | set(f.val_indices)
        test_ids = set(f.test_indices)
        assert not ({5, 35}.issubset(prior_ids | test_ids) and (5 in prior_ids and 35 in test_ids))
        assert not ({6, 36}.issubset(prior_ids | test_ids) and (6 in prior_ids and 36 in test_ids))

    # Verify LeakageAuditor catches scaler sample count mismatch if full dataset fitted
    aud = LeakageAuditor()
    f0 = folds[0]
    bad_scaler = aud.audit_split(
        [rows[i] for i in f0.train_indices],
        [tgts[i] for i in f0.train_indices],
        [rows[i] for i in f0.val_indices],
        [tgts[i] for i in f0.val_indices],
        [rows[i] for i in f0.test_indices],
        [tgts[i] for i in f0.test_indices],
        min_embargo_seconds=86400.0,
        fitted_scaler_sample_count=len(rows),  # LEAKED full dataset fit!
        allow_synthetic_in_test=True,
    )
    assert bad_scaler.passed is False
    assert bad_scaler.scaler_leakage_violations == 1
