"""Live-vs-Replay Deterministic Parity Verifier (Phase 6 WP-J).

Verifies that replaying captured real/simulated streams from durable `LiveCaptureStore`
storage through an isolated fresh `LiveResearchPipeline` produces identical:
- Decision timestamps (`local_received_at_utc`) & revision lineage
- Phase 3 NLP representations (`E_t`)
- Phase 4 Historical Intelligence & Market Regime (`H_t`, `M_t`)
- Phase 5 feature vectors (`87`-feature contract)
- Calibrated predictions / `NO_TRADE` decisions

Any discrepancy is surfaced with exact field-level provenance rather than silently ignored.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from pydantic import BaseModel, Field

from backend.alpha.event_embeddings import EventEmbeddingEngine
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.live_data_capture import LiveCaptureStore
from backend.alpha.live_instruments import LiveInstrumentUniverse
from backend.alpha.live_research_pipeline import LiveResearchPipeline, LoadedShadowModel
from backend.alpha.market_data import FeedIntegrityMonitor
from backend.alpha.outcomes import OutcomeHorizon
from backend.alpha.schemas import MarketEvent, NewsEvent
from backend.alpha.shadow_ledger import ShadowLedgerEntry, ShadowSignalLedger


class EventParityComparison(BaseModel):
    """Per-event comparison between live pass and isolated persisted-data replay."""

    event_id: str
    revision_number: int
    timestamp_preserved: bool
    nlp_parity: bool
    historical_regime_parity: bool
    feature_vector_max_abs_diff: float
    feature_vector_parity: bool
    prediction_output_parity: bool
    overall_parity_passed: bool
    discrepancies: List[str] = Field(default_factory=list)


class LiveReplayParityReport(BaseModel):
    """Summary report across a replayed capture session."""

    live_events_count: int
    replayed_events_count: int
    passed_count: int
    failed_count: int
    all_parity_passed: bool
    integrity_anomalies_detected: int = 0
    comparisons: List[EventParityComparison] = Field(default_factory=list)


def _compare_entries(
    live_e: ShadowLedgerEntry,
    rep_e: ShadowLedgerEntry,
    tol: float = 1e-9,
) -> EventParityComparison:
    diffs: List[str] = []

    ts_ok = live_e.decision_timestamp_utc == rep_e.decision_timestamp_utc
    if not ts_ok:
        diffs.append(
            f"DECISION_TS_MISMATCH: live={live_e.decision_timestamp_utc.isoformat()} vs replay={rep_e.decision_timestamp_utc.isoformat()}"
        )

    # Compare E_t core semantics
    l_er = live_e.event_representation or {}
    r_er = rep_e.event_representation or {}
    l_sub = (l_er.get("classification") or {}).get("primary_subtype")
    r_sub = (r_er.get("classification") or {}).get("primary_subtype")
    l_ent = (l_er.get("primary_entity") or {}).get("entity_id")
    r_ent = (r_er.get("primary_entity") or {}).get("entity_id")
    l_mat = (l_er.get("materiality") or {}).get("composite_materiality_score")
    r_mat = (r_er.get("materiality") or {}).get("composite_materiality_score")
    nlp_ok = (l_sub == r_sub) and (l_ent == r_ent) and (l_mat == r_mat)
    if not nlp_ok:
        diffs.append(
            f"NLP_E_MISMATCH: subtype=({l_sub},{r_sub}) entity=({l_ent},{r_ent}) mat=({l_mat},{r_mat})"
        )

    # Compare H_t / M_t core summaries
    l_hr = live_e.historical_intelligence_representation or {}
    r_hr = rep_e.historical_intelligence_representation or {}
    l_mc = l_hr.get("matched_event_count")
    r_mc = r_hr.get("matched_event_count")
    l_mr = (live_e.market_regime_representation or {}).get("mid_price")
    r_mr = (rep_e.market_regime_representation or {}).get("mid_price")
    hist_ok = (l_mc == r_mc) and (l_mr == r_mr)
    if not hist_ok:
        diffs.append(f"HIST_REGIME_MISMATCH: matched=({l_mc},{r_mc}) mid_price=({l_mr},{r_mr})")

    # Compare exact feature vector keys and float tolerances
    all_keys = sorted(set(live_e.feature_values.keys()) | set(rep_e.feature_values.keys()))
    max_diff = 0.0
    feat_diffs: List[str] = []
    for k in all_keys:
        v1 = live_e.feature_values.get(k)
        v2 = rep_e.feature_values.get(k)
        if v1 is None and v2 is None:
            continue
        if (v1 is None) != (v2 is None):
            feat_diffs.append(f"{k}:({v1},{v2})")
            max_diff = max(max_diff, 1.0)
            continue
        d = abs(float(v1) - float(v2))
        max_diff = max(max_diff, d)
        if d > tol:
            feat_diffs.append(f"{k}:diff={d:.3e}")
    feat_ok = len(feat_diffs) == 0
    if not feat_ok:
        diffs.append(f"FEATURE_DIFFS: {','.join(feat_diffs[:5])}")

    # Compare predictions & abstentions
    pred_ok = (
        live_e.action_state == rep_e.action_state
        and live_e.signal_direction == rep_e.signal_direction
        and live_e.no_trade_status == rep_e.no_trade_status
        and live_e.no_trade_reasons == rep_e.no_trade_reasons
    )
    if live_e.expected_return is not None and rep_e.expected_return is not None:
        if abs(live_e.expected_return - rep_e.expected_return) > tol:
            pred_ok = False
    elif live_e.expected_return != rep_e.expected_return:
        pred_ok = False
    if not pred_ok:
        diffs.append(
            f"PRED_MISMATCH: action=({live_e.action_state},{rep_e.action_state}) "
            f"ret=({live_e.expected_return},{rep_e.expected_return})"
        )

    passed = ts_ok and nlp_ok and hist_ok and feat_ok and pred_ok
    return EventParityComparison(
        event_id=live_e.event_id,
        revision_number=live_e.news_revision,
        timestamp_preserved=ts_ok,
        nlp_parity=nlp_ok,
        historical_regime_parity=hist_ok,
        feature_vector_max_abs_diff=round(max_diff, 12),
        feature_vector_parity=feat_ok,
        prediction_output_parity=pred_ok,
        overall_parity_passed=passed,
        discrepancies=diffs,
    )


class LiveReplayParityVerifier:
    """Replays captured disk payloads cleanly in strict chronological order to audit determinism."""

    @staticmethod
    def verify_capture_replay_parity(
        live_capture_store: LiveCaptureStore,
        live_shadow_ledger: ShadowSignalLedger,
        replay_dir: Path,
        horizon: OutcomeHorizon = OutcomeHorizon.D1,
        loaded_model: Optional[LoadedShadowModel] = None,
        embedding_engine: Optional[EventEmbeddingEngine] = None,
        include_synthetic: bool = True,
        as_of_upper_bound: Optional[datetime] = None,
    ) -> LiveReplayParityReport:
        """
        Reconstruct chronological news revisions + market events from `live_capture_store`,
        replay them through a brand-new `LiveResearchPipeline` inside `replay_dir`, and compare
        every input representation, feature vector, and prediction against `live_shadow_ledger`.
        """
        live_entries = live_shadow_ledger.list_predictions(include_synthetic=include_synthetic)
        if not live_entries:
            return LiveReplayParityReport(
                live_events_count=0,
                replayed_events_count=0,
                passed_count=0,
                failed_count=0,
                all_parity_passed=True,
            )

        max_ts = as_of_upper_bound or max(e.decision_timestamp_utc for e in live_entries)

        # Extract all news revisions chronologically up to max_ts from persisted capture DB
        syn_clause = "" if include_synthetic else " AND is_synthetic = 0"
        with live_capture_store._connect() as conn:
            rev_rows = conn.execute(
                f"""
                SELECT payload_json, published_ts_interpretation, is_synthetic
                FROM live_news_revisions
                WHERE local_received_at_utc <= ?{syn_clause}
                ORDER BY local_received_at_utc ASC, revision_number ASC
                """,
                (max_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),),
            ).fetchall()

            mkt_rows = conn.execute(
                f"""
                SELECT event_json, is_synthetic
                FROM live_market_snapshots
                WHERE local_received_at_utc <= ?{syn_clause}
                ORDER BY local_received_at_utc ASC, id ASC
                """,
                (max_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),),
            ).fetchall()

        news_seq: List[tuple[NewsEvent, str, bool]] = [
            (NewsEvent.model_validate_json(r[0]), str(r[1]), bool(r[2])) for r in rev_rows
        ]
        mkt_seq: List[tuple[MarketEvent, bool]] = [
            (MarketEvent.model_validate_json(r[0]), bool(r[1])) for r in mkt_rows
        ]

        # Build clean isolated replay pipeline
        replay_dir.mkdir(parents=True, exist_ok=True)
        rep_cap = LiveCaptureStore(replay_dir / "replay_cap.db", replay_dir / "replay_mkt")
        rep_hist = PersistentHistoricalEventStore(replay_dir / "replay_hist.db")
        rep_ledger = ShadowSignalLedger(replay_dir / "replay_ledger.db")
        try:
            rep_pipe = LiveResearchPipeline(
                capture_store=rep_cap,
                historical_store=rep_hist,
                shadow_ledger=rep_ledger,
                horizon=horizon,
                embedding_engine=embedding_engine or EventEmbeddingEngine(prefer_onnx=False),
                loaded_model=loaded_model,
            )

            # Merge chronologically: market event first when equal timestamps (matching DeterministicReplayEngine!)
            monitor = FeedIntegrityMonitor()
            anomalies = 0
            combined: List[tuple[datetime, int, int, str, Any]] = []
            seq = 0
            for mev, is_syn in mkt_seq:
                combined.append((mev.local_received_at_utc, 0, seq, "MKT", (mev, is_syn)))
                seq += 1
            for nev, interp, is_syn in news_seq:
                combined.append((nev.local_received_at_utc, 1, seq, "NEWS", (nev, interp, is_syn)))
                seq += 1
            combined.sort(key=lambda x: (x[0], x[1], x[2]))

            for _, _, _, kind, payload in combined:
                if kind == "MKT":
                    mev, is_syn = payload
                    valid, anom = monitor.inspect_packet(
                        mev.instrument_id, mev.sequence_number, mev.exchange_timestamp_utc
                    )
                    if not valid and anom is not None:
                        anomalies += 1
                        rep_cap.record_anomaly(mev.provider, anom)
                    rep_pipe.ingest_market_event(mev, persist=True, is_synthetic=is_syn)
                else:
                    nev, interp, is_syn = payload
                    rep_pipe.process_live_news_event(
                        nev,
                        published_ts_interpretation=interp,
                        include_synthetic_history=False,
                        is_synthetic=is_syn,
                    )

            rep_entries = rep_ledger.list_predictions(include_synthetic=include_synthetic)
            rep_by_key: Dict[tuple[str, int], ShadowLedgerEntry] = {
                (e.event_id, e.news_revision): e for e in rep_entries
            }

            comparisons: List[EventParityComparison] = []
            passed_c = 0
            for le in live_entries:
                key = (le.event_id, le.news_revision)
                re_match = rep_by_key.get(key)
                if re_match is None:
                    comparisons.append(
                        EventParityComparison(
                            event_id=le.event_id,
                            revision_number=le.news_revision,
                            timestamp_preserved=False,
                            nlp_parity=False,
                            historical_regime_parity=False,
                            feature_vector_max_abs_diff=1.0,
                            feature_vector_parity=False,
                            prediction_output_parity=False,
                            overall_parity_passed=False,
                            discrepancies=[f"MISSING_IN_REPLAY: key={key}"],
                        )
                    )
                    continue
                comp = _compare_entries(le, re_match)
                if comp.overall_parity_passed:
                    passed_c += 1
                comparisons.append(comp)

            return LiveReplayParityReport(
                live_events_count=len(live_entries),
                replayed_events_count=len(rep_entries),
                passed_count=passed_c,
                failed_count=len(live_entries) - passed_c,
                all_parity_passed=(passed_c == len(live_entries)),
                integrity_anomalies_detected=anomalies,
                comparisons=comparisons,
            )
        finally:
            if hasattr(rep_hist, "close"):
                rep_hist.close()
