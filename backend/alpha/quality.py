"""Data Quality Engine, Schema Validation & Quarantine Management."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from backend.alpha.instruments import get_instrument_master
from backend.alpha.schemas import MarketEvent, NewsEvent, QualityQuarantineRecord

logger = logging.getLogger("tickerwatch.alpha.quality")


class QualityViolation(BaseModel):
    """Specific rule violation detected during data quality auditing."""
    rule_id: str
    severity: str  # FATAL, WARNING, INFO
    entity_ref: str
    message: str
    raw_snippet: Optional[str] = None


class DataQualityReport(BaseModel):
    """Consolidated summary of data quality metrics and quarantine actions."""

    total_records_audited: int = 0
    passed_count: int = 0
    warning_count: int = 0
    quarantined_count: int = 0
    rule_violations: List[QualityViolation] = Field(default_factory=list)
    quarantine_records: List[QualityQuarantineRecord] = Field(default_factory=list)
    audited_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_clean(self) -> bool:
        return self.quarantined_count == 0


class DataQualityEngine:
    """
    Validates data integrity across news, market ticks, and temporal alignments.
    Enforces quality gates before records enter research datasets.
    """

    def __init__(self) -> None:
        self.instrument_master = get_instrument_master()

    def audit_news_event(self, event: NewsEvent) -> Tuple[bool, List[QualityViolation]]:
        """Validate news event contract compliance."""
        violations: List[QualityViolation] = []

        # 1. Mandatory headline
        if not event.headline or not event.headline.strip():
            violations.append(
                QualityViolation(
                    rule_id="NEWS_EMPTY_HEADLINE",
                    severity="FATAL",
                    entity_ref=event.event_id,
                    message="Headline is empty or whitespace only",
                )
            )

        # 2. Timestamp validity
        if event.published_at_utc and event.published_at_utc.year < 2000:
            violations.append(
                QualityViolation(
                    rule_id="NEWS_ANOMALOUS_TIMESTAMP",
                    severity="FATAL",
                    entity_ref=event.event_id,
                    message=f"Publication year {event.published_at_utc.year} is invalid",
                )
            )

        # 3. Future timestamp check (allowing 60s clock skew)
        now_utc = datetime.now(timezone.utc)
        if event.local_received_at_utc > now_utc:
            diff = (event.local_received_at_utc - now_utc).total_seconds()
            if diff > 60.0:
                violations.append(
                    QualityViolation(
                        rule_id="NEWS_FUTURE_LOCAL_TIMESTAMP",
                        severity="FATAL",
                        entity_ref=event.event_id,
                        message=f"Local receipt timestamp is {diff}s in the future",
                    )
                )

        # 4. Content hash presence
        if not event.content_hash or len(event.content_hash) < 32:
            violations.append(
                QualityViolation(
                    rule_id="NEWS_INVALID_CONTENT_HASH",
                    severity="FATAL",
                    entity_ref=event.event_id,
                    message="Content hash is missing or corrupted",
                )
            )

        is_valid = not any(v.severity == "FATAL" for v in violations)
        return is_valid, violations

    def audit_market_event(self, event: MarketEvent) -> Tuple[bool, List[QualityViolation]]:
        """Validate market event microstructure and price sanity."""
        violations: List[QualityViolation] = []

        # 1. Registered instrument
        inst_rec = self.instrument_master.get(event.instrument_id)
        if not inst_rec:
            violations.append(
                QualityViolation(
                    rule_id="MKT_UNREGISTERED_INSTRUMENT",
                    severity="FATAL",
                    entity_ref=event.event_id,
                    message=f"Instrument '{event.instrument_id}' not found in Instrument Master",
                )
            )

        # 2. Non-negative prices
        for price_field in ("bid_price", "ask_price", "last_traded_price"):
            val: Optional[Decimal] = getattr(event, price_field)
            if val is not None and val < Decimal("0"):
                violations.append(
                    QualityViolation(
                        rule_id="MKT_NEGATIVE_PRICE",
                        severity="FATAL",
                        entity_ref=event.event_id,
                        message=f"{price_field} {val} is negative",
                    )
                )

        # 3. Crossed Book Check: bid must be strictly less than ask
        if event.bid_price is not None and event.ask_price is not None:
            if event.bid_price > event.ask_price:
                violations.append(
                    QualityViolation(
                        rule_id="MKT_CROSSED_ORDER_BOOK",
                        severity="FATAL",
                        entity_ref=event.event_id,
                        message=f"Crossed book: Bid {event.bid_price} > Ask {event.ask_price}",
                    )
                )

        # 4. Tick Size Alignment Check (e.g. multiples of 0.05)
        if inst_rec and event.last_traded_price is not None:
            tick = inst_rec.tick_size
            rem = (event.last_traded_price % tick)
            if rem != Decimal("0"):
                violations.append(
                    QualityViolation(
                        rule_id="MKT_INVALID_TICK_ALIGNMENT",
                        severity="WARNING",
                        entity_ref=event.event_id,
                        message=f"LTP {event.last_traded_price} is not a multiple of tick size {tick}",
                    )
                )

        # 5. Non-negative quantities
        for qty_field in ("bid_quantity", "ask_quantity", "last_traded_quantity", "cumulative_volume"):
            q_val: Optional[int] = getattr(event, qty_field)
            if q_val is not None and q_val < 0:
                violations.append(
                    QualityViolation(
                        rule_id="MKT_NEGATIVE_QUANTITY",
                        severity="FATAL",
                        entity_ref=event.event_id,
                        message=f"{qty_field} {q_val} is negative",
                    )
                )

        is_valid = not any(v.severity == "FATAL" for v in violations)
        return is_valid, violations

    def audit_batch(
        self,
        news_events: List[NewsEvent],
        market_events: List[MarketEvent],
    ) -> DataQualityReport:
        """Run batch quality audit and isolate non-compliant records."""
        report = DataQualityReport()
        report.total_records_audited = len(news_events) + len(market_events)

        # Audit News
        for ne in news_events:
            ok, viols = self.audit_news_event(ne)
            report.rule_violations.extend(viols)
            if ok:
                report.passed_count += 1
            else:
                report.quarantined_count += 1
                report.quarantine_records.append(
                    QualityQuarantineRecord(
                        source_type="NEWS",
                        source_reference=ne.event_id,
                        quarantine_reason="; ".join(v.message for v in viols if v.severity == "FATAL"),
                        raw_payload_snippet=ne.model_dump_json()[:300],
                    )
                )

        # Audit Market Data
        for me in market_events:
            ok, viols = self.audit_market_event(me)
            report.rule_violations.extend(viols)
            if ok:
                report.passed_count += 1
            else:
                report.quarantined_count += 1
                report.quarantine_records.append(
                    QualityQuarantineRecord(
                        source_type="MARKET_DATA",
                        source_reference=me.event_id,
                        quarantine_reason="; ".join(v.message for v in viols if v.severity == "FATAL"),
                        raw_payload_snippet=me.model_dump_json()[:300],
                    )
                )

        report.warning_count = sum(1 for v in report.rule_violations if v.severity == "WARNING")
        return report
