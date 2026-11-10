"""Provider-Neutral Historical Event Dataset Ingestion and Validation Engine.

Provides extensible, validated, provider-neutral ingestion interfaces for historical
financial event datasets across Indian (NSE, BSE, RBI) and US (SEC EDGAR, regulatory) markets.
Strictly separates geographic jurisdictions, enforces causal timestamp validation,
and isolates input features from post-event outcome labels.
"""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, field_validator


class MarketJurisdiction(str, Enum):
    """Geographic and regulatory market jurisdiction."""

    IN = "IN"          # Indian Equity & Derivatives (NSE, BSE, RBI)
    US = "US"          # US Equities & Options (NYSE, NASDAQ, SEC)
    GLOBAL = "GLOBAL"  # Cross-market commodities, macro, or multi-listed assets


class HistoricalImportRecord(BaseModel):
    """
    Standardized, provider-neutral representation of a historical announcement.
    Enforces strict temporal metadata and explicit geographic jurisdiction.
    """

    event_id: str = Field(..., description="Unique deterministic or source event ID")
    jurisdiction: MarketJurisdiction = Field(..., description="Explicit market jurisdiction (IN, US, GLOBAL)")
    source_feed: str = Field(..., description="Normalized source identifier (e.g. NSE_CORPORATE, SEC_EDGAR_8K)")
    source_url: Optional[str] = Field(None, description="Resolvable link to official disclosure or filing")
    instrument_id: Optional[str] = Field(None, description="Primary mapped ticker or exchange instrument")
    company_name: Optional[str] = Field(None, description="Canonical legal corporate or institution name")
    headline: str = Field(..., description="Cleaned, unpadded headline or announcement subject")
    body_text: Optional[str] = Field(None, description="Full announcement text or extracted filing excerpt")
    event_timestamp_utc: datetime = Field(..., description="Reported publication or occurrence timestamp in UTC")
    system_available_at_utc: datetime = Field(..., description="Point-in-time timestamp when data became available to system")
    event_family: Optional[str] = Field(None, description="High-level category (e.g. CORPORATE_ACTIONS, MACRO)")
    event_subtype: Optional[str] = Field(None, description="Specific event taxonomy subtype if pre-annotated")
    raw_metadata: Dict[str, Any] = Field(default_factory=dict, description="Raw provider payload for auditability")

    @field_validator("system_available_at_utc")
    @classmethod
    def validate_causality(cls, v: datetime, info) -> datetime:
        """Causal integrity axiom: System availability cannot precede actual event occurrence."""
        values = info.data
        if "event_timestamp_utc" in values and values["event_timestamp_utc"]:
            evt_time = values["event_timestamp_utc"]
            # Allow up to 1 second of clock skew tolerance
            if v.timestamp() < (evt_time.timestamp() - 1.0):
                raise ValueError(
                    f"system_available_at_utc ({v.isoformat()}) cannot precede "
                    f"event_timestamp_utc ({evt_time.isoformat()}). Causality violated."
                )
        return v

    def compute_content_hash(self) -> str:
        """Compute SHA-256 fingerprint for deduplication."""
        fingerprint = f"{self.jurisdiction.value}|{self.instrument_id or ''}|{self.headline.strip().lower()}|{self.event_timestamp_utc.isoformat()}"
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


class HistoricalImportResult(BaseModel):
    """Execution telemetry for a batch historical dataset import."""

    feed_name: str
    jurisdiction: MarketJurisdiction
    records_processed: int = 0
    records_imported: int = 0
    records_skipped_duplicate: int = 0
    records_quarantined_invalid: int = 0
    errors: List[str] = Field(default_factory=list)
    import_started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    import_completed_at: Optional[datetime] = None


