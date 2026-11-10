"""Retrieval & Event-Study Evaluation and Benchmarking Framework (Phase 4).

Provides:
1. Manually inspectable chronological IN & US benchmark corpora & queries across 5 event strata:
   - Earnings
   - Guidance revisions
   - Contract wins
   - Regulatory actions
   - Macro events
   Strictly guarantees zero duplicate headlines/revisions across historical pool and queries,
   and labels benchmark items explicitly as `is_synthetic=True` curated test cases.
2. Standardized IR ranking metrics: Precision@k, Recall@k, MRR, nDCG@k, Coverage.
3. Comparative ablations across JACCARD, BM25, DENSE_SEMANTIC, and HYBRID_MODULAR
   (testing both ONNX BGE-small-en-v1.5 INT8 and FinConceptProjection encoders).
4. Systems performance metrics: indexing throughput, embedding latency, query p50/p95/p99,
   psutil process RSS RAM, and SQLite WAL storage footprint.
5. Event-Study verification suite covering return math, benchmark adjustment, maturity,
   missing data/resolution limits, session/holiday gaps, and futures rollover policies.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import platform
import tempfile
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import psutil
from pydantic import BaseModel, Field

from backend.alpha.event_embeddings import EventEmbeddingEngine
from backend.alpha.event_retrieval import PersistentVectorRetriever, RetrievalMode
from backend.alpha.event_study import (
    AnchorMode,
    EventStudyEngine,
    FuturesRolloverMethod,
    FuturesRolloverSpec,
    MarketCalendarConfig,
)
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.nlp_pipeline import get_nlp_pipeline
from backend.alpha.outcomes import OutcomeDataQualityStatus, OutcomeHorizon, PriceObservation


class BenchmarkHistoricalItem(BaseModel):
    """Curated chronological historical event item for inspectable IR benchmarking."""

    item_id: str
    jurisdiction: MarketJurisdiction
    stratum: str  # EARNINGS, GUIDANCE_REVISION, CONTRACT_WIN, REGULATORY_ACTION, MACRO_EVENT
    headline: str
    body_snippet: Optional[str] = None
    timestamp_utc: datetime
    sector: str
    relevance_tags: Set[str] = Field(default_factory=set)
    is_synthetic: bool = True


class BenchmarkQueryCase(BaseModel):
    """Manually inspectable query event paired with chronological graded relevance judgments."""

    query_id: str
    jurisdiction: MarketJurisdiction
    stratum: str
    headline: str
    body_snippet: Optional[str] = None
    decision_timestamp: datetime
    sector: str
    # Graded relevance: 2 = directly comparable analogue (same event type + core economic driver),
    # 1 = secondary sector/peer analogue
    graded_relevance: Dict[str, int]
    notes: str = ""
    is_synthetic: bool = True


def _build_in_corpus_and_queries() -> Tuple[List[BenchmarkHistoricalItem], List[BenchmarkQueryCase]]:
    """
    Construct inspectable Indian market benchmark corpus (25 historical events + 10 chronological queries).
    Carefully pairs realistic financial paraphrasing (low exact lexical overlap vs high lexical distractors)
    to honestly test whether semantic embeddings improve over lexical baselines.
    """
    t0 = datetime(2025, 1, 10, 5, 0, tzinfo=timezone.utc)
    hist = [
        # --- CONTRACT_WIN (IN) ---
        BenchmarkHistoricalItem(
            item_id="IN_CW_1", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="Larsen & Toubro secures Rs 3500 crore EPC construction project for Middle East solar grid",
            timestamp_utc=t0 + timedelta(days=1), sector="Infrastructure",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_CW_2", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="L&T wins Rs 4200 crore metro rail turnkey civil engineering mandate in Maharashtra",
            timestamp_utc=t0 + timedelta(days=5), sector="Infrastructure",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_CW_3", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="TCS inks multi-year $450 million cloud modernization agreement with Nordic banking group",
            timestamp_utc=t0 + timedelta(days=9), sector="Information Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_CW_4", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="Infosys obtains $300 million digital transformation engagement from European insurer",
            timestamp_utc=t0 + timedelta(days=12), sector="Information Technology",
        ),
        # Lexical trap: Contract CANCELLATION (opposite economic direction, shares words like contract/L&T)
        BenchmarkHistoricalItem(
            item_id="IN_CW_TRAP_CANCEL", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="Larsen & Toubro faces contract termination on highway corridor dispute worth Rs 900 crore",
            timestamp_utc=t0 + timedelta(days=15), sector="Infrastructure",
        ),

        # --- EARNINGS (IN) ---
        BenchmarkHistoricalItem(
            item_id="IN_EARN_1", jurisdiction=MarketJurisdiction.IN, stratum="EARNINGS",
            headline="TCS reports Q3 consolidated net profit rising 11% YoY to Rs 12,380 crore",
            timestamp_utc=t0 + timedelta(days=20), sector="Information Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_EARN_2", jurisdiction=MarketJurisdiction.IN, stratum="EARNINGS",
            headline="HDFC Bank quarterly bottom-line expands 19% to Rs 16,370 crore on steady credit growth",
            timestamp_utc=t0 + timedelta(days=25), sector="Banking & Financial Services",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_EARN_3", jurisdiction=MarketJurisdiction.IN, stratum="EARNINGS",
            headline="ICICI Bank posts Q4 standalone earnings jump of 17% to Rs 10,708 crore",
            timestamp_utc=t0 + timedelta(days=28), sector="Banking & Financial Services",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_EARN_4", jurisdiction=MarketJurisdiction.IN, stratum="EARNINGS",
            headline="Reliance Industries Q2 net income increases 9% to Rs 17,394 crore",
            timestamp_utc=t0 + timedelta(days=32), sector="Energy & Conglomerate",
        ),

        # --- GUIDANCE_REVISION (IN) ---
        BenchmarkHistoricalItem(
            item_id="IN_GUID_1", jurisdiction=MarketJurisdiction.IN, stratum="GUIDANCE_REVISION",
            headline="Infosys slashes full-year constant currency revenue growth projection to 1.5%-2.0%",
            timestamp_utc=t0 + timedelta(days=40), sector="Information Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_GUID_2", jurisdiction=MarketJurisdiction.IN, stratum="GUIDANCE_REVISION",
            headline="Infosys upgrades FY26 top-line sales outlook range on resilient BFSI demand",
            timestamp_utc=t0 + timedelta(days=45), sector="Information Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_GUID_3", jurisdiction=MarketJurisdiction.IN, stratum="GUIDANCE_REVISION",
            headline="Tata Motors lowers JLR EBIT margin target for current fiscal citing China softness",
            timestamp_utc=t0 + timedelta(days=48), sector="Automotive",
        ),

        # --- REGULATORY_ACTION (IN) ---
        BenchmarkHistoricalItem(
            item_id="IN_REG_1", jurisdiction=MarketJurisdiction.IN, stratum="REGULATORY_ACTION",
            headline="SEBI imposes Rs 25 crore adjudication fine on brokerage promoter for disclosure lapse",
            timestamp_utc=t0 + timedelta(days=55), sector="Financial Services",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_REG_2", jurisdiction=MarketJurisdiction.IN, stratum="REGULATORY_ACTION",
            headline="RBI restricts Kotak Mahindra Bank digital customer onboarding under supervisory directive",
            timestamp_utc=t0 + timedelta(days=60), sector="Banking & Financial Services",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_REG_3", jurisdiction=MarketJurisdiction.IN, stratum="REGULATORY_ACTION",
            headline="Securities regulator SEBI serves show cause notice in insider trading investigation",
            timestamp_utc=t0 + timedelta(days=65), sector="Financial Services",
        ),

        # --- MACRO_EVENT (IN) ---
        BenchmarkHistoricalItem(
            item_id="IN_MAC_1", jurisdiction=MarketJurisdiction.IN, stratum="MACRO_EVENT",
            headline="RBI Monetary Policy Committee keeps benchmark repo rate unchanged at 6.50%",
            timestamp_utc=t0 + timedelta(days=70), sector="Macro",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_MAC_2", jurisdiction=MarketJurisdiction.IN, stratum="MACRO_EVENT",
            headline="India consumer price inflation cools to 4.35% YoY in monthly MOSPI CPI release",
            timestamp_utc=t0 + timedelta(days=75), sector="Macro",
        ),
        BenchmarkHistoricalItem(
            item_id="IN_MAC_3", jurisdiction=MarketJurisdiction.IN, stratum="MACRO_EVENT",
            headline="RBI delivers 25 bps policy rate cut to stimulate domestic investment cycle",
            timestamp_utc=t0 + timedelta(days=80), sector="Macro",
        ),
        # Routine noise distractor
        BenchmarkHistoricalItem(
            item_id="IN_NOISE_1", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="Larsen & Toubro schedules routine annual general meeting notice for shareholders",
            timestamp_utc=t0 + timedelta(days=82), sector="Infrastructure",
        ),
    ]

    q_time = t0 + timedelta(days=120)
    queries = [
        BenchmarkQueryCase(
            query_id="IN_Q_CW_1", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="L&T awarded Rs 2900 crore power substation infrastructure mandate in Oman",
            decision_timestamp=q_time, sector="Infrastructure",
            graded_relevance={"IN_CW_1": 2, "IN_CW_2": 2},
            notes="Paraphrase query ('awarded ... mandate' vs 'secures ... project'); IN_CW_TRAP_CANCEL shares entity & word 'contract' but opposite direction.",
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_CW_2", jurisdiction=MarketJurisdiction.IN, stratum="CONTRACT_WIN",
            headline="TCS selected as technology vendor for $500 million European core banking overhaul",
            decision_timestamp=q_time, sector="Information Technology",
            graded_relevance={"IN_CW_3": 2, "IN_CW_4": 1},
            notes="Low lexical overlap IT mega-deal ('selected as vendor' vs 'inks agreement' / 'obtains engagement').",
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_EARN_1", jurisdiction=MarketJurisdiction.IN, stratum="EARNINGS",
            headline="ICICI Bank logs 16% profit growth to Rs 11,500 crore in March quarter",
            decision_timestamp=q_time, sector="Banking & Financial Services",
            graded_relevance={"IN_EARN_3": 2, "IN_EARN_2": 1},
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_EARN_2", jurisdiction=MarketJurisdiction.IN, stratum="EARNINGS",
            headline="TCS Q4 net earnings grow 9% YoY to Rs 12,500 crore on stable margins",
            decision_timestamp=q_time, sector="Information Technology",
            graded_relevance={"IN_EARN_1": 2},
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_GUID_1", jurisdiction=MarketJurisdiction.IN, stratum="GUIDANCE_REVISION",
            headline="Infosys trims annual revenue guidance band citing delayed discretionary client spend",
            decision_timestamp=q_time, sector="Information Technology",
            graded_relevance={"IN_GUID_1": 2, "IN_GUID_2": 1},
            notes="Synonym test: 'trims annual revenue guidance' vs 'slashes full-year revenue growth projection'.",
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_GUID_2", jurisdiction=MarketJurisdiction.IN, stratum="GUIDANCE_REVISION",
            headline="Tata Motors cuts fiscal operating profitability outlook amid overseas slowdown",
            decision_timestamp=q_time, sector="Automotive",
            graded_relevance={"IN_GUID_3": 2, "IN_GUID_1": 1},
            notes="Severe paraphrase: 'cuts fiscal operating profitability outlook' vs 'lowers EBIT margin target'.",
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_REG_1", jurisdiction=MarketJurisdiction.IN, stratum="REGULATORY_ACTION",
            headline="Kotak Bank hit with central bank enforcement restriction over IT compliance gaps",
            decision_timestamp=q_time, sector="Banking & Financial Services",
            graded_relevance={"IN_REG_2": 2, "IN_REG_1": 1, "IN_REG_3": 1},
            notes="'central bank enforcement restriction' vs 'RBI restricts ... supervisory directive'.",
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_REG_2", jurisdiction=MarketJurisdiction.IN, stratum="REGULATORY_ACTION",
            headline="SEBI slaps monetary penalty for non-compliance and market violation norms",
            decision_timestamp=q_time, sector="Financial Services",
            graded_relevance={"IN_REG_1": 2, "IN_REG_3": 2},
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_MAC_1", jurisdiction=MarketJurisdiction.IN, stratum="MACRO_EVENT",
            headline="RBI MPC holds borrowing benchmark steady with neutral liquidity stance",
            decision_timestamp=q_time, sector="Macro",
            graded_relevance={"IN_MAC_1": 2, "IN_MAC_3": 1},
        ),
        BenchmarkQueryCase(
            query_id="IN_Q_MAC_2", jurisdiction=MarketJurisdiction.IN, stratum="MACRO_EVENT",
            headline="Retail consumer price growth moderates to 4.1% in official inflation print",
            decision_timestamp=q_time, sector="Macro",
            graded_relevance={"IN_MAC_2": 2},
        ),
    ]
    return hist, queries


def _build_us_corpus_and_queries() -> Tuple[List[BenchmarkHistoricalItem], List[BenchmarkQueryCase]]:
    """Construct separate US market benchmark corpus (15 historical events + 6 chronological queries)."""
    t0 = datetime(2025, 1, 10, 14, 30, tzinfo=timezone.utc)
    hist = [
        BenchmarkHistoricalItem(
            item_id="US_EARN_1", jurisdiction=MarketJurisdiction.US, stratum="EARNINGS",
            headline="Apple reports Q1 EPS beat and record $95 billion quarterly iPhone revenue",
            timestamp_utc=t0 + timedelta(days=2), sector="Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="US_EARN_2", jurisdiction=MarketJurisdiction.US, stratum="EARNINGS",
            headline="Microsoft quarterly Azure cloud income surges 28% lifting total net profit",
            timestamp_utc=t0 + timedelta(days=6), sector="Technology & Software",
        ),
        BenchmarkHistoricalItem(
            item_id="US_EARN_3", jurisdiction=MarketJurisdiction.US, stratum="EARNINGS",
            headline="Amazon Q4 AWS operating profit rises 32% above Wall Street forecasts",
            timestamp_utc=t0 + timedelta(days=10), sector="Consumer Discretionary & Cloud",
        ),
        BenchmarkHistoricalItem(
            item_id="US_GUID_1", jurisdiction=MarketJurisdiction.US, stratum="GUIDANCE_REVISION",
            headline="Apple cautions next quarter Services sales growth will decelerate sharply",
            timestamp_utc=t0 + timedelta(days=18), sector="Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="US_GUID_2", jurisdiction=MarketJurisdiction.US, stratum="GUIDANCE_REVISION",
            headline="Microsoft raises full-year commercial capital spending projection for AI datacenters",
            timestamp_utc=t0 + timedelta(days=22), sector="Technology & Software",
        ),
        BenchmarkHistoricalItem(
            item_id="US_CW_1", jurisdiction=MarketJurisdiction.US, stratum="CONTRACT_WIN",
            headline="Amazon Web Services signs $1.2 billion defense cloud procurement contract",
            timestamp_utc=t0 + timedelta(days=30), sector="Consumer Discretionary & Cloud",
        ),
        BenchmarkHistoricalItem(
            item_id="US_CW_2", jurisdiction=MarketJurisdiction.US, stratum="CONTRACT_WIN",
            headline="Microsoft wins $850 million enterprise productivity government award",
            timestamp_utc=t0 + timedelta(days=35), sector="Technology & Software",
        ),
        BenchmarkHistoricalItem(
            item_id="US_REG_1", jurisdiction=MarketJurisdiction.US, stratum="REGULATORY_ACTION",
            headline="DOJ files antitrust enforcement complaint targeting Apple App Store policies",
            timestamp_utc=t0 + timedelta(days=45), sector="Technology",
        ),
        BenchmarkHistoricalItem(
            item_id="US_REG_2", jurisdiction=MarketJurisdiction.US, stratum="REGULATORY_ACTION",
            headline="SEC charges fintech executive over misleading quarterly revenue disclosures",
            timestamp_utc=t0 + timedelta(days=50), sector="Financials",
        ),
        BenchmarkHistoricalItem(
            item_id="US_MAC_1", jurisdiction=MarketJurisdiction.US, stratum="MACRO_EVENT",
            headline="Federal Reserve FOMC lowers federal funds policy rate by 25 basis points",
            timestamp_utc=t0 + timedelta(days=60), sector="Macro",
        ),
        BenchmarkHistoricalItem(
            item_id="US_MAC_2", jurisdiction=MarketJurisdiction.US, stratum="MACRO_EVENT",
            headline="US Bureau of Labor Statistics CPI inflation print comes in cooler than consensus at 2.6%",
            timestamp_utc=t0 + timedelta(days=65), sector="Macro",
        ),
    ]
    q_time = t0 + timedelta(days=100)
    queries = [
        BenchmarkQueryCase(
            query_id="US_Q_EARN_1", jurisdiction=MarketJurisdiction.US, stratum="EARNINGS",
            headline="Microsoft posts strong fiscal quarterly profit powered by enterprise cloud expansion",
            decision_timestamp=q_time, sector="Technology & Software",
            graded_relevance={"US_EARN_2": 2, "US_EARN_3": 1},
        ),
        BenchmarkQueryCase(
            query_id="US_Q_GUID_1", jurisdiction=MarketJurisdiction.US, stratum="GUIDANCE_REVISION",
            headline="Apple trims forward quarter top-line expectation amid softer handset upgrades",
            decision_timestamp=q_time, sector="Technology",
            graded_relevance={"US_GUID_1": 2},
        ),
        BenchmarkQueryCase(
            query_id="US_Q_CW_1", jurisdiction=MarketJurisdiction.US, stratum="CONTRACT_WIN",
            headline="AWS secures $900 million federal agency cloud infrastructure deal",
            decision_timestamp=q_time, sector="Consumer Discretionary & Cloud",
            graded_relevance={"US_CW_1": 2, "US_CW_2": 1},
        ),
        BenchmarkQueryCase(
            query_id="US_Q_REG_1", jurisdiction=MarketJurisdiction.US, stratum="REGULATORY_ACTION",
            headline="US regulators initiate competition antitrust lawsuit examining Apple ecosystem fees",
            decision_timestamp=q_time, sector="Technology",
            graded_relevance={"US_REG_1": 2, "US_REG_2": 1},
        ),
        BenchmarkQueryCase(
            query_id="US_Q_MAC_1", jurisdiction=MarketJurisdiction.US, stratum="MACRO_EVENT",
            headline="Fed cuts benchmark borrowing target range by 25 bps to support employment",
            decision_timestamp=q_time, sector="Macro",
            graded_relevance={"US_MAC_1": 2},
        ),
    ]
    return hist, queries


class ModeBenchmarkMetrics(BaseModel):
    """Aggregate IR and systems metrics for one retrieval configuration."""

    mode_label: str
    retrieval_mode: str
    embedding_model_version: str
    num_queries: int
    top_k: int
    precision_at_k: float
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    retrieval_coverage: float
    query_latency_p50_ms: float
    query_latency_p95_ms: float
    query_latency_p99_ms: float
    by_stratum_mrr: Dict[str, float] = Field(default_factory=dict)


class HistoricalBenchmarkReport(BaseModel):
    """Complete empirical Phase 4 Retrieval & Event-Study Verification Report."""

    generated_at_utc: datetime
    python_version: str
    platform_info: str
    onnx_bge_small_available: bool
    corpus_counts: Dict[str, int]
    split_leakage_duplicates: int
    indexing_throughput_events_per_sec: Dict[str, float]
    embedding_latency_mean_ms: Dict[str, float]
    ram_rss_mb_before: float
    ram_rss_mb_after: float
    sqlite_storage_bytes: Dict[str, int]
    in_market_comparison: List[ModeBenchmarkMetrics]
    us_market_comparison: List[ModeBenchmarkMetrics]
    combined_ablation_summary: Dict[str, Dict[str, float]]
    event_study_checks: Dict[str, bool]
    all_event_study_checks_passed: bool


def _compute_ndcg_at_k(ranked_ids: List[str], rel_map: Dict[str, int], k: int) -> float:
    dcg = 0.0
    for idx, eid in enumerate(ranked_ids[:k]):
        rel = rel_map.get(eid, 0)
        if rel > 0:
            dcg += (2 ** rel - 1) / math.log2(idx + 2)
    ideal_rels = sorted(rel_map.values(), reverse=True)[:k]
    idcg = sum((2 ** rel - 1) / math.log2(idx + 2) for idx, rel in enumerate(ideal_rels) if rel > 0)
    return (dcg / idcg) if idcg > 0 else 0.0


def verify_event_study_suite() -> Dict[str, bool]:
    """Programmatically verify core Event-Study mathematical and causal invariants."""
    checks: Dict[str, bool] = {}
    t0 = datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc)  # Monday 10:30 IST
    cal_in = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
    engine = EventStudyEngine(calendar=cal_in)

    # 1. Exact log/simple return & benchmark adjustment
    obs = [
        PriceObservation(timestamp_utc=t0 + timedelta(minutes=m), price=100.0 + (2.0 if m >= 5 else 0.0))
        for m in range(7)
    ]
    b_obs = [
        PriceObservation(timestamp_utc=t0 + timedelta(minutes=m), price=20000.0 + (100.0 if m >= 5 else 0.0))
        for m in range(7)
    ]
    lbl = engine.compute_outcome_label(
        event_id="chk1", instrument_id="NSE:TCS:EQ", horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0, decision_timestamp_utc=t0,
        observations=obs, benchmark_instrument="NSE:NIFTY_FUT:CURRENT", benchmark_observations=b_obs,
        is_synthetic=True,
    )
    checks["outcome_label_correctness"] = (
        lbl.data_quality_status == OutcomeDataQualityStatus.VERIFIED
        and lbl.simple_return is not None and abs(lbl.simple_return - 0.02) < 1e-5
        and lbl.raw_log_return is not None and abs(lbl.raw_log_return - math.log(1.02)) < 1e-5
    )
    checks["benchmark_adjustment_correctness"] = (
        lbl.benchmark_return is not None and abs(lbl.benchmark_return - 0.005) < 1e-5
        and lbl.benchmark_adjusted_return is not None and abs(lbl.benchmark_adjusted_return - 0.015) < 1e-5
    )

    # 2. Horizon maturity check
    lbl_imm = engine.compute_outcome_label(
        event_id="chk_imm", instrument_id="NSE:TCS:EQ", horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0, decision_timestamp_utc=t0, observations=obs,
        evaluation_decision_ts=t0 + timedelta(minutes=2), is_synthetic=True,
    )
    checks["horizon_maturity_guard"] = (
        lbl_imm.data_quality_status == OutcomeDataQualityStatus.IMMATURE
        and lbl_imm.simple_return is None
        and not lbl_imm.is_matured_at(t0 + timedelta(minutes=2))
    )

    # 3. Missing / Resolution check on daily bars
    d_obs = [
        PriceObservation(timestamp_utc=datetime(2026, 3, d, 10, 0, tzinfo=timezone.utc), price=1000.0 + d)
        for d in range(2, 7)
    ]
    lbl_res = engine.compute_outcome_label(
        event_id="chk_res", instrument_id="NSE:TCS:EQ", horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0, decision_timestamp_utc=t0, observations=d_obs, is_synthetic=True,
    )
    checks["missing_and_resolution_handling"] = (
        lbl_res.data_quality_status == OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION
        and lbl_res.simple_return is None
    )

    # 4. Session & overnight holiday boundary check
    t_fri = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)  # Fri after close
    t_fri_close = datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc)
    t_mon_close = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
    sess_obs = [
        PriceObservation(timestamp_utc=t_fri_close, price=1000.0),
        PriceObservation(timestamp_utc=t_mon_close, price=1040.0),
    ]
    lbl_sess = engine.compute_outcome_label(
        event_id="chk_sess", instrument_id="NSE:TCS:EQ", horizon=OutcomeHorizon.D1,
        event_timestamp_utc=t_fri, decision_timestamp_utc=t_fri, observations=sess_obs, is_synthetic=True,
    )
    checks["session_and_overnight_returns"] = (
        lbl_sess.data_quality_status == OutcomeDataQualityStatus.VERIFIED
        and lbl_sess.overnight_gap_included is True
        and lbl_sess.simple_return is not None and abs(lbl_sess.simple_return - 0.04) < 1e-4
    )

    # 5. Futures rollover rejection + adjustment
    roll_obs = [
        PriceObservation(timestamp_utc=t0, price=22000.0, contract_id="C1"),
        PriceObservation(timestamp_utc=t0 + timedelta(minutes=5), price=22120.0, contract_id="C2"),
    ]
    lbl_bad_roll = engine.compute_outcome_label(
        event_id="chk_r1", instrument_id="NSE:NIFTY_FUT:CURRENT", horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0, decision_timestamp_utc=t0, observations=roll_obs, is_synthetic=True,
    )
    eng_roll = EventStudyEngine(
        calendar=cal_in,
        rollover_specs=[
            FuturesRolloverSpec(
                from_contract_id="C1", to_contract_id="C2",
                method=FuturesRolloverMethod.ADDITIVE_SPREAD_BACK_ADJUST,
                price_spread_to_minus_from=100.0,
            )
        ],
    )
    lbl_good_roll = eng_roll.compute_outcome_label(
        event_id="chk_r2", instrument_id="NSE:NIFTY_FUT:CURRENT", horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0, decision_timestamp_utc=t0, observations=roll_obs, is_synthetic=True,
    )
    checks["futures_rollover_handling"] = (
        lbl_bad_roll.data_quality_status == OutcomeDataQualityStatus.ROLLOVER_INVALID
        and lbl_good_roll.data_quality_status == OutcomeDataQualityStatus.ROLLOVER_ADJUSTED
        and lbl_good_roll.simple_return is not None and abs(lbl_good_roll.simple_return - (20.0 / 22000.0)) < 1e-5
    )

    return checks


def _evaluate_store_queries(
    store: PersistentHistoricalEventStore,
    retriever: PersistentVectorRetriever,
    queries: List[BenchmarkQueryCase],
    mode: RetrievalMode,
    mode_label: str,
    top_k: int = 5,
) -> ModeBenchmarkMetrics:
    nlp = get_nlp_pipeline()
    precisions: List[float] = []
    recalls: List[float] = []
    rrs: List[float] = []
    ndcgs: List[float] = []
    covered = 0
    latencies_ms: List[float] = []
    stratum_rr: Dict[str, List[float]] = {}

    for q in queries:
        rep = nlp.process_raw_text(
            event_id=q.query_id,
            headline=q.headline,
            body_text=q.body_snippet,
            local_received_at=q.decision_timestamp,
        )
        q_entities = [e.entity_id for e in rep.all_entities if e.entity_id]
        q_insts = [m.instrument_id for m in rep.all_instrument_mappings]
        mag = abs(rep.extraction.primary_metric.normalized_value) if (
            rep.extraction.primary_metric and rep.extraction.primary_metric.normalized_value is not None
        ) else None

        t_start = time.perf_counter()
        ctx = retriever.retrieve(
            query_headline=rep.headline,
            decision_timestamp=q.decision_timestamp,
            query_event_id=q.query_id,
            query_body_snippet=rep.body_snippet,
            query_subtype=rep.classification.primary_subtype.value,
            query_family=rep.classification.primary_family.value,
            query_entity_ids=q_entities,
            query_instrument_ids=q_insts,
            query_materiality=rep.materiality.composite_materiality_score,
            query_primary_magnitude=mag,
            query_sector=q.sector,
            jurisdiction=q.jurisdiction,
            top_k=top_k,
            mode=mode,
            include_synthetic=True,
        )
        lat_ms = (time.perf_counter() - t_start) * 1000.0
        latencies_ms.append(lat_ms)

        ranked_ids = [m.event_id for m in ctx.matches]
        rel_ids = {eid for eid, grade in q.graded_relevance.items() if grade > 0}
        hits = [eid for eid in ranked_ids[:top_k] if eid in rel_ids]

        p_k = len(hits) / min(top_k, max(1, len(ranked_ids))) if ranked_ids else 0.0
        r_k = len(hits) / max(1, len(rel_ids))
        rr = 0.0
        for rank_idx, eid in enumerate(ranked_ids, start=1):
            if eid in rel_ids:
                rr = 1.0 / rank_idx
                break
        ndcg = _compute_ndcg_at_k(ranked_ids, q.graded_relevance, top_k)

        precisions.append(p_k)
        recalls.append(r_k)
        rrs.append(rr)
        ndcgs.append(ndcg)
        if ranked_ids and ctx.matches[0].similarity_score > 0.0:
            covered += 1
        stratum_rr.setdefault(q.stratum, []).append(rr)

    lat_sorted = sorted(latencies_ms)
    n = len(lat_sorted)
    p50 = lat_sorted[int(0.50 * (n - 1))] if n else 0.0
    p95 = lat_sorted[int(0.95 * (n - 1))] if n else 0.0
    p99 = lat_sorted[int(0.99 * (n - 1))] if n else 0.0

    return ModeBenchmarkMetrics(
        mode_label=mode_label,
        retrieval_mode=mode.value,
        embedding_model_version=retriever.embedding_engine.active_model_version,
        num_queries=len(queries),
        top_k=top_k,
        precision_at_k=round(sum(precisions) / max(1, len(precisions)), 4),
        recall_at_k=round(sum(recalls) / max(1, len(recalls)), 4),
        mrr=round(sum(rrs) / max(1, len(rrs)), 4),
        ndcg_at_k=round(sum(ndcgs) / max(1, len(ndcgs)), 4),
        retrieval_coverage=round(covered / max(1, len(queries)), 4),
        query_latency_p50_ms=round(p50, 3),
        query_latency_p95_ms=round(p95, 3),
        query_latency_p99_ms=round(p99, 3),
        by_stratum_mrr={s: round(sum(v) / len(v), 4) for s, v in sorted(stratum_rr.items())},
    )


def run_historical_benchmark(top_k: int = 5) -> HistoricalBenchmarkReport:
    """Execute complete Phase 4 empirical benchmark across both IN & US datasets and all retrieval modes."""
    proc = psutil.Process(os.getpid())
    ram_before = round(proc.memory_info().rss / (1024 ** 2), 2)

    nlp = get_nlp_pipeline()
    in_hist, in_queries = _build_in_corpus_and_queries()
    us_hist, us_queries = _build_us_corpus_and_queries()

    # Verify zero duplicate or revision leakage across historical items and queries
    hist_hashes = {
        hashlib.sha256(h.headline.strip().lower().encode("utf-8")).hexdigest()
        for h in (in_hist + us_hist)
    }
    query_hashes = {
        hashlib.sha256(q.headline.strip().lower().encode("utf-8")).hexdigest()
        for q in (in_queries + us_queries)
    }
    leakage_count = len(hist_hashes & query_hashes)

    onnx_engine = EventEmbeddingEngine(prefer_onnx=True)
    onnx_avail = onnx_engine.onnx_encoder.is_available()
    proj_engine = EventEmbeddingEngine(prefer_onnx=False)

    indexing_throughput: Dict[str, float] = {}
    emb_latency: Dict[str, float] = {}
    sqlite_bytes: Dict[str, int] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        in_db = tmp / "in_bench.db"
        us_db = tmp / "us_bench.db"
        in_store = PersistentHistoricalEventStore(in_db)
        us_store = PersistentHistoricalEventStore(us_db)

        # Measure indexing throughput with default active engine (ONNX if available, else Proj)
        t_idx_start = time.perf_counter()
        active_retriever_in = PersistentVectorRetriever(in_store, embedding_engine=onnx_engine)
        for item in in_hist:
            rep = nlp.process_raw_text(
                event_id=item.item_id,
                headline=item.headline,
                body_text=item.body_snippet,
                local_received_at=item.timestamp_utc,
            )
            in_store.upsert_event(rep, jurisdiction=item.jurisdiction, sector=item.sector, is_synthetic=True)
            ev = in_store.get_event(item.item_id)
            if ev:
                active_retriever_in.ensure_event_embedded(ev)
        dt_in = max(1e-6, time.perf_counter() - t_idx_start)
        indexing_throughput[f"IN ({onnx_engine.active_model_version})"] = round(len(in_hist) / dt_in, 2)

        t_us_start = time.perf_counter()
        active_retriever_us = PersistentVectorRetriever(us_store, embedding_engine=onnx_engine)
        for item in us_hist:
            rep = nlp.process_raw_text(
                event_id=item.item_id,
                headline=item.headline,
                body_text=item.body_snippet,
                local_received_at=item.timestamp_utc,
            )
            us_store.upsert_event(rep, jurisdiction=item.jurisdiction, sector=item.sector, is_synthetic=True)
            ev = us_store.get_event(item.item_id)
            if ev:
                active_retriever_us.ensure_event_embedded(ev)
        dt_us = max(1e-6, time.perf_counter() - t_us_start)
        indexing_throughput[f"US ({onnx_engine.active_model_version})"] = round(len(us_hist) / dt_us, 2)

        # Measure single-event embedding generation latency per encoder
        sample_texts = [h.headline for h in in_hist]
        for label, eng in [("FinConceptProj-256D", proj_engine)] + (
            [("BGE-Small-ONNX-INT8", onnx_engine)] if onnx_avail else []
        ):
            t_e = time.perf_counter()
            for txt in sample_texts:
                eng.active_encoder.encode_single(txt)
            emb_latency[label] = round(((time.perf_counter() - t_e) * 1000.0) / len(sample_texts), 3)

        # Pre-populate Proj embeddings too so query comparison only times query retrieval
        proj_retriever_in = PersistentVectorRetriever(in_store, embedding_engine=proj_engine)
        proj_retriever_us = PersistentVectorRetriever(us_store, embedding_engine=proj_engine)
        for item in in_hist:
            ev = in_store.get_event(item.item_id)
            if ev:
                proj_retriever_in.ensure_event_embedded(ev)
        for item in us_hist:
            ev = us_store.get_event(item.item_id)
            if ev:
                proj_retriever_us.ensure_event_embedded(ev)

        in_results: List[ModeBenchmarkMetrics] = [
            _evaluate_store_queries(in_store, proj_retriever_in, in_queries, RetrievalMode.JACCARD, "1_JACCARD_BASELINE", top_k),
            _evaluate_store_queries(in_store, proj_retriever_in, in_queries, RetrievalMode.BM25, "2_CAUSAL_BM25", top_k),
            _evaluate_store_queries(in_store, proj_retriever_in, in_queries, RetrievalMode.DENSE_SEMANTIC, "3A_DENSE_CONCEPT_PROJ", top_k),
            _evaluate_store_queries(in_store, proj_retriever_in, in_queries, RetrievalMode.HYBRID_MODULAR, "4A_HYBRID_CONCEPT_PROJ", top_k),
        ]
        us_results: List[ModeBenchmarkMetrics] = [
            _evaluate_store_queries(us_store, proj_retriever_us, us_queries, RetrievalMode.JACCARD, "1_JACCARD_BASELINE", top_k),
            _evaluate_store_queries(us_store, proj_retriever_us, us_queries, RetrievalMode.BM25, "2_CAUSAL_BM25", top_k),
            _evaluate_store_queries(us_store, proj_retriever_us, us_queries, RetrievalMode.DENSE_SEMANTIC, "3A_DENSE_CONCEPT_PROJ", top_k),
            _evaluate_store_queries(us_store, proj_retriever_us, us_queries, RetrievalMode.HYBRID_MODULAR, "4A_HYBRID_CONCEPT_PROJ", top_k),
        ]

        if onnx_avail:
            in_results.extend([
                _evaluate_store_queries(in_store, active_retriever_in, in_queries, RetrievalMode.DENSE_SEMANTIC, "3B_DENSE_ONNX_BGE_SMALL", top_k),
                _evaluate_store_queries(in_store, active_retriever_in, in_queries, RetrievalMode.HYBRID_MODULAR, "4B_HYBRID_ONNX_BGE_SMALL", top_k),
            ])
            us_results.extend([
                _evaluate_store_queries(us_store, active_retriever_us, us_queries, RetrievalMode.DENSE_SEMANTIC, "3B_DENSE_ONNX_BGE_SMALL", top_k),
                _evaluate_store_queries(us_store, active_retriever_us, us_queries, RetrievalMode.HYBRID_MODULAR, "4B_HYBRID_ONNX_BGE_SMALL", top_k),
            ])

        sqlite_bytes["IN_store"] = in_store.get_stats()["storage_bytes"]
        sqlite_bytes["US_store"] = us_store.get_stats()["storage_bytes"]

    ram_after = round(proc.memory_info().rss / (1024 ** 2), 2)

    # Combined weighted ablation summary across all 15 queries (10 IN + 5 US)
    combined: Dict[str, Dict[str, float]] = {}
    n_in, n_us = len(in_queries), len(us_queries)
    tot_q = n_in + n_us
    for r_in, r_us in zip(in_results, us_results):
        combined[r_in.mode_label] = {
            f"precision@{top_k}": round((r_in.precision_at_k * n_in + r_us.precision_at_k * n_us) / tot_q, 4),
            f"recall@{top_k}": round((r_in.recall_at_k * n_in + r_us.recall_at_k * n_us) / tot_q, 4),
            "mrr": round((r_in.mrr * n_in + r_us.mrr * n_us) / tot_q, 4),
            f"ndcg@{top_k}": round((r_in.ndcg_at_k * n_in + r_us.ndcg_at_k * n_us) / tot_q, 4),
            "p95_latency_ms": round(max(r_in.query_latency_p95_ms, r_us.query_latency_p95_ms), 3),
        }

    es_checks = verify_event_study_suite()

    return HistoricalBenchmarkReport(
        generated_at_utc=datetime.now(timezone.utc),
        python_version=platform.python_version(),
        platform_info=f"{platform.system()} {platform.release()} ({platform.machine()})",
        onnx_bge_small_available=onnx_avail,
        corpus_counts={
            "in_historical_events": len(in_hist),
            "in_queries": len(in_queries),
            "us_historical_events": len(us_hist),
            "us_queries": len(us_queries),
        },
        split_leakage_duplicates=leakage_count,
        indexing_throughput_events_per_sec=indexing_throughput,
        embedding_latency_mean_ms=emb_latency,
        ram_rss_mb_before=ram_before,
        ram_rss_mb_after=ram_after,
        sqlite_storage_bytes=sqlite_bytes,
        in_market_comparison=in_results,
        us_market_comparison=us_results,
        combined_ablation_summary=combined,
        event_study_checks=es_checks,
        all_event_study_checks_passed=all(es_checks.values()),
    )
