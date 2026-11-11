"""Point-in-Time Predictive Feature Extraction Builder (Phase 5 WP-C).

Constructs strictly causal, provenance-tracked feature vectors spanning:
- Group E: Phase 3 structured FinancialEventRepresentation (NLP classification, materiality, surprise).
- Group H: Phase 4 causally matured HistoricalIntelligenceRepresentation (analogue outcome stats,
           sample-count gating, similarity breakdowns, conditional slices).
- Group M: Phase 2/4 MarketRegimeContext (pre-event returns, volatility, L1/L2 depth, spreads,
           exchange session time, and optional pre-decision futures basis/expiry metadata).
"""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from backend.alpha.alignment import MarketSessionStatus
from backend.alpha.contextual_features import EvidenceStatus
from backend.alpha.entities import ResolutionState
from backend.alpha.event_classifier import EventPrimaryFamily, EventSubtype
from backend.alpha.event_extraction import CurrencyUnit
from backend.alpha.event_study import MarketCalendarConfig
from backend.alpha.feature_registry import (
    FEATURE_SCHEMA_VERSION,
    FeatureManifest,
    ModelConfiguration,
    build_feature_manifest,
)
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_intelligence import (
    HistoricalIntelligenceRepresentation,
    assert_valid_phase4_training_input,
)
from backend.alpha.market_context import MarketDirection, MarketRegimeContext, VolatilityRegime, LiquidityRegime
from backend.alpha.nlp_pipeline import FinancialEventRepresentation
from backend.alpha.outcomes import OutcomeHorizon
from backend.alpha.surprise import SurpriseCategory


class FuturesBasisMetadata(BaseModel):
    """Point-in-time futures contract metadata available strictly <= decision_timestamp."""

    contract_id: str
    as_of_timestamp: datetime
    futures_price: float = Field(..., gt=0.0)
    spot_price: float = Field(..., gt=0.0)
    expiry_date: date


class PointInTimeFeatureRow(BaseModel):
    """Single observation feature vector with complete causal provenance and missingness metadata."""

    event_id: str
    instrument_id: str
    jurisdiction: MarketJurisdiction
    decision_timestamp: datetime
    horizon_label: str
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    is_synthetic: bool = False
    is_revision: bool = False
    content_hash: Optional[str] = None
    primary_issuer_id: Optional[str] = None

    values: Dict[str, Optional[float]] = Field(default_factory=dict)
    missing_features: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)

    def subset(self, config: ModelConfiguration) -> Dict[str, Optional[float]]:
        """Project strictly the features permitted under ModelConfiguration (A-E)."""
        allowed = config.active_groups
        from backend.alpha.feature_registry import FEATURE_DEFINITION_MAP

        return {
            k: v for k, v in self.values.items()
            if k in FEATURE_DEFINITION_MAP and FEATURE_DEFINITION_MAP[k].group in allowed
        }