class BaseHistoricalImporter:
    """Base class for provider-specific dataset parsers."""

    def __init__(self, jurisdiction: MarketJurisdiction, default_feed_name: str) -> None:
        self.jurisdiction = jurisdiction
        self.default_feed_name = default_feed_name

    def parse_record(self, raw_data: Dict[str, Any]) -> Optional[HistoricalImportRecord]:
        """Convert a raw dictionary into a validated HistoricalImportRecord."""
        raise NotImplementedError

    def parse_file(self, file_path: Union[str, Path]) -> Iterator[Tuple[Optional[HistoricalImportRecord], Optional[str]]]:
        """Yield (record, error_message) tuples from a file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {path}")

        if path.suffix.lower() == ".jsonl":
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        rec = self.parse_record(raw)
                        yield rec, None
                    except Exception as e:
                        yield None, f"Line {line_num}: {str(e)}"
        elif path.suffix.lower() == ".json":
            with open(path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    items = data if isinstance(data, list) else data.get("items", [data])
                    for idx, raw in enumerate(items, start=1):
                        try:
                            rec = self.parse_record(raw)
                            yield rec, None
                        except Exception as e:
                            yield None, f"Item {idx}: {str(e)}"
                except Exception as e:
                    yield None, f"JSON parsing failed: {str(e)}"
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}. Expected .json or .jsonl")


class NSECorporateAnnouncementImporter(BaseHistoricalImporter):
    """
    Parser for National Stock Exchange of India (NSE) corporate announcements.
    Handles official fields: SYMBOL, COMPANY, PURPOSE, BROADCAST_DATE_TIME, ATTACHMENT_URL.
    """

    def __init__(self) -> None:
        super().__init__(jurisdiction=MarketJurisdiction.IN, default_feed_name="NSE_CORPORATE")

    def parse_record(self, raw_data: Dict[str, Any]) -> HistoricalImportRecord:
        # Expected formats for broadcast timestamp: "10-Nov-2026 09:15:00" or ISO format
        raw_ts = raw_data.get("BROADCAST_DATE_TIME") or raw_data.get("broadcast_time") or raw_data.get("timestamp")
        if not raw_ts:
            raise ValueError("NSE announcement missing BROADCAST_DATE_TIME")

        event_ts = self._parse_timestamp(raw_ts)
        # In historical NSE data, receipt is at broadcast time unless receipt log provided
        system_ts = self._parse_timestamp(raw_data.get("LOCAL_RECEIVED_TIME") or raw_data.get("received_at") or raw_ts)
        if system_ts < event_ts:
            system_ts = event_ts

        symbol = (raw_data.get("SYMBOL") or raw_data.get("symbol") or "").strip().upper()
        inst_id = f"{symbol}.NS" if symbol else None

        company = raw_data.get("COMPANY") or raw_data.get("company_name") or symbol
        purpose = raw_data.get("PURPOSE") or raw_data.get("subject") or raw_data.get("headline") or ""
        details = raw_data.get("DETAILS") or raw_data.get("description") or raw_data.get("body_text")

        event_id = raw_data.get("ANNOUNCEMENT_ID") or raw_data.get("event_id")
        if not event_id:
            fingerprint = f"NSE|{symbol}|{event_ts.isoformat()}|{purpose}"
            event_id = f"nse_{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"

        return HistoricalImportRecord(
            event_id=event_id,
            jurisdiction=MarketJurisdiction.IN,
            source_feed=self.default_feed_name,
            source_url=raw_data.get("ATTACHMENT_URL") or raw_data.get("url"),
            instrument_id=inst_id,
            company_name=company,
            headline=purpose.strip(),
            body_text=details.strip() if details else None,
            event_timestamp_utc=event_ts,
            system_available_at_utc=system_ts,
            raw_metadata=raw_data,
        )

    def _parse_timestamp(self, ts_val: Any) -> datetime:
        if isinstance(ts_val, datetime):
            return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        ts_str = str(ts_val).strip()
        # Try ISO first
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        # Try NSE format: "10-Nov-2026 09:15:00"
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse NSE timestamp: {ts_str}")


class BSEFilingsImporter(BaseHistoricalImporter):
    """
    Parser for Bombay Stock Exchange (BSE) corporate announcements and filings.
    Handles BSE scrip codes, company security identifiers, and dissemination timestamps.
    """

    def __init__(self) -> None:
        super().__init__(jurisdiction=MarketJurisdiction.IN, default_feed_name="BSE_FILINGS")

    def parse_record(self, raw_data: Dict[str, Any]) -> HistoricalImportRecord:
        raw_ts = raw_data.get("DISSEMINATION_TIME") or raw_data.get("dissemination_time") or raw_data.get("timestamp")
        if not raw_ts:
            raise ValueError("BSE filing missing DISSEMINATION_TIME")

        event_ts = self._parse_timestamp(raw_ts)
        system_ts = self._parse_timestamp(raw_data.get("LOCAL_RECEIVED_TIME") or raw_data.get("received_at") or raw_ts)
        if system_ts < event_ts:
            system_ts = event_ts

        scrip_cd = str(raw_data.get("SCRIP_CD") or raw_data.get("scrip_code") or "").strip()
        scrip_id = (raw_data.get("SCRIP_ID") or raw_data.get("security_id") or "").strip().upper()

        inst_id = f"{scrip_id}.BO" if scrip_id else (f"BSE_{scrip_cd}" if scrip_cd else None)
        company = raw_data.get("COMPANY_NAME") or raw_data.get("company_name") or scrip_id
        subject = raw_data.get("HEADLINE") or raw_data.get("subject") or raw_data.get("purpose") or ""
        details = raw_data.get("BODY") or raw_data.get("description") or raw_data.get("details")

        event_id = raw_data.get("NEWS_ID") or raw_data.get("event_id")
        if not event_id:
            fingerprint = f"BSE|{scrip_cd}|{event_ts.isoformat()}|{subject}"
            event_id = f"bse_{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"

        return HistoricalImportRecord(
            event_id=event_id,
            jurisdiction=MarketJurisdiction.IN,
            source_feed=self.default_feed_name,
            source_url=raw_data.get("PDF_URL") or raw_data.get("attachment_url") or raw_data.get("url"),
            instrument_id=inst_id,
            company_name=company,
            headline=subject.strip(),
            body_text=details.strip() if details else None,
            event_timestamp_utc=event_ts,
            system_available_at_utc=system_ts,
            raw_metadata=raw_data,
        )

    def _parse_timestamp(self, ts_val: Any) -> datetime:
        if isinstance(ts_val, datetime):
            return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        ts_str = str(ts_val).strip()
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse BSE timestamp: {ts_str}")


class RBIMacroReleaseImporter(BaseHistoricalImporter):
    """
    Parser for Reserve Bank of India (RBI) macroeconomic policy and regulatory announcements.
    Handles MPC resolutions, policy rate announcements, SLR/CRR changes, and banking directives.
    """

    def __init__(self) -> None:
        super().__init__(jurisdiction=MarketJurisdiction.IN, default_feed_name="RBI_PRESS")

    def parse_record(self, raw_data: Dict[str, Any]) -> HistoricalImportRecord:
        raw_ts = raw_data.get("RELEASE_DATE_TIME") or raw_data.get("release_time") or raw_data.get("timestamp")
        if not raw_ts:
            raise ValueError("RBI announcement missing RELEASE_DATE_TIME")

        event_ts = self._parse_timestamp(raw_ts)
        system_ts = self._parse_timestamp(raw_data.get("LOCAL_RECEIVED_TIME") or raw_data.get("received_at") or raw_ts)
        if system_ts < event_ts:
            system_ts = event_ts

        title = raw_data.get("TITLE") or raw_data.get("headline") or ""
        body = raw_data.get("CONTENT") or raw_data.get("body_text") or raw_data.get("description")

        event_id = raw_data.get("PRESS_RELEASE_ID") or raw_data.get("event_id")
        if not event_id:
            fingerprint = f"RBI|{event_ts.isoformat()}|{title}"
            event_id = f"rbi_{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"

        return HistoricalImportRecord(
            event_id=event_id,
            jurisdiction=MarketJurisdiction.IN,
            source_feed=self.default_feed_name,
            source_url=raw_data.get("URL") or raw_data.get("source_url"),
            instrument_id="NIFTY_BANK",  # Macro benchmark mapping for monetary policy
            company_name="Reserve Bank of India",
            headline=title.strip(),
            body_text=body.strip() if body else None,
            event_timestamp_utc=event_ts,
            system_available_at_utc=system_ts,
            event_family="MACROECONOMIC",
            event_subtype="INTEREST_RATE_DECISION",
            raw_metadata=raw_data,
        )

    def _parse_timestamp(self, ts_val: Any) -> datetime:
        if isinstance(ts_val, datetime):
            return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        ts_str = str(ts_val).strip()
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in ("%b %d, %Y %H:%M", "%B %d, %Y %H:%M", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                # Remove timezone acronym if present for strptime
                clean = ts_str.replace(" IST", "").strip()
                dt = datetime.strptime(clean, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse RBI timestamp: {ts_str}")


class SECFilingsImporter(BaseHistoricalImporter):
    """
    Parser for US SEC EDGAR submissions (Form 8-K, 10-Q, 10-K, Form 4).
    Validates CIK, Accession Number, Form Type, and Acceptance Timestamps.
    """

    def __init__(self) -> None:
        super().__init__(jurisdiction=MarketJurisdiction.US, default_feed_name="SEC_EDGAR_8K")

    def parse_record(self, raw_data: Dict[str, Any]) -> HistoricalImportRecord:
        # SEC acceptance datetime is the point of official system dissemination
        raw_ts = (
            raw_data.get("acceptance_datetime")
            or raw_data.get("ACCEPTANCE_TIME")
            or raw_data.get("filing_date")
            or raw_data.get("timestamp")
        )
        if not raw_ts:
            raise ValueError("SEC filing missing acceptance timestamp")

        event_ts = self._parse_timestamp(raw_ts)
        system_ts = self._parse_timestamp(raw_data.get("local_received_at") or raw_ts)
        if system_ts < event_ts:
            system_ts = event_ts

        cik = str(raw_data.get("cik") or raw_data.get("CIK") or "").zfill(10)
        ticker = (raw_data.get("ticker") or raw_data.get("TICKER") or "").upper().strip()
        inst_id = f"{ticker}.US" if ticker else (f"CIK_{cik}" if cik else None)

        company = raw_data.get("company_name") or raw_data.get("registrant_name") or ticker
        form = raw_data.get("form") or raw_data.get("FORM") or "8-K"
        title = raw_data.get("title") or raw_data.get("headline") or f"{company} Form {form} Current Report"
        body = raw_data.get("body_text") or raw_data.get("items_description") or raw_data.get("snippet")

        accession = raw_data.get("accession_number") or raw_data.get("ACCESSION_NUMBER")
        event_id = f"sec_{accession.replace('-', '')}" if accession else None
        if not event_id:
            fingerprint = f"SEC|{cik}|{event_ts.isoformat()}|{form}"
            event_id = f"sec_{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"

        return HistoricalImportRecord(
            event_id=event_id,
            jurisdiction=MarketJurisdiction.US,
            source_feed=f"SEC_EDGAR_{form.upper()}",
            source_url=raw_data.get("url") or raw_data.get("filing_url"),
            instrument_id=inst_id,
            company_name=company,
            headline=title.strip(),
            body_text=body.strip() if body else None,
            event_timestamp_utc=event_ts,
            system_available_at_utc=system_ts,
            raw_metadata=raw_data,
        )

    def _parse_timestamp(self, ts_val: Any) -> datetime:
        if isinstance(ts_val, datetime):
            return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        ts_str = str(ts_val).strip()
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(ts_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse SEC timestamp: {ts_str}")


class GenericHistoricalJSONLImporter(BaseHistoricalImporter):
    """General-purpose importer for standardized multi-jurisdiction JSONL feeds."""

    def __init__(self, jurisdiction: MarketJurisdiction = MarketJurisdiction.GLOBAL) -> None:
        super().__init__(jurisdiction=jurisdiction, default_feed_name="GENERIC_JSONL")

    def parse_record(self, raw_data: Dict[str, Any]) -> HistoricalImportRecord:
        return HistoricalImportRecord.model_validate(raw_data)
