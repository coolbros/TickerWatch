"""Versioned Point-in-Time Predictive Feature Registry & Fold-Safe Transformer (Phase 5 WP-C).

Defines the canonical schema of E (Event Intelligence), H (Historical Intelligence),
and M (Market Intelligence) features, along with model ablation subsets (Model A-E),
immutable feature manifests, and a training-fold-only scaler/imputer preventing
distributional data leakage across walk-forward splits.
"""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import numpy as np
from pydantic import BaseModel, Field


FEATURE_SCHEMA_VERSION = "5.0.0-alpha"


class FeatureGroup(str, Enum):
    """Canonical intelligence stream grouping for systematic causal ablation."""

    E = "E"  # Phase 3 NLP event intelligence
    H = "H"  # Phase 4 historical analogue intelligence
    M = "M"  # Phase 2 & 4 point-in-time market microstructure & regime state


class ModelConfiguration(str, Enum):
    """Experimental model feature-set configurations per Phase 5 specification."""

    MODEL_A_MARKET_ONLY = "MODEL_A_MARKET_ONLY"             # P(dF | M_t)
    MODEL_B_EVENT_ONLY = "MODEL_B_EVENT_ONLY"               # P(dF | E_t)
    MODEL_C_EVENT_PLUS_HIST = "MODEL_C_EVENT_PLUS_HIST"     # P(dF | E_t, H_t)
    MODEL_D_FULL_SYSTEM = "MODEL_D_FULL_SYSTEM"             # P(dF | E_t, H_t, M_t)
    MODEL_E_EVENT_PLUS_MARKET = "MODEL_E_EVENT_PLUS_MARKET" # P(dF | E_t, M_t)

    @property
    def active_groups(self) -> Set[FeatureGroup]:
        return {
            ModelConfiguration.MODEL_A_MARKET_ONLY: {FeatureGroup.M},
            ModelConfiguration.MODEL_B_EVENT_ONLY: {FeatureGroup.E},
            ModelConfiguration.MODEL_C_EVENT_PLUS_HIST: {FeatureGroup.E, FeatureGroup.H},
            ModelConfiguration.MODEL_D_FULL_SYSTEM: {FeatureGroup.E, FeatureGroup.H, FeatureGroup.M},
            ModelConfiguration.MODEL_E_EVENT_PLUS_MARKET: {FeatureGroup.E, FeatureGroup.M},
        }[self]


class FeatureDefinition(BaseModel):
    """Metadata schema describing a single versioned predictive feature."""

    name: str
    group: FeatureGroup
    dtype: str = "float64"
    description: str
    default_impute: float = 0.0
    scale_with_fold_scaler: bool = True
    is_missingness_flag: bool = False


class FeatureManifest(BaseModel):
    """Machine-readable immutable contract describing a feature matrix configuration."""

    schema_version: str = FEATURE_SCHEMA_VERSION
    feature_names: List[str]
    groups_included: List[str]
    definition_count: int
    manifest_sha256: str