class PointInTimeFeatureBuilder:
    """
    Deterministic builder assembling Group E, H, and M predictive features strictly from
    point-in-time Phase 3 & Phase 4 representations.
    """

    def __init__(self, horizon: OutcomeHorizon = OutcomeHorizon.D1) -> None:
        self.horizon = horizon

    @staticmethod
    def _extract_group_e(
        rep: FinancialEventRepresentation,
        is_revision: bool,
    ) -> Dict[str, Optional[float]]:
        cls = rep.classification
        fam = cls.primary_family
        sub = cls.primary_subtype
        mat = rep.materiality
        sur = rep.surprise
        ent = rep.primary_entity

        earnings_subs = {
            EventSubtype.EARNINGS_ANNOUNCEMENT,
            EventSubtype.REVENUE_SURPRISE,
            EventSubtype.PROFIT_SURPRISE,
            EventSubtype.MARGIN_CHANGE,
        }
        mna_subs = {EventSubtype.MERGER_ACQUISITION, EventSubtype.STRATEGIC_INVESTMENT}
        reg_subs = {
            EventSubtype.REGULATORY_INVESTIGATION,
            EventSubtype.LITIGATION,
            EventSubtype.BANKRUPTCY_INSOLVENCY,
            EventSubtype.CREDIT_RATING_CHANGE,
        }
        macro_subs = {
            EventSubtype.INTEREST_RATE_DECISION,
            EventSubtype.INFLATION_CPI,
            EventSubtype.MONETARY_POLICY_GUIDANCE,
            EventSubtype.GDP_RELEASE,
            EventSubtype.EMPLOYMENT_DATA,
            EventSubtype.FISCAL_POLICY,
        }

        primary_m = rep.extraction.primary_metric
        monetary_val: Optional[float] = None
        currency = CurrencyUnit.UNKNOWN
        pct_val: Optional[float] = None
        for m in ([primary_m] if primary_m else []) + list(rep.extraction.metrics):
            if monetary_val is None and not m.is_percentage and not m.is_basis_points and m.currency in (
                CurrencyUnit.INR,
                CurrencyUnit.USD,
                CurrencyUnit.EUR,
                CurrencyUnit.GBP,
                CurrencyUnit.JPY,
            ):
                monetary_val = float(abs(m.normalized_value))
                currency = m.currency
            if pct_val is None and m.is_percentage:
                pct_val = float(m.normalized_value)

        has_surprise = sur.surprise_category not in (
            SurpriseCategory.UNAVAILABLE_EXPECTATION,
            SurpriseCategory.UNQUANTIFIED,
        )

        # Direct instrument count
        direct_count = sum(
            1 for m in rep.all_instrument_mappings
            if getattr(m.relevance, "value", str(m.relevance)) == "DIRECT"
        )

        return {
            "e_cls_confidence": float(cls.confidence),
            "e_severity_score": float(cls.severity_score),
            "e_is_multi_label": 1.0 if cls.is_multi_label else 0.0,
            "e_fam_corp_fin": 1.0 if fam == EventPrimaryFamily.CORPORATE_FINANCIAL else 0.0,
            "e_fam_corp_op": 1.0 if fam == EventPrimaryFamily.CORPORATE_OPERATIONAL else 0.0,
            "e_fam_macro": 1.0 if fam == EventPrimaryFamily.MACROECONOMIC else 0.0,
            "e_fam_market": 1.0 if fam == EventPrimaryFamily.MARKET_SYSTEMIC else 0.0,
            "e_sub_earnings": 1.0 if sub in earnings_subs else 0.0,
            "e_sub_guidance": 1.0 if sub == EventSubtype.GUIDANCE_REVISION else 0.0,
            "e_sub_contract_win": 1.0 if sub == EventSubtype.CONTRACT_ORDER_WIN else 0.0,
            "e_sub_contract_cancel": 1.0 if sub == EventSubtype.CONTRACT_CANCELLATION else 0.0,
            "e_sub_mna": 1.0 if sub in mna_subs else 0.0,
            "e_sub_reg_legal": 1.0 if sub in reg_subs else 0.0,
            "e_sub_macro_rate_cpi": 1.0 if sub in macro_subs else 0.0,
            "e_sub_routine": 1.0 if sub == EventSubtype.ROUTINE_ADMINISTRATIVE else 0.0,
            "e_entity_resolved": 1.0 if ent.resolution_state == ResolutionState.RESOLVED else 0.0,
            "e_entity_confidence": float(ent.confidence),
            "e_entity_count": float(len([e for e in rep.all_entities if e.resolution_state == ResolutionState.RESOLVED])),
            "e_direct_inst_count": float(direct_count),
            "e_mat_composite": float(mat.composite_materiality_score),
            "e_mat_magnitude": float(mat.quantitative_magnitude_score),
            "e_mat_forward": float(mat.forward_guidance_score),
            "e_mat_novelty": float(mat.novelty_score),
            "e_mat_spillover": float(mat.spillover_potential_score),
            "e_has_monetary_metric": 1.0 if monetary_val is not None else 0.0,
            "e_log10_magnitude": round(math.log10(1.0 + monetary_val), 6) if monetary_val is not None else None,
            "e_currency_inr": 1.0 if currency == CurrencyUnit.INR else 0.0,
            "e_currency_usd": 1.0 if currency == CurrencyUnit.USD else 0.0,
            "e_pct_metric_signed": pct_val,
            "e_pct_metric_missing": 0.0 if pct_val is not None else 1.0,
            "e_surprise_missing": 0.0 if has_surprise else 1.0,
            "e_surprise_pct_signed": float(sur.percentage_surprise) if (has_surprise and sur.percentage_surprise is not None) else None,
            "e_surprise_z": float(sur.standardised_surprise_z) if (has_surprise and sur.standardised_surprise_z is not None) else None,
            "e_surprise_dir_positive": 1.0 if (has_surprise and sur.surprise_category == SurpriseCategory.POSITIVE_SURPRISE) else 0.0,
            "e_surprise_dir_negative": 1.0 if (has_surprise and sur.surprise_category == SurpriseCategory.NEGATIVE_SURPRISE) else 0.0,
            "e_is_cost_metric": 1.0 if sur.is_cost_metric else 0.0,
            "e_composite_confidence": float(rep.composite_confidence),
            "e_is_revision": 1.0 if is_revision else 0.0,
        }

    def _extract_group_h(
        self,
        hist_rep: HistoricalIntelligenceRepresentation,
    ) -> Dict[str, Optional[float]]:
        ctx = hist_rep.contextual_features
        h_key = self.horizon.value
        dist = hist_rep.return_distributions.get(h_key)

        matured_cnt = dist.matured_count if dist else 0
        insuff = (dist is None) or dist.insufficient_evidence or (dist.evidence_status != EvidenceStatus.SUFFICIENT)
        outcomes_missing = (dist is None) or (dist.mean_return is None)

        iqr: Optional[float] = None
        if dist and dist.quantiles is not None:
            iqr = float(dist.quantiles.p75 - dist.quantiles.p25)
        ci_w: Optional[float] = None
        if dist and dist.bootstrap_ci_95 is not None:
            ci_w = float(dist.bootstrap_ci_95.upper - dist.bootstrap_ci_95.lower)

        # Retrieval similarity components on top match
        top_match = hist_rep.retrieved_matches[0] if hist_rep.retrieved_matches else None
        top_bd = top_match.score_breakdown if top_match else None

        # Conditional slices for this horizon
        inst_slice = hist_rep.conditional_slices.get(f"INSTRUMENT_SPECIFIC_{h_key}")
        mat_slice = hist_rep.conditional_slices.get(f"MATERIALITY_MATCHED_{h_key}")
        surp_slice = hist_rep.conditional_slices.get(f"SURPRISE_MATCHED_{h_key}")
        surp_avail = (
            surp_slice is not None
            and surp_slice.evidence_status != EvidenceStatus.EXPECTATION_UNAVAILABLE
            and surp_slice.mean_return is not None
        )

        return {
            "h_comparable_count": float(hist_rep.comparable_event_count),
            "h_matched_count": float(hist_rep.matched_event_count),
            "h_matured_count": float(matured_cnt),
            "h_sample_weight_saturation": min(1.0, matured_cnt / 30.0),
            "h_insufficient_evidence": 1.0 if insuff else 0.0,
            "h_outcomes_missing": 1.0 if outcomes_missing else 0.0,
            "h_mean_return": float(dist.mean_return) if (dist and dist.mean_return is not None) else None,
            "h_sim_weighted_return": float(dist.similarity_weighted_mean_return) if (dist and dist.similarity_weighted_mean_return is not None) else None,
            "h_median_return": float(dist.median_return) if (dist and dist.median_return is not None) else None,
            "h_return_std": float(dist.std_dev) if (dist and dist.std_dev is not None) else None,
            "h_pos_freq": float(dist.positive_frequency) if (dist and dist.positive_frequency is not None) else None,
            "h_neg_freq": float(dist.negative_frequency) if (dist and dist.negative_frequency is not None) else None,
            "h_iqr_spread": iqr,
            "h_bootstrap_ci_width": ci_w,
            "h_mean_bench_adj_return": float(dist.mean_benchmark_adjusted_return) if (dist and dist.mean_benchmark_adjusted_return is not None) else None,
            "h_recency_days_latest": float(ctx.recency_days_latest) if ctx.recency_days_latest is not None else None,
            "h_mean_similarity": float(ctx.mean_match_similarity) if ctx.mean_match_similarity is not None else 0.0,
            "h_top1_semantic_sim": float(top_bd.semantic_cosine) if top_bd else 0.0,
            "h_top1_bm25_sim": float(top_bd.lexical_bm25_norm) if top_bd else 0.0,
            "h_top1_subtype_match": float(top_bd.event_subtype_match) if top_bd else 0.0,
            "h_top1_inst_match": float(top_bd.instrument_match) if top_bd else 0.0,
            "h_inst_slice_mean": float(inst_slice.mean_return) if (inst_slice and inst_slice.mean_return is not None) else None,
            "h_inst_slice_matured_count": float(inst_slice.matured_count) if inst_slice else 0.0,
            "h_mat_slice_mean": float(mat_slice.mean_return) if (mat_slice and mat_slice.mean_return is not None) else None,
            "h_surprise_slice_mean": float(surp_slice.mean_return) if surp_avail else None,
            "h_surprise_slice_available": 1.0 if surp_avail else 0.0,
        }

    @staticmethod
    def _extract_group_m(
        decision_timestamp: datetime,
        jurisdiction: MarketJurisdiction,
        market_regime: Optional[MarketRegimeContext],
        futures_meta: Optional[FuturesBasisMetadata] = None,
    ) -> Dict[str, Optional[float]]:
        # Assert causal timestamp invariants on market regime & futures basis inputs
        if market_regime is not None and market_regime.latest_market_timestamp is not None:
            if market_regime.latest_market_timestamp > decision_timestamp:
                raise ValueError(
                    f"Causal leakage in M_t: latest_market_timestamp {market_regime.latest_market_timestamp} > decision {decision_timestamp}"
                )
        if futures_meta is not None and futures_meta.as_of_timestamp > decision_timestamp:
            raise ValueError(
                f"Causal leakage in M_t futures metadata: {futures_meta.as_of_timestamp} > decision {decision_timestamp}"
            )

        cal = MarketCalendarConfig.for_jurisdiction(jurisdiction)
        loc_dt = decision_timestamp.astimezone(ZoneInfo(cal.timezone_name))
        in_sess = cal.is_in_session(decision_timestamp)
        session_prog = -1.0
        first_30 = 0.0
        last_30 = 0.0
        if in_sess and not cal.is_24x7:
            open_dt = datetime.combine(loc_dt.date(), cal.session_open_local, tzinfo=loc_dt.tzinfo)
            close_dt = datetime.combine(loc_dt.date(), cal.session_close_local, tzinfo=loc_dt.tzinfo)
            tot_s = max(1.0, (close_dt - open_dt).total_seconds())
            elapsed_s = (loc_dt - open_dt).total_seconds()
            rem_s = (close_dt - loc_dt).total_seconds()
            session_prog = max(0.0, min(1.0, elapsed_s / tot_s))
            first_30 = 1.0 if elapsed_s <= 1800.0 else 0.0
            last_30 = 1.0 if rem_s <= 1800.0 else 0.0

        basis_bps: Optional[float] = None
        days_exp: Optional[float] = None
        if futures_meta is not None:
            basis_bps = round(((futures_meta.futures_price - futures_meta.spot_price) / futures_meta.spot_price) * 10000.0, 4)
            days_exp = float(max(0, (futures_meta.expiry_date - loc_dt.date()).days))

        if market_regime is None or market_regime.pre_event_observation_count == 0:
            return {
                "m_available": 0.0,
                "m_is_stale": 1.0,
                "m_feed_freshness_ms": None,
                "m_ret_1m": None,
                "m_ret_5m": None,
                "m_ret_30m": None,
                "m_bench_ret_30m": None,
                "m_realized_vol_bps": None,
                "m_spread_bps": None,
                "m_top_depth_log1p": None,
                "m_depth_imbalance": None,
                "m_microprice_offset_bps": None,
                "m_dir_bullish": 0.0,
                "m_dir_bearish": 0.0,
                "m_vol_elevated": 0.0,
                "m_liq_thin": 0.0,
                "m_session_open": 1.0 if in_sess else 0.0,
                "m_session_progress": session_prog,
                "m_is_first_30m": first_30,
                "m_is_last_30m": last_30,
                "m_futures_basis_bps": basis_bps,
                "m_futures_basis_missing": 0.0 if basis_bps is not None else 1.0,
                "m_days_to_expiry": days_exp,
            }

        top_depth = market_regime.top_of_book_total_depth
        return {
            "m_available": 1.0,
            "m_is_stale": 1.0 if market_regime.is_stale else 0.0,
            "m_feed_freshness_ms": market_regime.feed_freshness_ms,
            "m_ret_1m": market_regime.recent_return_1m,
            "m_ret_5m": market_regime.recent_return_5m,
            "m_ret_30m": market_regime.recent_return_30m,
            "m_bench_ret_30m": market_regime.benchmark_recent_return_30m,
            "m_realized_vol_bps": market_regime.realized_volatility_bps,
            "m_spread_bps": market_regime.bid_ask_spread_bps,
            "m_top_depth_log1p": round(math.log1p(max(0, top_depth)), 6) if top_depth is not None else None,
            "m_depth_imbalance": market_regime.depth_imbalance,
            "m_microprice_offset_bps": market_regime.microprice_offset_bps,
            "m_dir_bullish": 1.0 if market_regime.market_direction == MarketDirection.BULLISH else 0.0,
            "m_dir_bearish": 1.0 if market_regime.market_direction == MarketDirection.BEARISH else 0.0,
            "m_vol_elevated": 1.0 if market_regime.volatility_regime == VolatilityRegime.ELEVATED else 0.0,
            "m_liq_thin": 1.0 if market_regime.liquidity_regime in (LiquidityRegime.THIN, LiquidityRegime.CLOSED_OR_HALTED) else 0.0,
            "m_session_open": 1.0 if (in_sess or market_regime.session_state == MarketSessionStatus.REGULAR_OPEN) else 0.0,
            "m_session_progress": session_prog,
            "m_is_first_30m": first_30,
            "m_is_last_30m": last_30,
            "m_futures_basis_bps": basis_bps,
            "m_futures_basis_missing": 0.0 if basis_bps is not None else 1.0,
            "m_days_to_expiry": days_exp,
        }

    def build_row(
        self,
        rep: FinancialEventRepresentation,
        hist_rep: HistoricalIntelligenceRepresentation,
        futures_meta: Optional[FuturesBasisMetadata] = None,
        is_revision: bool = False,
        content_hash: Optional[str] = None,
        is_synthetic: bool = False,
    ) -> PointInTimeFeatureRow:
        """Build a verified PointInTimeFeatureRow strictly bound to `rep.decision_timestamp`."""
        validated_hist = assert_valid_phase4_training_input(hist_rep)
        if rep.event_id != validated_hist.query_event_id:
            raise ValueError(
                f"Mismatched event_id between Phase 3 rep ({rep.event_id}) and Phase 4 hist_rep ({validated_hist.query_event_id})"
            )
        if rep.decision_timestamp != validated_hist.decision_timestamp:
            raise ValueError("Mismatched decision_timestamp between E_t and H_t representations.")

        vals: Dict[str, Optional[float]] = {}
        vals.update(self._extract_group_e(rep, is_revision=is_revision))
        vals.update(self._extract_group_h(validated_hist))
        vals.update(
            self._extract_group_m(
                decision_timestamp=rep.decision_timestamp,
                jurisdiction=validated_hist.jurisdiction,
                market_regime=validated_hist.market_regime,
                futures_meta=futures_meta,
            )
        )

        missing = [k for k, v in vals.items() if v is None]
        inst_id = validated_hist.primary_instrument_id or (
            rep.all_instrument_mappings[0].instrument_id if rep.all_instrument_mappings else "UNMAPPED"
        )

        return PointInTimeFeatureRow(
            event_id=rep.event_id,
            instrument_id=inst_id,
            jurisdiction=validated_hist.jurisdiction,
            decision_timestamp=rep.decision_timestamp,
            horizon_label=self.horizon.value,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            is_synthetic=is_synthetic or validated_hist.provenance.is_synthetic,
            is_revision=is_revision,
            content_hash=content_hash,
            primary_issuer_id=rep.primary_entity.entity_id,
            values=vals,
            missing_features=missing,
            provenance={
                "nlp_pipeline_version": rep.pipeline_version,
                "hist_pipeline_version": validated_hist.provenance.pipeline_version,
                "retrieval_model_version": validated_hist.retrieval_model_version,
                "embedding_model_version": validated_hist.embedding_model_version,
                "decision_timestamp": rep.decision_timestamp.isoformat(),
                "historical_context_source": validated_hist.historical_context_source,
            },
        )
