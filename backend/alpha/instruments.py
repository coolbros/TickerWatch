"""Instrument Master and Event-to-Instrument Mapping for TickerWatch Alpha."""

from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    US_REGULATORY = "US_REGULATORY"


class Segment(str, Enum):
    EQ = "EQ"              # Cash Equity
    FUTIDX = "FUTIDX"      # Index Futures
    FUTSTK = "FUTSTK"      # Stock Futures
    OPTIDX = "OPTIDX"      # Index Options
    OPTSTK = "OPTSTK"      # Stock Options


class InstrumentType(str, Enum):
    INDEX_FUTURE = "INDEX_FUTURE"
    STOCK_FUTURE = "STOCK_FUTURE"
    EQUITY_CASH = "EQUITY_CASH"
    INDEX_CASH = "INDEX_CASH"


class RelevanceTier(str, Enum):
    DIRECT = "DIRECT"              # Primary target of event flow
    CONTEXTUAL = "CONTEXTUAL"      # Systematic index / macro benchmark
    INDIRECT = "INDIRECT"          # Peer or supply chain sympathy asset
    NOT_RELEVANT = "NOT_RELEVANT"  # Explicitly excluded


class VerificationStatus(str, Enum):
    VERIFIED_SPEC = "VERIFIED_SPEC"
    PROVISIONAL = "PROVISIONAL"
    EXPIRED = "EXPIRED"


