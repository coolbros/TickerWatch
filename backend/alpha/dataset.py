"""Reproducible Research Dataset Exporter and Manifest Generator."""

import csv
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from backend.alpha.alignment import AlignedEventRecord
from backend.alpha.schemas import QualityQuarantineRecord
from backend.alpha.timestamps import to_utc_iso

logger = logging.getLogger("tickerwatch.alpha.dataset")


class ResearchDatasetManifest(BaseModel):
    """Immutable provenance manifest for an exported quantitative research dataset."""

    dataset_id: str
    export_timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_aligned_records: int = 0
    clean_records_count: int = 0
    anomalous_records_count: int = 0
    instruments_covered: List[str] = Field(default_factory=list)
    earliest_decision_utc: Optional[str] = None
    latest_decision_utc: Optional[str] = None
    dataset_sha256: str = ""
    export_format: str = "jsonl"
    schema_version: str = "2.0.0"
    pipeline_version: str = "1.0.0-alpha"


class DatasetExporter:
    """Exports AlignedEventRecords into standardized, reproducible research datasets."""

    def __init__(self, output_dir: str = "data/research_datasets") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_dataset(
        self,
        records: List[AlignedEventRecord],
        dataset_name: str = "alpha_research_phase2",
        format_type: str = "jsonl",
    ) -> Tuple[Path, Path]:
        """
        Export aligned events to disk with cryptographic manifest.

        Args:
            records: List of AlignedEventRecord objects.
            dataset_name: Base name for exported files.
            format_type: 'jsonl' or 'csv'.

        Returns:
            Tuple of (data_file_path, manifest_file_path).
        """
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dataset_id = f"{dataset_name}_{now_str}"

        target_data_file = self.output_dir / f"{dataset_id}.{format_type}"
        manifest_file = self.output_dir / f"{dataset_id}_manifest.json"

        hasher = hashlib.sha256()
        instruments_seen = set()
        clean_count = 0
        anomalous_count = 0
        decision_times = []

        if format_type == "jsonl":
            with open(target_data_file, "w", encoding="utf-8") as f:
                for rec in records:
                    line = rec.model_dump_json()
                    hasher.update(line.encode("utf-8") + b"\n")
                    f.write(line + "\n")

                    if rec.quality_flag == "CLEAN":
                        clean_count += 1
                    else:
                        anomalous_count += 1

                    for inst in rec.pre_event_context.keys():
                        instruments_seen.add(inst)
                    decision_times.append(rec.decision_timestamp_utc)

        elif format_type == "csv":
            # Flattened table representation for standard statistical tools (pandas, R)
            fieldnames = [
                "alignment_id",
                "news_event_id",
                "headline",
                "entity_ids",
                "published_at_utc",
                "decision_timestamp_utc",
                "instrument_id",
                "relevance",
                "pre_mid_price",
                "pre_spread",
                "pre_freshness_ms",
                "pre_volatility_bps",
                "session_state",
                "ret_100ms_bps",
                "ret_1s_bps",
                "ret_5s_bps",
                "ret_30s_bps",
                "ret_5m_bps",
                "quality_flag",
            ]
            with open(target_data_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for rec in records:
                    if rec.quality_flag == "CLEAN":
                        clean_count += 1
                    else:
                        anomalous_count += 1
                    decision_times.append(rec.decision_timestamp_utc)

                    for inst_id, pre in rec.pre_event_context.items():
                        instruments_seen.add(inst_id)
                        horizons = rec.forward_outcomes.get(inst_id, {})
                        row = {
                            "alignment_id": rec.alignment_id,
                            "news_event_id": rec.news_event_id,
                            "headline": rec.headline,
                            "entity_ids": ",".join(rec.entity_ids),
                            "published_at_utc": to_utc_iso(rec.published_at_utc),
                            "decision_timestamp_utc": to_utc_iso(rec.decision_timestamp_utc),
                            "instrument_id": inst_id,
                            "relevance": pre.relevance.value,
                            "pre_mid_price": str(pre.mid_price) if pre.mid_price else "",
                            "pre_spread": str(pre.spread) if pre.spread else "",
                            "pre_freshness_ms": str(pre.feed_freshness_ms) if pre.feed_freshness_ms else "",
                            "pre_volatility_bps": str(pre.short_term_volatility_bps) if pre.short_term_volatility_bps else "",
                            "session_state": pre.session_state.value,
                            "ret_100ms_bps": str(horizons.get("100ms", {}).forward_return_bps or ""),
                            "ret_1s_bps": str(horizons.get("1s", {}).forward_return_bps or ""),
                            "ret_5s_bps": str(horizons.get("5s", {}).forward_return_bps or ""),
                            "ret_30s_bps": str(horizons.get("30s", {}).forward_return_bps or ""),
                            "ret_5m_bps": str(horizons.get("5m", {}).forward_return_bps or ""),
                            "quality_flag": rec.quality_flag,
                        }
                        line = json.dumps(row, sort_keys=True)
                        hasher.update(line.encode("utf-8"))
                        writer.writerow(row)

        earliest_dt = to_utc_iso(min(decision_times)) if decision_times else None
        latest_dt = to_utc_iso(max(decision_times)) if decision_times else None

        manifest = ResearchDatasetManifest(
            dataset_id=dataset_id,
            total_aligned_records=len(records),
            clean_records_count=clean_count,
            anomalous_records_count=anomalous_count,
            instruments_covered=sorted(list(instruments_seen)),
            earliest_decision_utc=earliest_dt,
            latest_decision_utc=latest_dt,
            dataset_sha256=hasher.hexdigest(),
            export_format=format_type,
        )

        manifest_file.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return target_data_file, manifest_file
