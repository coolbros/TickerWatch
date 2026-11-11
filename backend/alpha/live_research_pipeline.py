"""Live Event-to-Market Causal Research Pipeline (Phase 6 WP-E).

Connects live news & market streams end-to-end:
  Live NewsEvent
    -> LiveCaptureStore (immutable revision snapshot)
    -> Phase 3 FinancialNLPPipeline (E_t)
    -> Phase 4 HistoricalIntelligencePipeline (H_t + live M_t strictly <= decision_ts)
    -> Phase 5 PointInTimeFeatureBuilder (87-feature manifest)
    -> Model eligibility verification (MODEL_ELIGIBLE vs MODEL_UNVALIDATED / SYNTHETIC_DIAGNOSTIC_ONLY)
    -> Inference + UncertaintyCalibratorAndGate (calibrated forecast OR NO_TRADE)
    -> Immutable ShadowSignalLedger
    -> Subsequent matured market outcome attachment

All processing stage durations are measured with `time.perf_counter_ns()` and aggregated
into observed `p50 / p95 / p99` operational telemetry (never isolated microbenchmark claims).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Sequence
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.event_embeddings import EventEmbeddingEngine, get_embedding_engine
from backend.alpha.feature_registry import ModelConfiguration, build_feature_manifest
from backend.alpha.historical_intelligence import (
    HistoricalIntelligencePipeline,
    HistoricalIntelligenceRepresentation,
)
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.live_data_capture import LiveCaptureStore
from backend.alpha.live_instruments import INSTRUMENT_MASTER_VERSION, LiveInstrumentUniverse
from backend.alpha.model_registry import LocalModelRegistry, ModelArtifactRecord
from backend.alpha.nlp_pipeline import FinancialEventRepresentation, FinancialNLPPipeline, get_nlp_pipeline
from backend.alpha.outcomes import OutcomeHorizon, OutcomeLabelType
from backend.alpha.predictive_features import PointInTimeFeatureBuilder, PointInTimeFeatureRow
from backend.alpha.predictive_models import TabularEventPredictor
from backend.alpha.predictive_targets import CostAssumptionSpec
from backend.alpha.prediction_uncertainty import CalibrationMethod, UncertaintyCalibratorAndGate
from backend.alpha.schemas import MarketEvent, NewsEvent
from backend.alpha.shadow_ledger import (
    ModelValidationStatus,
    ShadowLedgerEntry,
    ShadowOutcomeRecord,
    ShadowSignalLedger,
)
from backend.alpha.timestamps import get_monotonic_ns


class LatencyPercentiles(BaseModel):
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


class PipelineLatencySummary(BaseModel):
    """Observed empirical processing latencies across pipeline stages."""

    events_processed: int
    stages: Dict[str, LatencyPercentiles] = Field(default_factory=dict)


class LoadedShadowModel(BaseModel):
    """Governance metadata + eligibility status for a loaded Phase 5 model."""

    model_config = {"arbitrary_types_allowed": True}

    model_id: str
    model_version: str
    training_dataset_hash: str
    model_configuration: ModelConfiguration
    validation_status: ModelValidationStatus
    predictor: Optional[TabularEventPredictor] = None
    calibrator: Optional[UncertaintyCalibratorAndGate] = None
    status_reason: str = ""

    @classmethod
    def from_registry(
        cls,
        registry: LocalModelRegistry,
        model_id: str,
        approved_for_real_shadow: bool = False,
        allow_diagnostic_synthetic: bool = False,
    ) -> "LoadedShadowModel":
        rec, predictor, calibrator = registry.load_bundle(model_id)
        cfg = ModelConfiguration(rec.model_config_name)
        if rec.is_synthetic:
            status = ModelValidationStatus.SYNTHETIC_DIAGNOSTIC_ONLY
            reason = (
                "Synthetic-trained model enabled strictly in diagnostic plumbing mode; NOT real alpha."
                if allow_diagnostic_synthetic
                else "Synthetic-trained model quarantined (diagnostic execution disabled)."
            )
            return cls(
                model_id=rec.model_id,
                model_version=rec.model_version,
                training_dataset_hash=rec.dataset_manifest_hash,
                model_configuration=cfg,
                validation_status=status,
                predictor=predictor if allow_diagnostic_synthetic else None,
                calibrator=calibrator if allow_diagnostic_synthetic else None,
                status_reason=reason,
            )

        if rec.leakage_audit_passed and approved_for_real_shadow:
            return cls(
                model_id=rec.model_id,
                model_version=rec.model_version,
                training_dataset_hash=rec.dataset_manifest_hash,
                model_configuration=cfg,
                validation_status=ModelValidationStatus.MODEL_ELIGIBLE,
                predictor=predictor,
                calibrator=calibrator,
                status_reason="Real-data model verified eligible.",
            )

        return cls(
            model_id=rec.model_id,
            model_version=rec.model_version,
            training_dataset_hash=rec.dataset_manifest_hash,
            model_configuration=cfg,
            validation_status=ModelValidationStatus.MODEL_UNVALIDATED,
            predictor=predictor,
            calibrator=calibrator,
            status_reason="Model lacks approved real-data out-of-sample sign-off; outputs are unvalidated diagnostics.",
        )

    @classmethod
    def none_available(cls, config: ModelConfiguration = ModelConfiguration.MODEL_D_FULL_SYSTEM) -> "LoadedShadowModel":
        return cls(
            model_id="NO_ELIGIBLE_MODEL",
            model_version="0.0.0",
            training_dataset_hash="NONE",
            model_configuration=config,
            validation_status=ModelValidationStatus.NO_ELIGIBLE_MODEL,
            predictor=None,
            calibrator=None,
            status_reason="No eligible real-data predictive model configured; collecting feature snapshots and outcomes only.",
        )


class LivePipelineExecutionResult(BaseModel):
    """Full structured output of one live event-to-market evaluation cycle."""

    event_id: str
    decision_timestamp_utc: datetime
    instrument_id: str
    capture_status: str
    feature_row: PointInTimeFeatureRow
    ledger_entry: ShadowLedgerEntry
    latency_ms: Dict[str, float]


class LiveResearchPipeline:
    """
    Operational causal pipeline wiring Phase 2 capture -> Phase 3 NLP -> Phase 4 H_t/M_t
    -> Phase 5 features/model/calibration -> Phase 6 immutable ShadowSignalLedger.
    """

    def __init__(
        self,
        capture_store: LiveCaptureStore,
        historical_store: PersistentHistoricalEventStore,
        shadow_ledger: ShadowSignalLedger,
        horizon: OutcomeHorizon = OutcomeHorizon.D1,
        universe: Optional[LiveInstrumentUniverse] = None,
        nlp_pipeline: Optional[FinancialNLPPipeline] = None,
        embedding_engine: Optional[EventEmbeddingEngine] = None,
        loaded_model: Optional[LoadedShadowModel] = None,
        max_market_buffer_per_instrument: int = 5000,
    ) -> None:
        self.capture_store = capture_store
        self.historical_store = historical_store
        self.shadow_ledger = shadow_ledger
        self.horizon = horizon
        self.universe = universe or LiveInstrumentUniverse()
        self.nlp_pipeline = nlp_pipeline or get_nlp_pipeline()
        self.embedding_engine = embedding_engine or get_embedding_engine()
        self.hist_pipeline = HistoricalIntelligencePipeline(
            store=self.historical_store,
            embedding_engine=self.embedding_engine,
            nlp_pipeline=self.nlp_pipeline,
        )
        self.feature_builder = PointInTimeFeatureBuilder(horizon=horizon)
        self.loaded_model = loaded_model or LoadedShadowModel.none_available()
        self.max_market_buffer = max_market_buffer_per_instrument
        self._market_buffers: Dict[str, Deque[MarketEvent]] = defaultdict(
            lambda: deque(maxlen=self.max_market_buffer)
        )
        self._stage_latencies_ms: Dict[str, List[float]] = defaultdict(list)

    def ingest_market_event(
        self,
        market_event: MarketEvent,
        persist: bool = True,
        is_synthetic: bool = False,
    ) -> None:
        """Buffer and optionally persist an incoming live MarketEvent."""
        self._market_buffers[market_event.instrument_id].append(market_event)
        if persist:
            self.capture_store.record_market_event(
                market_event,
                is_synthetic=is_synthetic,
                instrument_master_version=self.universe.version,
            )

    def _collect_as_of_market_events(
        self,
        instrument_id: Optional[str],
        decision_ts: datetime,
    ) -> List[MarketEvent]:
        events: List[MarketEvent] = []
        target_keys = [instrument_id] if instrument_id else list(self._market_buffers.keys())
        # Also include NSE:NIFTY_FUT:CURRENT contextual ticks when available
        if "NSE:NIFTY_FUT:CURRENT" not in target_keys and "NSE:NIFTY_FUT:CURRENT" in self._market_buffers:
            target_keys.append("NSE:NIFTY_FUT:CURRENT")

        for key in target_keys:
            if not key:
                continue
            for ev in self._market_buffers.get(key, ()):
                if ev.local_received_at_utc <= decision_ts:
                    events.append(ev)

        if not events:
            # Fallback read from persistent store strictly <= decision_ts
            snap = self.capture_store.reconstruct_state_as_of(
                decision_timestamp=decision_ts,
                instrument_id=instrument_id,
                include_synthetic=True,
            )
            events = [e for e in snap.market_events if e.local_received_at_utc <= decision_ts]
        return events

    def process_live_news_event(
        self,
        news_event: NewsEvent,
        published_ts_interpretation: str = "EXPLICIT_OR_LOCALIZED_UTC",
        persist_to_historical_store: bool = True,
        include_synthetic_history: bool = False,
        is_synthetic: bool = False,
        cost_spec: Optional[CostAssumptionSpec] = None,
    ) -> LivePipelineExecutionResult:
        """
        Execute full causal event-to-shadow-prediction evaluation for one `NewsEvent`.
        `decision_timestamp` is strictly bound to `news_event.local_received_at_utc`.
        """
        t_start_ns = get_monotonic_ns()
        queue_delay_ms = max(0.0, (t_start_ns - int(news_event.local_monotonic_ns)) / 1_000_000.0)
        decision_ts = news_event.local_received_at_utc

        # 1. Durable raw capture
        capture_status, rev_num = self.capture_store.record_news_event(
            news_event,
            published_ts_interpretation=published_ts_interpretation,
            is_synthetic=is_synthetic,
            instrument_master_version=self.universe.version,
        )

        # 2. Phase 3 Financial NLP (E_t)
        t_nlp0 = get_monotonic_ns()
        rep: FinancialEventRepresentation = self.nlp_pipeline.process_news_event(news_event)
        # Enhance with Phase 6 clean universe mappings if resolved entity exists
        if rep.primary_entity.entity_id:
            sep = self.universe.map_event_separated(
                rep.primary_entity.entity_id,
                rep.classification.primary_family.value,
                tickers=[news_event.entity_ids[0]] if news_event.entity_ids else None,
            )
            existing_keys = {(m.instrument_id, m.relevance.value) for m in rep.all_instrument_mappings}
            for m in sep.all_mappings():
                if (m.instrument_id, m.relevance.value) not in existing_keys:
                    rep.all_instrument_mappings.append(m)
                    existing_keys.add((m.instrument_id, m.relevance.value))
        t_nlp1 = get_monotonic_ns()
        news_to_nlp_ms = (t_nlp1 - t_nlp0) / 1_000_000.0

        primary_inst = (
            rep.all_instrument_mappings[0].instrument_id
            if rep.all_instrument_mappings
            else "NSE:NIFTY_FUT:CURRENT"
        )
        as_of_market = self._collect_as_of_market_events(primary_inst, decision_ts)

        # 3. Phase 4 Historical Intelligence & Market Regime (H_t, M_t) strictly BEFORE query insert
        t_hist0 = get_monotonic_ns()
        hist_rep: HistoricalIntelligenceRepresentation = self.hist_pipeline.process_event_representation(
            rep=rep,
            market_events=as_of_market,
            include_synthetic=include_synthetic_history,
            index_event_after_query=persist_to_historical_store,
            is_synthetic=is_synthetic,
            horizons=[self.horizon],
        )
        t_hist1 = get_monotonic_ns()
        nlp_to_hist_ms = (t_hist1 - t_hist0) / 1_000_000.0

        # 4. Phase 5 Point-in-Time Features
        t_feat0 = get_monotonic_ns()
        feat_row = self.feature_builder.build_row(
            rep=rep,
            hist_rep=hist_rep,
            is_revision=news_event.is_correction,
            content_hash=news_event.content_hash,
            is_synthetic=is_synthetic,
        )
        feat_row.provenance["primary_family"] = rep.classification.primary_family.value
        feat_row.provenance["primary_subtype"] = rep.classification.primary_subtype.value
        t_feat1 = get_monotonic_ns()
        feature_build_ms = (t_feat1 - t_feat0) / 1_000_000.0

        # 5. Model Eligibility & Calibrated Inference / Abstention
        t_inf0 = get_monotonic_ns()
        active_cfg = self.loaded_model.model_configuration
        manifest = build_feature_manifest(active_cfg)
        subset_vals = feat_row.subset(active_cfg)

        exp_ret: Optional[float] = None
        med_ret: Optional[float] = None
        dir_probs: Dict[str, Optional[float]] = {}
        quantiles: Dict[str, Optional[float]] = {}
        pi80 = None
        pi50 = None
        cal_meta: Dict[str, object] = {"method": "NONE"}
        uncert_meta: Dict[str, object] = {
            "nlp_composite_confidence": rep.composite_confidence,
            "hist_evidence_status": hist_rep.confidence_and_uncertainty.historical_evidence_status.value,
            "market_available": hist_rep.confidence_and_uncertainty.market_regime_available,
        }
        action_state = "NO_TRADE"
        signal_dir = 0
        abstain = True
        reasons: List[str] = []

        eff_cost = cost_spec or CostAssumptionSpec.indian_nifty_fut_conservative()
        spread_bps = feat_row.values.get("m_spread_bps")
        rt_cost_bps = eff_cost.total_round_trip_cost_bps(spread_bps)

        if len(feat_row.missing_features) > int(0.65 * len(feat_row.values)):
            reasons.append("EXCESSIVE_MISSING_FEATURES")

        if self.loaded_model.predictor is None:
            reasons.append(f"MODEL_POLICY:{self.loaded_model.validation_status.value}")
        else:
            try:
                raw_preds = self.loaded_model.predictor.predict_raw(
                    [subset_vals],
                    instrument_ids=[feat_row.instrument_id],
                    subtypes=[rep.classification.primary_subtype.value],
                )
                cal = self.loaded_model.calibrator or UncertaintyCalibratorAndGate(
                    method=CalibrationMethod.NONE,
                    model_config=active_cfg,
                )
                cal_preds = cal.calibrate_and_gate(
                    event_ids=[rep.event_id],
                    instrument_ids=[feat_row.instrument_id],
                    raw_preds=raw_preds,
                    feature_dicts=[subset_vals],
                    round_trip_cost_bps_list=[rt_cost_bps],
                )
                cp = cal_preds[0]
                exp_ret = cp.expected_return
                med_ret = cp.median_return
                dir_probs = {
                    "p_positive": cp.p_positive,
                    "p_negative": cp.p_negative,
                    "p_material_move": cp.p_material_move,
                }
                quantiles = {
                    "q10": cp.q10,
                    "q25": cp.q25,
                    "q50": cp.q50,
                    "q75": cp.q75,
                    "q90": cp.q90,
                }
                pi80 = (cp.q10, cp.q90)
                pi50 = (cp.q25, cp.q75)
                cal_meta = {
                    "method": cal.method.value,
                    "is_calibrated": cal.is_calibrated,
                    "conformal_q_pad": getattr(cal, "_conformal_q_pad", 0.0),
                    "estimated_round_trip_cost_bps": rt_cost_bps,
                }
                uncert_meta["predictive_std"] = cp.predictive_std
                uncert_meta["net_expected_edge_bps"] = cp.net_expected_edge_bps

                reasons.extend(r.value for r in cp.abstain_reasons)
                # Quarantine non-eligible models: never emit actionable shadow direction without approved model
                if self.loaded_model.validation_status != ModelValidationStatus.MODEL_ELIGIBLE:
                    reasons.append(f"MODEL_NOT_ELIGIBLE:{self.loaded_model.validation_status.value}")
                    action_state = "NO_TRADE"
                    signal_dir = 0
                    abstain = True
                else:
                    action_state = cp.action_state
                    signal_dir = cp.signal_direction
                    abstain = cp.abstain
            except Exception as inf_exc:
                reasons.append(f"INFERENCE_OR_CALIBRATION_FAILURE:{type(inf_exc).__name__}")
                action_state = "NO_TRADE"
                signal_dir = 0
                abstain = True

        t_inf1 = get_monotonic_ns()
        inference_ms = (t_inf1 - t_inf0) / 1_000_000.0

        # Quality flags collected cleanly
        q_flags = list(hist_rep.data_quality_flags)
        if not as_of_market:
            q_flags.append("MISSING_LIVE_MARKET_STATE")
        if is_synthetic:
            q_flags.append("SYNTHETIC_EVENT_FIXTURE")

        # 6. Persist Immutable Shadow Ledger Entry
        t_pers0 = get_monotonic_ns()
        raw_entry = ShadowLedgerEntry(
            is_synthetic=is_synthetic,
            event_id=rep.event_id,
            instrument_id=feat_row.instrument_id,
            news_source=news_event.provider_id,
            news_revision=rev_num,
            decision_timestamp_utc=decision_ts,
            event_representation=rep.model_dump(mode="json"),
            historical_intelligence_representation=hist_rep.model_dump(mode="json"),
            market_regime_representation=(
                hist_rep.market_regime.model_dump(mode="json") if hist_rep.market_regime else {}
            ),
            feature_values=feat_row.values,
            feature_manifest_version=manifest.schema_version,
            feature_manifest_sha256=manifest.manifest_sha256,
            input_data_quality_flags=q_flags,
            model_id=self.loaded_model.model_id,
            model_version=self.loaded_model.model_version,
            model_training_dataset_hash=self.loaded_model.training_dataset_hash,
            model_validation_status=self.loaded_model.validation_status,
            forecast_horizon=self.horizon.value,
            expected_return=exp_ret,
            median_return=med_ret,
            predicted_direction_probabilities=dir_probs,
            predictive_quantiles=quantiles,
            prediction_interval_80pct=pi80,
            prediction_interval_50pct=pi50,
            calibration_metadata=cal_meta,
            confidence_and_uncertainty=uncert_meta,
            action_state=action_state,
            signal_direction=signal_dir,
            no_trade_status=abstain,
            no_trade_reasons=list(dict.fromkeys(reasons)),
        )
        t_pers1 = get_monotonic_ns()
        persistence_est_ms = (t_pers1 - t_pers0) / 1_000_000.0
        total_ms = (t_pers1 - t_start_ns) / 1_000_000.0

        lat_dict = {
            "queue_delay_ms": round(queue_delay_ms, 4),
            "news_to_nlp_ms": round(news_to_nlp_ms, 4),
            "nlp_to_hist_ms": round(nlp_to_hist_ms, 4),
            "feature_build_ms": round(feature_build_ms, 4),
            "inference_ms": round(inference_ms, 4),
            "persistence_ms": round(persistence_est_ms, 4),
            "total_event_to_prediction_ms": round(total_ms, 4),
        }
        raw_entry.latency_breakdown_ms = lat_dict
        sealed_entry = self.shadow_ledger.append_prediction(raw_entry)

        for k, v in lat_dict.items():
            self._stage_latencies_ms[k].append(v)

        return LivePipelineExecutionResult(
            event_id=rep.event_id,
            decision_timestamp_utc=decision_ts,
            instrument_id=feat_row.instrument_id,
            capture_status=capture_status,
            feature_row=feat_row,
            ledger_entry=sealed_entry,
            latency_ms=lat_dict,
        )

    def record_matured_outcome_from_market(
        self,
        prediction_id: str,
        post_decision_market_events: Sequence[MarketEvent],
        exit_timestamp_utc: datetime,
        cost_spec: Optional[CostAssumptionSpec] = None,
        pipeline_latency_ms: float = 50.0,
    ) -> ShadowOutcomeRecord:
        """
        Resolve post-decision entry and horizon exit prices from observed live `MarketEvent`s
        and append the realized outcome separately to `ShadowSignalLedger`.
        """
        pred = self.shadow_ledger.get_prediction(prediction_id)
        dec_ts = pred.decision_timestamp_utc
        entry_cutoff = dec_ts + timedelta(milliseconds=pipeline_latency_ms)

        sorted_mkt = sorted(
            [m for m in post_decision_market_events if m.instrument_id == pred.instrument_id],
            key=lambda m: m.local_received_at_utc,
        )

        # Anchor pre-decision theoretical price (REACTION) and post-receipt price (EXECUTABLE)
        pre_events = [m for m in sorted_mkt if m.local_received_at_utc <= dec_ts]
        exec_entry_events = [m for m in sorted_mkt if entry_cutoff <= m.local_received_at_utc <= exit_timestamp_utc]
        exit_events = [m for m in sorted_mkt if m.local_received_at_utc <= exit_timestamp_utc]

        def _mid_or_ltp(m: Optional[MarketEvent]) -> Optional[float]:
            if m is None:
                return None
            if m.bid_price is not None and m.ask_price is not None and m.ask_price > 0:
                return float((m.bid_price + m.ask_price) / 2)
            return float(m.last_traded_price) if m.last_traded_price is not None else None

        pre_m = pre_events[-1] if pre_events else None
        exec_m = exec_entry_events[0] if exec_entry_events else None
        exit_m = exit_events[-1] if exit_events and exit_events[-1].local_received_at_utc >= dec_ts else None

        pre_p = _mid_or_ltp(pre_m)
        exec_p = _mid_or_ltp(exec_m)
        exit_p = _mid_or_ltp(exit_m)

        matured = exit_m is not None and exec_m is not None and exit_m.local_received_at_utc > exec_m.local_received_at_utc
        actual_ret = ((exit_p - pre_p) / pre_p) if (pre_p and exit_p) else None
        exec_ret = ((exit_p - exec_p) / exec_p) if (matured and exec_p and exit_p) else None

        spread_bps: Optional[float] = None
        if exec_m and exec_m.bid_price is not None and exec_m.ask_price is not None and exec_p:
            spread_bps = float((exec_m.ask_price - exec_m.bid_price) / exec_m.bid_price) * 10000.0 if exec_m.bid_price > 0 else None

        eff_cost = cost_spec or CostAssumptionSpec.indian_nifty_fut_conservative()
        est_cost_bps = eff_cost.total_round_trip_cost_bps(spread_bps)
        net_ret = (
            ((pred.signal_direction if pred.signal_direction != 0 else 1) * exec_ret) - (est_cost_bps / 10000.0)
            if exec_ret is not None
            else None
        )

        q_flags = []
        if not matured:
            q_flags.append("IMMATURE_OR_MISSING_EXIT_BAR")
        if pre_p is None:
            q_flags.append("MISSING_PRE_DECISION_PRICE")

        outcome = ShadowOutcomeRecord(
            prediction_id=prediction_id,
            event_id=pred.event_id,
            instrument_id=pred.instrument_id,
            horizon_label=pred.forecast_horizon,
            label_type=OutcomeLabelType.EXECUTABLE.value,
            entry_timestamp_utc=exec_m.local_received_at_utc if exec_m else None,
            exit_timestamp_utc=exit_m.local_received_at_utc if exit_m else None,
            actual_entry_price=pre_p,
            actual_exit_price=exit_p,
            actual_realized_return=actual_ret,
            executable_entry_price=exec_p,
            executable_exit_price=exit_p,
            executable_realized_return=exec_ret,
            outcome_matured=matured,
            market_data_provenance=exec_m.provider if exec_m else "UNAVAILABLE",
            realized_spread_bps=round(spread_bps, 4) if spread_bps is not None else None,
            estimated_transaction_cost_bps=round(est_cost_bps, 4),
            net_estimated_return=round(net_ret, 8) if net_ret is not None else None,
            data_quality_flags=q_flags,
            is_synthetic=pred.is_synthetic,
        )
        return self.shadow_ledger.append_outcome(outcome)

    def get_latency_summary(self) -> PipelineLatencySummary:
        stages_out: Dict[str, LatencyPercentiles] = {}
        for name, vals in self._stage_latencies_ms.items():
            if not vals:
                continue
            arr = np.asarray(vals, dtype=np.float64)
            stages_out[name] = LatencyPercentiles(
                count=int(arr.shape[0]),
                p50_ms=round(float(np.percentile(arr, 50)), 4),
                p95_ms=round(float(np.percentile(arr, 95)), 4),
                p99_ms=round(float(np.percentile(arr, 99)), 4),
                max_ms=round(float(np.max(arr)), 4),
            )
        total_n = len(self._stage_latencies_ms.get("total_event_to_prediction_ms", []))
        return PipelineLatencySummary(events_processed=total_n, stages=stages_out)