class InstrumentMasterRecord(BaseModel):
    """Normalized specification for a tradable or observable financial instrument."""

    instrument_id: str = Field(..., description="Canonical ID e.g. NSE:NIFTY_FUT:CURRENT")
    exchange: Exchange
    segment: Segment
    symbol: str = Field(..., description="Trading symbol, e.g. NIFTY, RELIANCE")
    underlying: str = Field(..., description="Underlying asset key")
    contract_id: Optional[str] = Field(None, description="Vendor token or exchange contract code")
    instrument_type: InstrumentType
    expiry_date: Optional[str] = Field(None, description="Contract expiry ISO YYYY-MM-DD or None for cash")
    strike_price: Optional[Decimal] = Field(None, description="Strike price if option, else None")
    tick_size: Decimal = Field(default=Decimal("0.05"), description="Minimum price increment")
    lot_size: int = Field(default=1, description="Shares per contract lot")
    quote_currency: str = Field(default="INR", description="Quote currency ISO code")
    trading_session: str = Field(default="REGULAR_0915_1530", description="Trading session definition")
    source_metadata: str = Field(default="STATIC_VERIFIED", description="Origin of contract specs")
    verification_status: VerificationStatus = Field(default=VerificationStatus.VERIFIED_SPEC)

    @field_validator("tick_size")
    @classmethod
    def validate_positive_tick(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("tick_size must be strictly positive")
        return v

    @field_validator("lot_size")
    @classmethod
    def validate_positive_lot(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("lot_size must be >= 1")
        return v


class InstrumentMappingRecord(BaseModel):
    """Defines how a financial news event links to a specific instrument."""

    instrument_id: str
    relevance: RelevanceTier
    weight: float = Field(default=1.0, description="Normalized weight or index beta weight")
    notes: Optional[str] = None


class InstrumentMaster:
    """In-memory instrument registry with dynamic mapping and resolution."""

    def __init__(self) -> None:
        self._instruments: Dict[str, InstrumentMasterRecord] = {}
        self._load_default_universe()

    def register(self, record: InstrumentMasterRecord) -> None:
        """Register or update an instrument definition."""
        self._instruments[record.instrument_id] = record

    def get(self, instrument_id: str) -> Optional[InstrumentMasterRecord]:
        """Fetch instrument specification by canonical ID."""
        return self._instruments.get(instrument_id)

    def list_all(self) -> List[InstrumentMasterRecord]:
        """Return all registered instruments."""
        return list(self._instruments.values())

    def _load_default_universe(self) -> None:
        """Load approved Phase 2 initial research universe."""
        defaults = [
            InstrumentMasterRecord(
                instrument_id="NSE:NIFTY_FUT:CURRENT",
                exchange=Exchange.NSE,
                segment=Segment.FUTIDX,
                symbol="NIFTY",
                underlying="NIFTY",
                contract_id="256265",
                instrument_type=InstrumentType.INDEX_FUTURE,
                lot_size=25,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_FUTIDX_SPEC_2026",
            ),
            InstrumentMasterRecord(
                instrument_id="NSE:BANKNIFTY_FUT:CURRENT",
                exchange=Exchange.NSE,
                segment=Segment.FUTIDX,
                symbol="BANKNIFTY",
                underlying="BANKNIFTY",
                contract_id="260105",
                instrument_type=InstrumentType.INDEX_FUTURE,
                lot_size=15,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_FUTIDX_SPEC_2026",
            ),
            InstrumentMasterRecord(
                instrument_id="NSE:RELIANCE:EQ",
                exchange=Exchange.NSE,
                segment=Segment.EQ,
                symbol="RELIANCE",
                underlying="RELIANCE",
                contract_id="738561",
                instrument_type=InstrumentType.EQUITY_CASH,
                lot_size=1,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_EQUITY_SPEC_2026",
            ),
            InstrumentMasterRecord(
                instrument_id="NSE:RELIANCE_FUT:CURRENT",
                exchange=Exchange.NSE,
                segment=Segment.FUTSTK,
                symbol="RELIANCE",
                underlying="RELIANCE",
                contract_id="288513",
                instrument_type=InstrumentType.STOCK_FUTURE,
                lot_size=250,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_FUTSTK_SPEC_2026",
            ),
            InstrumentMasterRecord(
                instrument_id="NSE:INFY:EQ",
                exchange=Exchange.NSE,
                segment=Segment.EQ,
                symbol="INFY",
                underlying="INFY",
                contract_id="408065",
                instrument_type=InstrumentType.EQUITY_CASH,
                lot_size=1,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_EQUITY_SPEC_2026",
            ),
            InstrumentMasterRecord(
                instrument_id="NSE:TCS:EQ",
                exchange=Exchange.NSE,
                segment=Segment.EQ,
                symbol="TCS",
                underlying="TCS",
                contract_id="2953217",
                instrument_type=InstrumentType.EQUITY_CASH,
                lot_size=1,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_EQUITY_SPEC_2026",
            ),
            InstrumentMasterRecord(
                instrument_id="NSE:HDFCBANK:EQ",
                exchange=Exchange.NSE,
                segment=Segment.EQ,
                symbol="HDFCBANK",
                underlying="HDFCBANK",
                contract_id="341249",
                instrument_type=InstrumentType.EQUITY_CASH,
                lot_size=1,
                tick_size=Decimal("0.05"),
                quote_currency="INR",
                source_metadata="NSE_EQUITY_SPEC_2026",
            ),
        ]
        for inst in defaults:
            self.register(inst)

    def resolve_event_mapping(
        self,
        entity_id: Optional[str],
        event_family: str,
        tickers: Optional[List[str]] = None,
    ) -> List[InstrumentMappingRecord]:
        """
        Derive structured multi-tier instrument mappings for a given financial news event.

        Args:
            entity_id: Canonical entity identifier (e.g. 'RELIANCE', 'RBI', 'INFY')
            event_family: Classification category ('MACRO', 'EARNINGS', 'CONTRACTS', 'GENERAL')
            tickers: Optional list of raw tickers extracted from news payload

        Returns:
            List of InstrumentMappingRecord categorized by relevance tier.
        """
        mappings: List[InstrumentMappingRecord] = []
        normalized_entity = (entity_id or "").upper().strip()
        fam = event_family.upper().strip()

        # 1. Macro Announcements: Target NIFTY 50 Futures directly
        if fam == "MACRO" or normalized_entity in ("RBI", "FINMIN", "INDIA_MACRO", "GOI"):
            mappings.append(
                InstrumentMappingRecord(
                    instrument_id="NSE:NIFTY_FUT:CURRENT",
                    relevance=RelevanceTier.DIRECT,
                    weight=1.0,
                    notes="Primary macroeconomic market benchmark",
                )
            )
            mappings.append(
                InstrumentMappingRecord(
                    instrument_id="NSE:BANKNIFTY_FUT:CURRENT",
                    relevance=RelevanceTier.CONTEXTUAL,
                    weight=0.7,
                    notes="Banking & rate-sensitive contextual benchmark",
                )
            )
            return mappings

        # 2. Company-Specific Events (Earnings, Major Contracts, Corporate Actions)
        # Check entity directly, or fall back to ticker references
        matched_symbol: Optional[str] = None
        if normalized_entity in ("RELIANCE", "RIL", "RELIANCE INDUSTRIES"):
            matched_symbol = "RELIANCE"
        elif normalized_entity in ("INFY", "INFOSYS"):
            matched_symbol = "INFY"
        elif normalized_entity in ("TCS", "TATA CONSULTANCY SERVICES"):
            matched_symbol = "TCS"
        elif normalized_entity in ("HDFCBANK", "HDFC BANK"):
            matched_symbol = "HDFCBANK"
        elif tickers:
            for t in tickers:
                clean_t = t.upper().replace(".NS", "").replace(".BO", "")
                if clean_t in ("RELIANCE", "INFY", "TCS", "HDFCBANK"):
                    matched_symbol = clean_t
                    break

        if matched_symbol:
            # Direct Cash Equity
            cash_id = f"NSE:{matched_symbol}:EQ"
            if self.get(cash_id):
                mappings.append(
                    InstrumentMappingRecord(
                        instrument_id=cash_id,
                        relevance=RelevanceTier.DIRECT,
                        weight=1.0,
                        notes="Direct underlying cash equity",
                    )
                )

            # Direct Single-Stock Future (if available)
            fut_id = f"NSE:{matched_symbol}_FUT:CURRENT"
            if self.get(fut_id):
                mappings.append(
                    InstrumentMappingRecord(
                        instrument_id=fut_id,
                        relevance=RelevanceTier.DIRECT,
                        weight=1.0,
                        notes="Direct single-stock future derivative",
                    )
                )

            # Systematic Contextual Benchmark: NIFTY Futures
            # Reliance weight ~9.5%, HDFCBANK ~11.5%, INFY ~5.8%, TCS ~3.8%
            weight_map = {"RELIANCE": 0.095, "HDFCBANK": 0.115, "INFY": 0.058, "TCS": 0.038}
            w = weight_map.get(matched_symbol, 0.05)
            mappings.append(
                InstrumentMappingRecord(
                    instrument_id="NSE:NIFTY_FUT:CURRENT",
                    relevance=RelevanceTier.CONTEXTUAL,
                    weight=w,
                    notes=f"Systemic market context (Index weight ~{round(w*100, 1)}%)",
                )
            )
            return mappings

        # 3. Unmapped / General Corporate Announcements:
        # Default to contextual observation only; do not invent direct mapping
        mappings.append(
            InstrumentMappingRecord(
                instrument_id="NSE:NIFTY_FUT:CURRENT",
                relevance=RelevanceTier.CONTEXTUAL,
                weight=0.0,
                notes="Unmapped corporate entity; broad market context only",
            )
        )
        return mappings


# Singleton InstrumentMaster instance
_instrument_master: Optional[InstrumentMaster] = None


def get_instrument_master() -> InstrumentMaster:
    """Return shared InstrumentMaster singleton."""
    global _instrument_master
    if _instrument_master is None:
        _instrument_master = InstrumentMaster()
    return _instrument_master
