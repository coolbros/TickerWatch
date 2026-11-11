"""Model Governance, Provenance & Reproducible Experiment Registry (Phase 5 WP-I).

Lightweight, dependency-free local registry persisting complete experimental lineage
in SQLite (`registry.db`), human-readable JSON manifests, and SHA-256 integrity-verified
model + calibrator artifact bundles so any registered prediction run can be reproduced
bit-for-bit and audited for data boundaries, software versions, and empirical caveats.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import platform
import sqlite3
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
import pydantic
from pydantic import BaseModel, Field

from backend.alpha.feature_registry import (
    FEATURE_SCHEMA_VERSION,
    ModelConfiguration,
    build_feature_manifest,
)
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_models import TabularEventPredictor
from backend.alpha.predictive_targets import TARGET_SCHEMA_VERSION
from backend.alpha.prediction_uncertainty import CalibratedPrediction, UncertaintyCalibratorAndGate


class ModelArtifactRecord(BaseModel):
    """Complete governance & lineage metadata for a registered predictive model run."""

    model_id: str
    model_version: str
    created_at_utc: str
    algorithm: str
    model_config_name: str
    horizon_label: str

    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    feature_manifest_sha256: str
    target_schema_version: str = TARGET_SCHEMA_VERSION
    dataset_manifest_hash: str

    training_cutoff_utc: str
    validation_period: str
    test_period: str

    hyperparameters: Dict[str, Any] = Field(default_factory=dict)
    seed: int
    software_environment: Dict[str, str] = Field(default_factory=dict)
    git_commit_hash: str

    train_metrics: Dict[str, Any] = Field(default_factory=dict)
    oos_metrics: Dict[str, Any] = Field(default_factory=dict)
    calibration_summary: Dict[str, Any] = Field(default_factory=dict)

    leakage_audit_passed: bool
    is_synthetic: bool
    known_limitations: List[str] = Field(default_factory=list)

    artifact_rel_path: str
    artifact_sha256: str


def _capture_software_env() -> Dict[str, str]:
    return {
        "python_version": sys.version.split()[0],
        "numpy_version": str(np.__version__),
        "pydantic_version": str(pydantic.__version__),
        "platform": platform.platform(),
    }


def _capture_git_commit(repo_root: Optional[Path] = None) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
        return out.decode("utf-8").strip()
    except Exception:
        return "UNKNOWN"


def compute_dataset_manifest_hash(
    event_ids: Sequence[str],
    decision_timestamps: Sequence[datetime],
    is_synthetic: bool,
) -> str:
    """Deterministic SHA-256 digest identifying an exact chronological training/eval dataset."""
    h = hashlib.sha256()
    h.update(f"synthetic={int(is_synthetic)}|n={len(event_ids)}\n".encode("utf-8"))
    for eid, ts in zip(event_ids, decision_timestamps):
        h.update(f"{eid}|{ts.isoformat()}\n".encode("utf-8"))
    return h.hexdigest()


class LocalModelRegistry:
    """SQLite + JSON + SHA-256 verified artifact registry for reproducible Alpha research."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.artifacts_dir = self.root_dir / "artifacts"
        self.manifests_dir = self.root_dir / "manifests"
        self.db_path = self.root_dir / "registry.db"
        self._init_storage()

    def _init_storage(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_runs (
                    model_id TEXT PRIMARY KEY,
                    model_version TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    model_config_name TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL,
                    leakage_audit_passed INTEGER NOT NULL,
                    dataset_manifest_hash TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def register_run(
        self,
        model_id: str,
        model_version: str,
        model_config: ModelConfiguration,
        horizon_label: str,
        predictor: TabularEventPredictor,
        calibrator: UncertaintyCalibratorAndGate,
        dataset_manifest_hash: str,
        training_cutoff_utc: datetime,
        validation_period: str,
        test_period: str,
        train_metrics: Dict[str, Any],
        oos_metrics: Dict[str, Any],
        calibration_summary: Dict[str, Any],
        leakage_audit_passed: bool,
        is_synthetic: bool,
        known_limitations: Sequence[str],
        hyperparameters: Optional[Dict[str, Any]] = None,
        repo_root: Optional[Path] = None,
    ) -> ModelArtifactRecord:
        if not predictor.is_fitted:
            raise ValueError("Cannot register an unfitted TabularEventPredictor.")
        if predictor.config != model_config:
            raise ValueError(
                f"Predictor config {predictor.config.value} does not match registered model_config {model_config.value}."
            )

        manifest = build_feature_manifest(model_config)
        bundle = {"predictor": predictor, "calibrator": calibrator}
        blob = pickle.dumps(bundle, protocol=pickle.HIGHEST_PROTOCOL)
        artifact_sha256 = hashlib.sha256(blob).hexdigest()

        safe_id = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in model_id)
        artifact_rel_path = f"artifacts/{safe_id}.pkl"
        artifact_abs = self.root_dir / artifact_rel_path
        artifact_abs.write_bytes(blob)

        hparams = dict(hyperparameters or {})
        hparams.setdefault("algorithm", predictor.algorithm.value)
        hparams.setdefault("ridge_alpha", predictor.ridge_alpha)
        hparams.setdefault("gbdt_n_estimators", predictor.gbdt_n_estimators)
        hparams.setdefault("calibration_method", calibrator.method.value)

        record = ModelArtifactRecord(
            model_id=model_id,
            model_version=model_version,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            algorithm=predictor.algorithm.value,
            model_config_name=model_config.value,
            horizon_label=horizon_label,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            feature_manifest_sha256=manifest.manifest_sha256,
            target_schema_version=TARGET_SCHEMA_VERSION,
            dataset_manifest_hash=dataset_manifest_hash,
            training_cutoff_utc=training_cutoff_utc.isoformat(),
            validation_period=validation_period,
            test_period=test_period,
            hyperparameters=hparams,
            seed=int(predictor.seed),
            software_environment=_capture_software_env(),
            git_commit_hash=_capture_git_commit(repo_root),
            train_metrics=dict(train_metrics),
            oos_metrics=dict(oos_metrics),
            calibration_summary=dict(calibration_summary),
            leakage_audit_passed=bool(leakage_audit_passed),
            is_synthetic=bool(is_synthetic),
            known_limitations=list(known_limitations),
            artifact_rel_path=artifact_rel_path,
            artifact_sha256=artifact_sha256,
        )

        manifest_json = record.model_dump_json(indent=2)
        (self.manifests_dir / f"{safe_id}.json").write_text(manifest_json, encoding="utf-8")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO model_runs (
                    model_id, model_version, created_at_utc, algorithm,
                    model_config_name, horizon_label, is_synthetic,
                    leakage_audit_passed, dataset_manifest_hash, artifact_sha256, manifest_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.model_id,
                    record.model_version,
                    record.created_at_utc,
                    record.algorithm,
                    record.model_config_name,
                    record.horizon_label,
                    1 if record.is_synthetic else 0,
                    1 if record.leakage_audit_passed else 0,
                    record.dataset_manifest_hash,
                    record.artifact_sha256,
                    manifest_json,
                ),
            )
            conn.commit()
        return record

    def get_run(self, model_id: str) -> ModelArtifactRecord:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT manifest_json FROM model_runs WHERE model_id = ?",
                (model_id,),
            )
            row = cur.fetchone()
        if not row:
            raise KeyError(f"Model run '{model_id}' not found in registry.")
        return ModelArtifactRecord.model_validate_json(row[0])

    def list_runs(
        self,
        model_config: Optional[ModelConfiguration] = None,
        include_synthetic: bool = True,
    ) -> List[ModelArtifactRecord]:
        query = "SELECT manifest_json FROM model_runs WHERE 1=1"
        params: List[Any] = []
        if model_config is not None:
            query += " AND model_config_name = ?"
            params.append(model_config.value)
        if not include_synthetic:
            query += " AND is_synthetic = 0"
        query += " ORDER BY created_at_utc ASC"

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [ModelArtifactRecord.model_validate_json(r[0]) for r in rows]

    def verify_artifact_integrity(self, model_id: str) -> bool:
        rec = self.get_run(model_id)
        artifact_path = self.root_dir / rec.artifact_rel_path
        if not artifact_path.exists():
            return False
        actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        return actual == rec.artifact_sha256

    def load_bundle(
        self, model_id: str
    ) -> Tuple[ModelArtifactRecord, TabularEventPredictor, UncertaintyCalibratorAndGate]:
        rec = self.get_run(model_id)
        artifact_path = self.root_dir / rec.artifact_rel_path
        if not artifact_path.exists():
            raise FileNotFoundError(f"Artifact file missing for {model_id}: {artifact_path}")
        blob = artifact_path.read_bytes()
        actual_sha = hashlib.sha256(blob).hexdigest()
        if actual_sha != rec.artifact_sha256:
            raise ValueError(
                f"Artifact integrity check FAILED for {model_id}: expected {rec.artifact_sha256}, got {actual_sha}"
            )
        bundle = pickle.loads(blob)
        return rec, bundle["predictor"], bundle["calibrator"]

    def reproduce_predictions(
        self,
        model_id: str,
        feature_rows: Sequence[PointInTimeFeatureRow],
        round_trip_cost_bps_list: Optional[Sequence[float]] = None,
    ) -> List[CalibratedPrediction]:
        rec, predictor, calibrator = self.load_bundle(model_id)
        cfg = ModelConfiguration(rec.model_config_name)
        feat_dicts = [r.subset(cfg) for r in feature_rows]
        inst_ids = [r.instrument_id for r in feature_rows]
        raw = predictor.predict_raw(feat_dicts, instrument_ids=inst_ids)
        return calibrator.calibrate_and_gate(
            event_ids=[r.event_id for r in feature_rows],
            instrument_ids=inst_ids,
            raw_preds=raw,
            feature_dicts=feat_dicts,
            round_trip_cost_bps_list=round_trip_cost_bps_list,
        )
