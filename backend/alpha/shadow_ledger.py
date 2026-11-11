"""Immutable Shadow Signal Ledger (Phase 6 WP-F).

Maintains an append-only, cryptographically hashed SQLite WAL audit ledger separating:
1. `shadow_predictions` (complete E_t, H_t, M_t inputs + feature manifest + model lineage
   + calibrated prediction or NO_TRADE abstention + latency breakdown).
2. `shadow_outcomes` (subsequent realized market prices, spreads, costs, maturity,
   and executable returns linked by `prediction_id`).

Enforces immutability via SQLite `BEFORE UPDATE` / `BEFORE DELETE` triggers on both tables
and verifies per-record SHA-256 digests to ensure full reproducibility.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Tuple
import uuid
from pydantic import BaseModel, Field

from backend.alpha.timestamps import get_current_utc, to_utc_iso

LEDGER_SCHEMA_VERSION = "6.0.0-alpha"


class ModelValidationStatus(str, Enum):
    MODEL_ELIGIBLE = "MODEL_ELIGIBLE"
    MODEL_UNVALIDATED = "MODEL_UNVALIDATED"
    SYNTHETIC_DIAGNOSTIC_ONLY = "SYNTHETIC_DIAGNOSTIC_ONLY"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"


class ShadowLedgerEntry(BaseModel):
    """Immutable combined input + prediction record persisted prior to horizon maturation."""

    prediction_id: str = Field(default_factory=lambda: f"spred_{uuid.uuid4().hex[:14]}")
    recorded_at_utc: datetime = Field(default_factory=get_current_utc)
    is_synthetic: bool = False

    # Input snapshot fields
    event_id: str
    instrument_id: str
    news_source: str
    news_revision: int = 1
    decision_timestamp_utc: datetime
    event_representation: Dict[str, Any]
    historical_intelligence_representation: Dict[str, Any]
    market_regime_representation: Dict[str, Any]
    feature_values: Dict[str, Optional[float]] = Field(default_factory=dict)
    feature_manifest_version: str
    feature_manifest_sha256: str
    input_data_quality_flags: List[str] = Field(default_factory=list)

    # Prediction snapshot fields
    model_id: str
    model_version: str
    model_training_dataset_hash: str
    model_validation_status: ModelValidationStatus
    forecast_horizon: str
    expected_return: Optional[float] = None
    median_return: Optional[float] = None
    predicted_direction_probabilities: Dict[str, Optional[float]] = Field(default_factory=dict)
    predictive_quantiles: Dict[str, Optional[float]] = Field(default_factory=dict)
    prediction_interval_80pct: Optional[Tuple[float, float]] = None
    prediction_interval_50pct: Optional[Tuple[float, float]] = None
    calibration_metadata: Dict[str, Any] = Field(default_factory=dict)
    confidence_and_uncertainty: Dict[str, Any] = Field(default_factory=dict)
    action_state: str = "NO_TRADE"
    signal_direction: int = 0
    no_trade_status: bool = True
    no_trade_reasons: List[str] = Field(default_factory=list)

    # Operational telemetry
    latency_breakdown_ms: Dict[str, float] = Field(default_factory=dict)
    ledger_schema_version: str = LEDGER_SCHEMA_VERSION
    record_sha256: str = ""

    def compute_canonical_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"record_sha256", "recorded_at_utc"})
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class ShadowOutcomeRecord(BaseModel):
    """Append-only subsequent market outcome linked strictly by `prediction_id`."""

    outcome_id: str = Field(default_factory=lambda: f"sout_{uuid.uuid4().hex[:14]}")
    prediction_id: str
    event_id: str
    instrument_id: str
    horizon_label: str
    label_type: str = "EXECUTABLE"

    observed_at_utc: datetime = Field(default_factory=get_current_utc)
    entry_timestamp_utc: Optional[datetime] = None
    exit_timestamp_utc: Optional[datetime] = None
    actual_entry_price: Optional[float] = None
    actual_exit_price: Optional[float] = None
    actual_realized_return: Optional[float] = None

    executable_entry_price: Optional[float] = None
    executable_exit_price: Optional[float] = None
    executable_realized_return: Optional[float] = None

    outcome_matured: bool = False
    market_data_provenance: str = "UNAVAILABLE"
    realized_spread_bps: Optional[float] = None
    estimated_transaction_cost_bps: Optional[float] = None
    net_estimated_return: Optional[float] = None
    data_quality_flags: List[str] = Field(default_factory=list)
    is_synthetic: bool = False


class ShadowSignalLedger:
    """Append-only SQLite WAL shadow prediction and outcome ledger."""

    def __init__(self, db_path: str | Path = "data/alpha_shadow_ledger.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS shadow_predictions (
                    prediction_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    news_source TEXT NOT NULL,
                    news_revision INTEGER NOT NULL,
                    decision_timestamp_utc TEXT NOT NULL,
                    forecast_horizon TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    model_validation_status TEXT NOT NULL,
                    action_state TEXT NOT NULL,
                    no_trade_status INTEGER NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 0,
                    record_sha256 TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_spred_event_ts
                ON shadow_predictions(event_id, decision_timestamp_utc);

                CREATE TRIGGER IF NOT EXISTS trg_immutable_shadow_predictions_update
                BEFORE UPDATE ON shadow_predictions
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_LEDGER: shadow_predictions cannot be updated');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_immutable_shadow_predictions_delete
                BEFORE DELETE ON shadow_predictions
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_LEDGER: shadow_predictions cannot be deleted');
                END;

                CREATE TABLE IF NOT EXISTS shadow_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    prediction_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    label_type TEXT NOT NULL,
                    outcome_matured INTEGER NOT NULL,
                    actual_realized_return REAL,
                    executable_realized_return REAL,
                    estimated_transaction_cost_bps REAL,
                    net_estimated_return REAL,
                    is_synthetic INTEGER NOT NULL DEFAULT 0,
                    outcome_json TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    FOREIGN KEY(prediction_id) REFERENCES shadow_predictions(prediction_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sout_pred
                ON shadow_outcomes(prediction_id, horizon_label, label_type);

                CREATE TRIGGER IF NOT EXISTS trg_immutable_shadow_outcomes_update
                BEFORE UPDATE ON shadow_outcomes
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_LEDGER: shadow_outcomes cannot be updated');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_immutable_shadow_outcomes_delete
                BEFORE DELETE ON shadow_outcomes
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_SHADOW_LEDGER: shadow_outcomes cannot be deleted');
                END;
                """
            )
            conn.commit()

    def append_prediction(self, entry: ShadowLedgerEntry) -> ShadowLedgerEntry:
        """Persist an immutable shadow prediction with verified canonical SHA-256 digest."""
        digest = entry.compute_canonical_sha256()
        sealed = entry.model_copy(update={"record_sha256": digest})
        rec_iso = to_utc_iso(sealed.recorded_at_utc) or ""
        dec_iso = to_utc_iso(sealed.decision_timestamp_utc) or ""
        entry_json = sealed.model_dump_json()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO shadow_predictions (
                    prediction_id, event_id, instrument_id, news_source, news_revision,
                    decision_timestamp_utc, forecast_horizon, model_id, model_version,
                    model_validation_status, action_state, no_trade_status, is_synthetic,
                    record_sha256, entry_json, recorded_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sealed.prediction_id,
                    sealed.event_id,
                    sealed.instrument_id,
                    sealed.news_source,
                    sealed.news_revision,
                    dec_iso,
                    sealed.forecast_horizon,
                    sealed.model_id,
                    sealed.model_version,
                    sealed.model_validation_status.value,
                    sealed.action_state,
                    1 if sealed.no_trade_status else 0,
                    1 if sealed.is_synthetic else 0,
                    sealed.record_sha256,
                    entry_json,
                    rec_iso,
                ),
            )
            conn.commit()
        return sealed

    def append_outcome(self, outcome: ShadowOutcomeRecord) -> ShadowOutcomeRecord:
        """Link a newly matured market outcome cleanly without modifying the prediction row."""
        obs_iso = to_utc_iso(outcome.observed_at_utc) or ""
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM shadow_predictions WHERE prediction_id = ?",
                (outcome.prediction_id,),
            ).fetchone()
            if not exists:
                raise KeyError(f"Cannot link outcome to missing prediction_id={outcome.prediction_id}")

            conn.execute(
                """
                INSERT INTO shadow_outcomes (
                    outcome_id, prediction_id, event_id, instrument_id, horizon_label,
                    label_type, outcome_matured, actual_realized_return,
                    executable_realized_return, estimated_transaction_cost_bps,
                    net_estimated_return, is_synthetic, outcome_json, recorded_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.outcome_id,
                    outcome.prediction_id,
                    outcome.event_id,
                    outcome.instrument_id,
                    outcome.horizon_label,
                    outcome.label_type,
                    1 if outcome.outcome_matured else 0,
                    outcome.actual_realized_return,
                    outcome.executable_realized_return,
                    outcome.estimated_transaction_cost_bps,
                    outcome.net_estimated_return,
                    1 if outcome.is_synthetic else 0,
                    outcome.model_dump_json(),
                    obs_iso,
                ),
            )
            conn.commit()
        return outcome

    def get_prediction(self, prediction_id: str) -> ShadowLedgerEntry:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT entry_json FROM shadow_predictions WHERE prediction_id = ?",
                (prediction_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"prediction_id '{prediction_id}' not found")
        return ShadowLedgerEntry.model_validate_json(row[0])

    def verify_integrity(self, prediction_id: str) -> bool:
        entry = self.get_prediction(prediction_id)
        return entry.record_sha256 == entry.compute_canonical_sha256()

    def list_predictions(
        self,
        include_synthetic: bool = True,
        validation_status: Optional[ModelValidationStatus] = None,
    ) -> List[ShadowLedgerEntry]:
        q = "SELECT entry_json FROM shadow_predictions WHERE 1=1"
        params: List[Any] = []
        if not include_synthetic:
            q += " AND is_synthetic = 0"
        if validation_status is not None:
            q += " AND model_validation_status = ?"
            params.append(validation_status.value)
        q += " ORDER BY decision_timestamp_utc ASC, recorded_at_utc ASC"

        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [ShadowLedgerEntry.model_validate_json(r[0]) for r in rows]

    def get_outcomes_for_prediction(self, prediction_id: str) -> List[ShadowOutcomeRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT outcome_json FROM shadow_outcomes WHERE prediction_id = ? ORDER BY recorded_at_utc ASC",
                (prediction_id,),
            ).fetchall()
        return [ShadowOutcomeRecord.model_validate_json(r[0]) for r in rows]

    def summary_counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            total_pred = conn.execute("SELECT COUNT(*) FROM shadow_predictions").fetchone()[0]
            real_pred = conn.execute("SELECT COUNT(*) FROM shadow_predictions WHERE is_synthetic = 0").fetchone()[0]
            abstained = conn.execute("SELECT COUNT(*) FROM shadow_predictions WHERE no_trade_status = 1").fetchone()[0]
            total_out = conn.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0]
            matured_out = conn.execute("SELECT COUNT(*) FROM shadow_outcomes WHERE outcome_matured = 1").fetchone()[0]
        return {
            "total_predictions": int(total_pred),
            "real_predictions": int(real_pred),
            "abstained_predictions": int(abstained),
            "total_outcomes": int(total_out),
            "matured_outcomes": int(matured_out),
        }
