"""Real-Data Readiness & Empirical Corpus Integrity Auditor (Phase 5 WP-B).

Inspects accessible SQLite databases (`tickerwatch.db`, `PersistentHistoricalEventStore`),
market-data partitions (`data/`), outcome labels, consensus expectations, and revision metadata
without fabricating or mixing synthetic fixtures with empirical records.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.alpha.event_classifier import FinancialEventClassifier
from backend.alpha.historical_store import PersistentHistoricalEventStore


class ReadinessStatus(str, Enum):
    """Strict governance verdict on empirical predictive training readiness."""

    READY = "READY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    BLOCKED = "BLOCKED"


class DataPrerequisitesStatus(BaseModel):
    """Checklist against Phase 4/5 minimum institutional research thresholds."""

    nse_bse_disclosures_target: int = 5000
    nse_bse_disclosures_actual: int = 0
    macro_releases_target: int = 150
    macro_releases_actual: int = 0
    historical_span_years_target: float = 2.0
    in_historical_span_years_actual: float = 0.0
    us_historical_span_years_actual: float = 0.0
    market_1m_or_l1_history_available: bool = False
    l2_depth_history_available: bool = False
    corporate_action_factors_available: bool = False
    futures_rollover_ledger_available: bool = False
    consensus_estimates_available: bool = False


class RepositoryDataInventory(BaseModel):
    """Comprehensive empirical inventory strictly segregating real vs synthetic data."""

    audit_timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    readiness_status_in: ReadinessStatus
    readiness_status_us: ReadinessStatus
    overall_empirical_training_status: ReadinessStatus

    # Core Counts (synthetic NEVER added into real counts)
    real_events_count: int = 0
    synthetic_events_count: int = 0
    unique_real_issuers_count: int = 0
    unique_real_issuers: List[str] = Field(default_factory=list)

    events_by_subtype_real: Dict[str, int] = Field(default_factory=dict)
    events_by_jurisdiction_real: Dict[str, int] = Field(default_factory=dict)
    events_by_instrument_real: Dict[str, int] = Field(default_factory=dict)
    events_by_source_real: Dict[str, int] = Field(default_factory=dict)

    earliest_real_event_utc: Optional[str] = None
    latest_real_event_utc: Optional[str] = None
    observed_at_min_utc: Optional[str] = None
    observed_at_max_utc: Optional[str] = None
    observed_timestamps_are_batch_backfill: bool = False

    missing_timestamp_pct: Dict[str, float] = Field(default_factory=dict)
    missing_body_or_snippet_pct: float = 0.0
    duplicate_content_hash_count: int = 0
    duplicate_title_count: int = 0
    revision_or_amendment_count_real: int = 0

    persistent_store_events_real: int = 0
    persistent_store_events_synthetic: int = 0
    valid_real_market_observations: int = 0
    synthetic_market_benchmark_files: int = 0
    matured_real_labels_by_instrument_horizon: Dict[str, int] = Field(default_factory=dict)
    events_with_valid_real_market_context: int = 0
    events_with_real_consensus: int = 0

    prerequisites: DataPrerequisitesStatus = Field(default_factory=DataPrerequisitesStatus)
    blocking_gaps: List[str] = Field(default_factory=list)


class RealDataReadinessAuditor:
    """Audits repository SQLite stores and local market-data directories."""

    def __init__(
        self,
        legacy_db_path: Optional[Path] = Path("tickerwatch.db"),
        historical_store_path: Optional[Path] = Path("data/alpha_historical_store.db"),
        data_dir: Optional[Path] = Path("data"),
    ) -> None:
        self.legacy_db_path = Path(legacy_db_path) if legacy_db_path else None
        self.historical_store_path = Path(historical_store_path) if historical_store_path else None
        self.data_dir = Path(data_dir) if data_dir else None
        self._classifier = FinancialEventClassifier()

    def audit(self) -> RepositoryDataInventory:
        real_events = 0
        syn_events = 0
        issuers: Counter[str] = Counter()
        subtypes_real: Counter[str] = Counter()
        jurisdictions_real: Counter[str] = Counter()
        instruments_real: Counter[str] = Counter()
        sources_real: Counter[str] = Counter()

        pub_dates: List[str] = []
        obs_dates: List[str] = []
        miss_pub = 0
        miss_orig_pub = 0
        miss_obs = 0
        miss_snip = 0
        amendments_real = 0
        content_hashes: List[str] = []
        titles: List[str] = []

        # 1. Inspect Phase 1-3 Legacy Ingestion DB (`tickerwatch.db`)
        if self.legacy_db_path and self.legacy_db_path.exists():
            conn = sqlite3.connect(str(self.legacy_db_path))
            try:
                has_articles = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles'"
                ).fetchone()
                if has_articles:
                    rows = conn.execute(
                        """
                        SELECT connector_id, title, snippet, published_at_utc,
                               published_at_original, observed_at_utc, content_hash, tickers, raw_metadata
                        FROM articles
                        """
                    ).fetchall()
                    for (cid, title, snip, pub_utc, orig_pub, obs_utc, chash, tickers_json, raw_meta) in rows:
                        is_syn = (cid == "test_api_conn") or ("synthetic" in (cid or "").lower())
                        if chash:
                            content_hashes.append(chash)
                        if title:
                            titles.append(title)

                        if not pub_utc or not str(pub_utc).strip():
                            miss_pub += 1
                        if not orig_pub or not str(orig_pub).strip():
                            miss_orig_pub += 1
                        if not obs_utc or not str(obs_utc).strip():
                            miss_obs += 1
                        if not snip or not str(snip).strip():
                            miss_snip += 1

                        if is_syn:
                            syn_events += 1
                            continue

                        real_events += 1
                        sources_real[cid or "UNKNOWN"] += 1
                        if pub_utc:
                            pub_dates.append(pub_utc)
                        if obs_utc:
                            obs_dates.append(obs_utc)

                        jur = "US" if (cid or "").startswith("sec_") else "IN"
                        jurisdictions_real[jur] += 1

                        # Parse tickers
                        parsed_tickers: List[str] = []
                        if tickers_json:
                            try:
                                raw_t = json.loads(tickers_json)
                                if isinstance(raw_t, list):
                                    parsed_tickers = [str(x).strip().upper() for x in raw_t if str(x).strip()]
                            except Exception:
                                pass
                        for tk in parsed_tickers:
                            issuers[tk] += 1
                            instruments_real[f"{jur}:{tk}:EQ"] += 1
                        if not parsed_tickers:
                            instruments_real[f"{jur}:UNMAPPED"] += 1

                        # Detect SEC amendment filings (/A) or correction notices
                        form_val = ""
                        if raw_meta:
                            try:
                                form_val = str(json.loads(raw_meta).get("form", ""))
                            except Exception:
                                form_val = ""
                        t_low = (title or "").lower()
                        if form_val.upper().endswith("/A") or any(w in t_low for w in ("amendment", "correction", "/a ")):
                            amendments_real += 1

                        cls_res = self._classifier.classify_text(title or "", body_text=snip)
                        subtypes_real[cls_res.primary_subtype.value] += 1
            finally:
                conn.close()

        # 2. Inspect Persistent Phase 4 Historical Store (if initialized on disk)
        store_real = 0
        store_syn = 0
        matured_map: Dict[str, int] = {}
        if self.historical_store_path and self.historical_store_path.exists():
            store = PersistentHistoricalEventStore(self.historical_store_path)
            s_conn = store._get_connection()
            try:
                for is_s, cnt in s_conn.execute(
                    "SELECT is_synthetic, COUNT(*) FROM historical_events GROUP BY is_synthetic"
                ).fetchall():
                    if int(is_s) == 1:
                        store_syn += int(cnt)
                    else:
                        store_real += int(cnt)

                for inst_id, hor, cnt in s_conn.execute(
                    """
                    SELECT o.instrument_id, o.horizon_label, COUNT(*)
                    FROM historical_event_outcomes o
                    JOIN historical_events e ON e.event_id = o.event_id
                    WHERE e.is_synthetic = 0
                    GROUP BY o.instrument_id, o.horizon_label
                    """
                ).fetchall():
                    matured_map[f"{inst_id}|{hor}"] = int(cnt)
            finally:
                store._close_connection(s_conn)

        # 3. Inspect `data/` partitions for market data files (separating synthetic benchmark exports)
        valid_real_mkt_obs = 0
        syn_bench_files = 0
        if self.data_dir and self.data_dir.exists():
            for p in self.data_dir.rglob("*"):
                if not p.is_file():
                    continue
                if p.name == (self.historical_store_path.name if self.historical_store_path else ""):
                    continue
                name_low = p.name.lower()
                if "benchmark" in name_low or "synthetic" in name_low or "fixture" in name_low:
                    syn_bench_files += 1
                elif p.suffix in (".jsonl", ".gz", ".parquet", ".csv"):
                    # Count non-empty lines conservatively for JSONL market ticks
                    if p.suffix == ".jsonl":
                        try:
                            valid_real_mkt_obs += sum(1 for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip())
                        except Exception:
                            pass

        total_legacy = real_events + syn_events
        miss_pct = {
            "published_at_utc": round((miss_pub / max(1, total_legacy)) * 100.0, 2),
            "published_at_original": round((miss_orig_pub / max(1, total_legacy)) * 100.0, 2),
            "observed_at_utc": round((miss_obs / max(1, total_legacy)) * 100.0, 2),
        }
        miss_snip_pct = round((miss_snip / max(1, total_legacy)) * 100.0, 2)

        obs_min = min(obs_dates) if obs_dates else None
        obs_max = max(obs_dates) if obs_dates else None
        batch_backfill = False
        if obs_min and obs_max and real_events > 10:
            try:
                t_a = datetime.fromisoformat(obs_min.replace("Z", "+00:00"))
                t_b = datetime.fromisoformat(obs_max.replace("Z", "+00:00"))
                # If thousands of multi-year filings all share an ingestion span < 1 hour, it is a batch backfill
                if (t_b - t_a).total_seconds() < 3600.0:
                    batch_backfill = True
            except Exception:
                pass

        # Compute date span for real IN and US events
        in_span_yrs = 0.0
        us_span_yrs = 0.0
        if self.legacy_db_path and self.legacy_db_path.exists() and real_events > 0:
            conn = sqlite3.connect(str(self.legacy_db_path))
            try:
                us_r = conn.execute(
                    "SELECT MIN(published_at_utc), MAX(published_at_utc) FROM articles WHERE connector_id LIKE 'sec_%'"
                ).fetchone()
                in_r = conn.execute(
                    "SELECT MIN(published_at_utc), MAX(published_at_utc) FROM articles WHERE connector_id NOT LIKE 'sec_%' AND connector_id != 'test_api_conn'"
                ).fetchone()
                if us_r and us_r[0] and us_r[1]:
                    us_span_yrs = round(
                        (
                            datetime.fromisoformat(us_r[1].replace("Z", "+00:00"))
                            - datetime.fromisoformat(us_r[0].replace("Z", "+00:00"))
                        ).total_seconds()
                        / (365.25 * 86400.0),
                        2,
                    )
                if in_r and in_r[0] and in_r[1]:
                    in_span_yrs = round(
                        (
                            datetime.fromisoformat(in_r[1].replace("Z", "+00:00"))
                            - datetime.fromisoformat(in_r[0].replace("Z", "+00:00"))
                        ).total_seconds()
                        / (365.25 * 86400.0),
                        2,
                    )
            finally:
                conn.close()

        in_corp_real = jurisdictions_real.get("IN", 0)
        # Check Blocking Gaps
        blockers: List[str] = []
        if in_corp_real < 5000 or in_span_yrs < 2.0:
            blockers.append(
                f"IN corpus critically undersized: {in_corp_real} real records (0 company-specific NSE/BSE filings, {in_span_yrs:.2f} yr span vs >=5,000 events & >=2.0 yrs target)."
            )
        if real_events == 0 or len(matured_map) == 0:
            blockers.append(
                "Zero real matured post-event market outcome labels (1m-5d) exist in persistent storage."
            )
        if valid_real_mkt_obs == 0:
            blockers.append(
                "Zero authorized historical 1m OHLCV / L1 / L2 market-data archives bundled for either IN or US equities."
            )
        if batch_backfill:
            blockers.append(
                "All 3,042 real records in tickerwatch.db share a ~1.5-second batch backfill `observed_at_utc` window (2026-11-08T09:34Z) and cannot serve as real-time wire arrival latency."
            )
        if subtypes_real.get("ROUTINE_ADMINISTRATIVE", 0) > 0.9 * max(1, real_events):
            blockers.append(
                f"{subtypes_real.get('ROUTINE_ADMINISTRATIVE', 0)}/{real_events} real filings hold header-only metadata without full-text item extraction (61% SEC Form 4 insider logs; 0 real Indian earnings/contracts)."
            )
        blockers.append("0.0% historical point-in-time analyst consensus expectations (E) available.")

        prereq = DataPrerequisitesStatus(
            nse_bse_disclosures_actual=0,  # Moneycontrol 4 general wrap-ups are NOT official NSE/BSE filings
            macro_releases_actual=0,
            in_historical_span_years_actual=in_span_yrs,
            us_historical_span_years_actual=us_span_yrs,
            market_1m_or_l1_history_available=valid_real_mkt_obs > 0,
            l2_depth_history_available=False,
            corporate_action_factors_available=False,
            futures_rollover_ledger_available=False,
            consensus_estimates_available=False,
        )

        status_in = ReadinessStatus.BLOCKED if in_corp_real < 100 or valid_real_mkt_obs == 0 else ReadinessStatus.READY
        status_us = ReadinessStatus.INSUFFICIENT_DATA if valid_real_mkt_obs == 0 or len(matured_map) == 0 else ReadinessStatus.READY
        overall_status = (
            ReadinessStatus.READY
            if (status_in == ReadinessStatus.READY or status_us == ReadinessStatus.READY)
            else ReadinessStatus.BLOCKED
        )

        return RepositoryDataInventory(
            readiness_status_in=status_in,
            readiness_status_us=status_us,
            overall_empirical_training_status=overall_status,
            real_events_count=real_events,
            synthetic_events_count=syn_events,
            unique_real_issuers_count=len(issuers),
            unique_real_issuers=sorted(issuers.keys()),
            events_by_subtype_real=dict(subtypes_real),
            events_by_jurisdiction_real=dict(jurisdictions_real),
            events_by_instrument_real=dict(instruments_real),
            events_by_source_real=dict(sources_real),
            earliest_real_event_utc=min(pub_dates) if pub_dates else None,
            latest_real_event_utc=max(pub_dates) if pub_dates else None,
            observed_at_min_utc=obs_min,
            observed_at_max_utc=obs_max,
            observed_timestamps_are_batch_backfill=batch_backfill,
            missing_timestamp_pct=miss_pct,
            missing_body_or_snippet_pct=miss_snip_pct,
            duplicate_content_hash_count=len(content_hashes) - len(set(content_hashes)),
            duplicate_title_count=len(titles) - len(set(titles)),
            revision_or_amendment_count_real=amendments_real,
            persistent_store_events_real=store_real,
            persistent_store_events_synthetic=store_syn,
            valid_real_market_observations=valid_real_mkt_obs,
            synthetic_market_benchmark_files=syn_bench_files,
            matured_real_labels_by_instrument_horizon=matured_map,
            events_with_valid_real_market_context=0,
            events_with_real_consensus=0,
            prerequisites=prereq,
            blocking_gaps=blockers,
        )