# Canonical ordered list of Phase 5 predictive features
CANONICAL_FEATURE_DEFINITIONS: List[FeatureDefinition] = [
    # ==================== GROUP E: Event Intelligence (Phase 3) ====================
    FeatureDefinition(name="e_cls_confidence", group=FeatureGroup.E, description="Event classification confidence [0,1]", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_severity_score", group=FeatureGroup.E, description="Inherent event severity tier score [0,1]", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_is_multi_label", group=FeatureGroup.E, description="Whether event triggered multiple subtypes", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_fam_corp_fin", group=FeatureGroup.E, description="One-hot CORPORATE_FINANCIAL family", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_fam_corp_op", group=FeatureGroup.E, description="One-hot CORPORATE_OPERATIONAL family", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_fam_macro", group=FeatureGroup.E, description="One-hot MACROECONOMIC family", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_fam_market", group=FeatureGroup.E, description="One-hot MARKET_SYSTEMIC family", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_earnings", group=FeatureGroup.E, description="Subtype Earnings/Revenue/Profit indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_guidance", group=FeatureGroup.E, description="Subtype Guidance Revision indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_contract_win", group=FeatureGroup.E, description="Subtype Contract Order Win indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_contract_cancel", group=FeatureGroup.E, description="Subtype Contract Cancellation indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_mna", group=FeatureGroup.E, description="Subtype M&A/Investment indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_reg_legal", group=FeatureGroup.E, description="Subtype Regulatory/Litigation/Bankruptcy indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_macro_rate_cpi", group=FeatureGroup.E, description="Subtype Rate/CPI Macro indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_sub_routine", group=FeatureGroup.E, description="Subtype Routine Administrative indicator", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_entity_resolved", group=FeatureGroup.E, description="1 if primary entity cleanly resolved", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_entity_confidence", group=FeatureGroup.E, description="Primary entity resolution confidence [0,1]", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_entity_count", group=FeatureGroup.E, description="Total resolved corporate entities count", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="e_direct_inst_count", group=FeatureGroup.E, description="Count of DIRECT instrument mappings", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="e_mat_composite", group=FeatureGroup.E, description="Composite materiality score [0,1]", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_mat_magnitude", group=FeatureGroup.E, description="Materiality quantitative magnitude component", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_mat_forward", group=FeatureGroup.E, description="Materiality forward-guidance component", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_mat_novelty", group=FeatureGroup.E, description="Announcement novelty sub-score", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_mat_spillover", group=FeatureGroup.E, description="Market/systemic spillover sub-score", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_has_monetary_metric", group=FeatureGroup.E, description="1 if normalized monetary value present", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_log10_magnitude", group=FeatureGroup.E, description="log10(1 + |normalized monetary value|) within currency scale", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="e_currency_inr", group=FeatureGroup.E, description="1 if primary metric currency is INR", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_currency_usd", group=FeatureGroup.E, description="1 if primary metric currency is USD", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_pct_metric_signed", group=FeatureGroup.E, description="Extracted signed percentage change if present", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="e_pct_metric_missing", group=FeatureGroup.E, description="Explicit missing indicator for signed percentage metric", default_impute=1.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="e_surprise_missing", group=FeatureGroup.E, description="1 if real consensus unavailable (NEVER conflated with 0 surprise)", default_impute=1.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="e_surprise_pct_signed", group=FeatureGroup.E, description="Percentage surprise (only valid when consensus observed)", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="e_surprise_z", group=FeatureGroup.E, description="Standardised surprise Z-score (only when dispersion observed)", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="e_surprise_dir_positive", group=FeatureGroup.E, description="1 strictly for positive verified surprise", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_surprise_dir_negative", group=FeatureGroup.E, description="1 strictly for negative verified surprise", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_is_cost_metric", group=FeatureGroup.E, description="1 if primary surprise evaluates an inverted cost/debt metric", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_composite_confidence", group=FeatureGroup.E, description="Phase 3 NLP composite confidence", scale_with_fold_scaler=False),
    FeatureDefinition(name="e_is_revision", group=FeatureGroup.E, description="1 if disclosure is a correction/amendment", scale_with_fold_scaler=False),

    # ==================== GROUP H: Historical Intelligence (Phase 4) ====================
    FeatureDefinition(name="h_comparable_count", group=FeatureGroup.H, description="Total causally eligible comparable events in store", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_matched_count", group=FeatureGroup.H, description="Top-k retrieved historical analogue count", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_matured_count", group=FeatureGroup.H, description="Count of retrieved analogues with matured target horizon outcome", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_sample_weight_saturation", group=FeatureGroup.H, description="min(1.0, h_matured_count / 30.0) separating small samples from large samples", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_insufficient_evidence", group=FeatureGroup.H, description="1 if matured_count < min_sample_size or no outcomes", default_impute=1.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="h_outcomes_missing", group=FeatureGroup.H, description="1 if target horizon mean return is unavailable", default_impute=1.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="h_mean_return", group=FeatureGroup.H, description="Matured historical mean return at horizon h (gated)", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_sim_weighted_return", group=FeatureGroup.H, description="Similarity-weighted matured return at horizon h", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_median_return", group=FeatureGroup.H, description="Matured historical median return at horizon h", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_return_std", group=FeatureGroup.H, description="Historical outcome dispersion at horizon h", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_pos_freq", group=FeatureGroup.H, description="Historical positive return frequency [0,1]", default_impute=0.5, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_neg_freq", group=FeatureGroup.H, description="Historical negative return frequency [0,1]", default_impute=0.5, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_iqr_spread", group=FeatureGroup.H, description="Empirical quantile spread (p75 - p25)", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_bootstrap_ci_width", group=FeatureGroup.H, description="95% bootstrap confidence interval width", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_mean_bench_adj_return", group=FeatureGroup.H, description="Matured historical market-adjusted return", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_recency_days_latest", group=FeatureGroup.H, description="Days since most recent comparable analogue", default_impute=365.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_mean_similarity", group=FeatureGroup.H, description="Average hybrid similarity across top-k matches", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_top1_semantic_sim", group=FeatureGroup.H, description="Top analogue semantic cosine similarity", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_top1_bm25_sim", group=FeatureGroup.H, description="Top analogue causal BM25 similarity", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_top1_subtype_match", group=FeatureGroup.H, description="Top analogue subtype match [0,1]", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_top1_inst_match", group=FeatureGroup.H, description="Top analogue instrument/sector match [0,1]", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="h_inst_slice_mean", group=FeatureGroup.H, description="Instrument-conditioned historical return", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_inst_slice_matured_count", group=FeatureGroup.H, description="Sample count for instrument-specific slice", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_mat_slice_mean", group=FeatureGroup.H, description="Materiality-conditioned historical return", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_surprise_slice_mean", group=FeatureGroup.H, description="Surprise-conditioned historical return (when consensus exists)", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="h_surprise_slice_available", group=FeatureGroup.H, description="1 if real consensus allowed surprise-conditioned slice", default_impute=0.0, scale_with_fold_scaler=False, is_missingness_flag=True),

    # ==================== GROUP M: Market Intelligence (Phases 2 & 4) ====================
    FeatureDefinition(name="m_available", group=FeatureGroup.M, description="1 if pre-event market microstructure state observed", default_impute=0.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="m_is_stale", group=FeatureGroup.M, description="1 if latest pre-event tick exceeded staleness budget", default_impute=1.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="m_feed_freshness_ms", group=FeatureGroup.M, description="Feed age (ms) relative to decision_timestamp", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_ret_1m", group=FeatureGroup.M, description="Trailing 1-minute return strictly <= decision_timestamp", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_ret_5m", group=FeatureGroup.M, description="Trailing 5-minute return strictly <= decision_timestamp", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_ret_30m", group=FeatureGroup.M, description="Trailing 30-minute return strictly <= decision_timestamp", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_bench_ret_30m", group=FeatureGroup.M, description="Benchmark trailing 30-minute return <= decision_timestamp", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_realized_vol_bps", group=FeatureGroup.M, description="Trailing realized volatility in bps", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_spread_bps", group=FeatureGroup.M, description="As-of bid-ask spread in bps", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_top_depth_log1p", group=FeatureGroup.M, description="log1p(top-of-book total bid+ask quantity)", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_depth_imbalance", group=FeatureGroup.M, description="Order book imbalance [-1, 1]", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_microprice_offset_bps", group=FeatureGroup.M, description="Microprice minus mid-price in bps", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_dir_bullish", group=FeatureGroup.M, description="1 if pre-event trend classification BULLISH", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_dir_bearish", group=FeatureGroup.M, description="1 if pre-event trend classification BEARISH", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_vol_elevated", group=FeatureGroup.M, description="1 if VolatilityRegime.ELEVATED", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_liq_thin", group=FeatureGroup.M, description="1 if LiquidityRegime is THIN or CLOSED_OR_HALTED", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_session_open", group=FeatureGroup.M, description="1 if decision occurs during active exchange session", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_session_progress", group=FeatureGroup.M, description="Normalized time in active session [0,1] (-1 if out of session)", default_impute=-1.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_is_first_30m", group=FeatureGroup.M, description="1 if within first 30m of exchange open", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_is_last_30m", group=FeatureGroup.M, description="1 if within last 30m before exchange close", default_impute=0.0, scale_with_fold_scaler=False),
    FeatureDefinition(name="m_futures_basis_bps", group=FeatureGroup.M, description="Futures basis relative to spot in bps when available", default_impute=0.0, scale_with_fold_scaler=True),
    FeatureDefinition(name="m_futures_basis_missing", group=FeatureGroup.M, description="1 if futures basis metadata unavailable", default_impute=1.0, scale_with_fold_scaler=False, is_missingness_flag=True),
    FeatureDefinition(name="m_days_to_expiry", group=FeatureGroup.M, description="Calendar days to active contract expiry when available", default_impute=0.0, scale_with_fold_scaler=True),
]

FEATURE_DEFINITION_MAP: Dict[str, FeatureDefinition] = {d.name: d for d in CANONICAL_FEATURE_DEFINITIONS}


def get_feature_names_for_config(config: ModelConfiguration) -> List[str]:
    """Return ordered list of active feature names for a Model A-E configuration."""
    groups = config.active_groups
    return [d.name for d in CANONICAL_FEATURE_DEFINITIONS if d.group in groups]


def build_feature_manifest(config: ModelConfiguration = ModelConfiguration.MODEL_D_FULL_SYSTEM) -> FeatureManifest:
    """Create a cryptographic manifest identifying an exact feature schema version and subset."""
    names = get_feature_names_for_config(config)
    groups = sorted(g.value for g in config.active_groups)
    payload = json.dumps({"schema_version": FEATURE_SCHEMA_VERSION, "features": names}, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return FeatureManifest(
        schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=names,
        groups_included=groups,
        definition_count=len(names),
        manifest_sha256=digest,
    )


class FoldSafeFeatureTransformer:
    """
    Training-fold-only imputer and Z-score scaler.
    Ensures validation/test fold distributions NEVER contaminate training scaling statistics.
    """

    def __init__(self, feature_names: Sequence[str], clip_z: float = 8.0) -> None:
        self.feature_names = list(feature_names)
        self.clip_z = clip_z
        self._means: Optional[np.ndarray] = None
        self._stds: Optional[np.ndarray] = None
        self._scale_mask: Optional[np.ndarray] = None
        self._defaults: Optional[np.ndarray] = None
        self.is_fitted: bool = False
        self.fit_sample_count: int = 0

    def _rows_to_raw_matrix(self, feature_dicts: Sequence[Dict[str, Optional[float]]]) -> np.ndarray:
        n = len(feature_dicts)
        d = len(self.feature_names)
        mat = np.full((n, d), np.nan, dtype=np.float64)
        for i, row in enumerate(feature_dicts):
            for j, fname in enumerate(self.feature_names):
                val = row.get(fname)
                if val is not None and not np.isnan(val):
                    mat[i, j] = float(val)
        return mat

    def fit(self, train_feature_dicts: Sequence[Dict[str, Optional[float]]]) -> "FoldSafeFeatureTransformer":
        if not train_feature_dicts:
            raise ValueError("Cannot fit FoldSafeFeatureTransformer on empty training set")
        raw = self._rows_to_raw_matrix(train_feature_dicts)
        d = len(self.feature_names)

        defaults = np.zeros(d, dtype=np.float64)
        scale_mask = np.zeros(d, dtype=bool)
        means = np.zeros(d, dtype=np.float64)
        stds = np.ones(d, dtype=np.float64)

        for j, fname in enumerate(self.feature_names):
            defn = FEATURE_DEFINITION_MAP[fname]
            defaults[j] = float(defn.default_impute)
            scale_mask[j] = bool(defn.scale_with_fold_scaler)
            col = raw[:, j]
            valid = col[~np.isnan(col)]
            if valid.size == 0:
                means[j] = defaults[j]
                stds[j] = 1.0
            else:
                if scale_mask[j]:
                    means[j] = float(np.mean(valid))
                    s = float(np.std(valid))
                    stds[j] = s if s > 1e-8 else 1.0
                else:
                    # Keep bounded binary/probability/missingness columns unshifted
                    means[j] = defaults[j]
                    stds[j] = 1.0

        self._defaults = defaults
        self._scale_mask = scale_mask
        self._means = means
        self._stds = stds
        self.is_fitted = True
        self.fit_sample_count = len(train_feature_dicts)
        return self

    def transform(self, feature_dicts: Sequence[Dict[str, Optional[float]]]) -> np.ndarray:
        if not self.is_fitted or self._means is None or self._stds is None or self._scale_mask is None:
            raise RuntimeError("FoldSafeFeatureTransformer must be fitted on training folds before transform()")
        if not feature_dicts:
            return np.zeros((0, len(self.feature_names)), dtype=np.float64)
        raw = self._rows_to_raw_matrix(feature_dicts)
        out = raw.copy()
        for j in range(len(self.feature_names)):
            col = out[:, j]
            nan_mask = np.isnan(col)
            if nan_mask.any():
                col[nan_mask] = self._means[j]
            if self._scale_mask[j]:
                z = (col - self._means[j]) / self._stds[j]
                out[:, j] = np.clip(z, -self.clip_z, self.clip_z)
            else:
                out[:, j] = col
        return out

    def fit_transform(self, train_feature_dicts: Sequence[Dict[str, Optional[float]]]) -> np.ndarray:
        self.fit(train_feature_dicts)
        return self.transform(train_feature_dicts)

    def get_fit_statistics(self) -> Dict[str, Any]:
        if not self.is_fitted or self._means is None or self._stds is None:
            return {}
        return {
            "fit_sample_count": self.fit_sample_count,
            "feature_count": len(self.feature_names),
            "means": {n: round(float(self._means[i]), 6) for i, n in enumerate(self.feature_names)},
            "stds": {n: round(float(self._stds[i]), 6) for i, n in enumerate(self.feature_names)},
        }
