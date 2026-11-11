"""Live Market Instrument Configuration & Dynamic Contract Resolver (Phase 6 WP-C).

Extends Phase 2 `InstrumentMaster` to support:
- Configurable Indian cash equities, single-stock futures, index futures, and contextual benchmarks
  (prioritizing NIFTY, BANKNIFTY, RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK, LT + liquid NIFTY 50 names).
- Non-hardcoded, date-validated contract resolution checking expiry schedules, rollover windows,
  lot size, tick size, session definitions, and explicit `EXPIRED` / `PRE_ROLLOVER` / `ACTIVE` / `HALTED` status.
- Clean separation of Cash Equity, Single-Stock Futures, Index Futures, and Contextual Benchmark mappings
  plus corporate vs macroeconomic event routing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from backend.alpha.instruments import (
    Exchange,
    InstrumentMappingRecord,
    InstrumentMaster,
    InstrumentMasterRecord,
    InstrumentType,
    RelevanceTier,
    Segment,
    VerificationStatus,
)

INSTRUMENT_MASTER_VERSION = "6.0.0-alpha"
IST_ZONE = ZoneInfo("Asia/Kolkata")


class LiveInstrumentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PRE_ROLLOVER = "PRE_ROLLOVER"
    EXPIRED = "EXPIRED"
    HALTED = "HALTED"
    SUSPENDED = "SUSPENDED"


class ContractMetadataEntry(BaseModel):
    """Exchange/broker-sourced futures contract specification entry."""

    underlying: str
    segment: Segment
    exchange: Exchange = Exchange.NSE
    trading_symbol: str                  # e.g. NIFTY26NOVFUT (resolved at runtime, not stale static)
    contract_token: Optional[str] = None
    expiry_date: date
    lot_size: int = Field(..., gt=0)
    tick_size: Decimal = Field(default=Decimal("0.05"), gt=Decimal("0"))
    trading_session: str = "REGULAR_0915_1530"
    rollover_days_before_expiry: int = Field(default=2, ge=0)
    is_halted: bool = False
    is_suspended: bool = False
    source_ledger: str = "EXCHANGE_MASTER"


class ResolvedContractState(BaseModel):
    """Point-in-time contract state resolved for `as_of_date`."""

    canonical_instrument_id: str
    underlying: str
    segment: Segment
    active_contract_symbol: str
    active_contract_token: Optional[str] = None
    expiry_date: date
    days_to_expiry: int
    lot_size: int
    tick_size: Decimal
    trading_session: str
    status: LiveInstrumentStatus
    rollover_target_symbol: Optional[str] = None
    rollover_target_expiry: Optional[date] = None
    source_ledger: str


class SeparatedInstrumentMappings(BaseModel):
    """Explicit separation between cash equities, SSFs, index futures, and contextual benchmarks."""

    cash_equity: List[InstrumentMappingRecord] = Field(default_factory=list)
    single_stock_futures: List[InstrumentMappingRecord] = Field(default_factory=list)
    index_futures: List[InstrumentMappingRecord] = Field(default_factory=list)
    contextual_benchmarks: List[InstrumentMappingRecord] = Field(default_factory=list)

    def all_mappings(self) -> List[InstrumentMappingRecord]:
        return (
            list(self.cash_equity)
            + list(self.single_stock_futures)
            + list(self.index_futures)
            + list(self.contextual_benchmarks)
        )


DEFAULT_LOT_SIZES: Dict[str, int] = {
    "NIFTY": 75,
    "BANKNIFTY": 30,
    "RELIANCE": 500,
    "TCS": 175,
    "INFY": 400,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "LT": 150,
    "SBIN": 750,
    "BHARTIARTL": 475,
    "AXISBANK": 625,
    "KOTAKBANK": 400,
    "ITC": 1600,
}

DEFAULT_NIFTY_WEIGHTS: Dict[str, float] = {
    "HDFCBANK": 0.115,
    "RELIANCE": 0.095,
    "ICICIBANK": 0.082,
    "INFY": 0.058,
    "TCS": 0.038,
    "LT": 0.041,
    "SBIN": 0.032,
    "BHARTIARTL": 0.042,
    "AXISBANK": 0.031,
    "KOTAKBANK": 0.025,
    "ITC": 0.041,
}


def compute_last_weekday_of_month(year: int, month: int, target_weekday: int = 3) -> date:
    """Compute last weekday (0=Mon .. 3=Thu) in `(year, month)`."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - target_weekday) % 7
    return last_day - timedelta(days=offset)


def generate_rolling_monthly_contracts(
    underlying: str,
    segment: Segment,
    as_of_date: date,
    lot_size: int,
    expiry_weekday: int = 3,
    months_ahead: int = 3,
) -> List[ContractMetadataEntry]:
    """
    Generate upcoming non-expired calendar contract entries relative to `as_of_date`
    when an external exchange instrument dump has not yet overridden symbols.
    Explicitly labeled `CALENDAR_DERIVED_PROVISIONAL` rather than claiming static vendor tokens.
    """
    entries: List[ContractMetadataEntry] = []
    cur_y, cur_m = as_of_date.year, as_of_date.month
    collected = 0
    for step in range(months_ahead + 2):
        m_total = (cur_m - 1) + step
        y = cur_y + m_total // 12
        m = (m_total % 12) + 1
        exp = compute_last_weekday_of_month(y, m, target_weekday=expiry_weekday)
        if exp < as_of_date:
            continue
        mon_tag = exp.strftime("%y%b").upper()
        entries.append(
            ContractMetadataEntry(
                underlying=underlying,
                segment=segment,
                trading_symbol=f"{underlying}{mon_tag}FUT",
                contract_token=None,
                expiry_date=exp,
                lot_size=lot_size,
                source_ledger="CALENDAR_DERIVED_PROVISIONAL",
            )
        )
        collected += 1
        if collected >= months_ahead:
            break
    return entries


class LiveInstrumentUniverse:
    """Configurable, rollover-aware live research universe synchronized with InstrumentMaster."""

    PRIORITIZED_UNDERLYINGS: tuple[str, ...] = (
        "RELIANCE",
        "TCS",
        "INFY",
        "HDFCBANK",
        "ICICIBANK",
        "LT",
        "SBIN",
        "BHARTIARTL",
        "AXISBANK",
        "KOTAKBANK",
        "ITC",
    )

    def __init__(
        self,
        additional_underlyings: Optional[Sequence[str]] = None,
        contract_entries: Optional[Sequence[ContractMetadataEntry]] = None,
        as_of_date: Optional[date] = None,
        halted_underlyings: Optional[Sequence[str]] = None,
    ) -> None:
        self.reference_date = as_of_date or datetime.now(IST_ZONE).date()
        self.version = INSTRUMENT_MASTER_VERSION
        self.underlyings = list(dict.fromkeys(list(self.PRIORITIZED_UNDERLYINGS) + list(additional_underlyings or ())))
        self._halted = {u.upper() for u in (halted_underlyings or ())}
        self._contracts_by_key: Dict[tuple[str, Segment], List[ContractMetadataEntry]] = {}
        self.instrument_master = InstrumentMaster()

        if contract_entries:
            for entry in contract_entries:
                self.register_contract_entry(entry)
        else:
            self._populate_default_contracts(self.reference_date)

        self._sync_to_instrument_master(self.reference_date)

    def register_contract_entry(self, entry: ContractMetadataEntry) -> None:
        key = (entry.underlying.upper(), entry.segment)
        lst = self._contracts_by_key.setdefault(key, [])
        lst.append(entry)
        lst.sort(key=lambda c: c.expiry_date)

    def _populate_default_contracts(self, ref_date: date) -> None:
        for idx_sym in ("NIFTY", "BANKNIFTY"):
            for entry in generate_rolling_monthly_contracts(
                idx_sym, Segment.FUTIDX, ref_date, lot_size=DEFAULT_LOT_SIZES[idx_sym]
            ):
                self.register_contract_entry(entry)

        for sym in self.underlyings:
            lot = DEFAULT_LOT_SIZES.get(sym, 100)
            for entry in generate_rolling_monthly_contracts(sym, Segment.FUTSTK, ref_date, lot_size=lot):
                self.register_contract_entry(entry)

    def _sync_to_instrument_master(self, ref_date: date) -> None:
        """Populate cash equities, SSFs, index futures, and contextual benchmarks cleanly."""
        # Cash Equities
        for sym in self.underlyings:
            self.instrument_master.register(
                InstrumentMasterRecord(
                    instrument_id=f"NSE:{sym}:EQ",
                    exchange=Exchange.NSE,
                    segment=Segment.EQ,
                    symbol=sym,
                    underlying=sym,
                    instrument_type=InstrumentType.EQUITY_CASH,
                    lot_size=1,
                    tick_size=Decimal("0.05"),
                    quote_currency="INR",
                    source_metadata="LIVE_UNIVERSE_CASH_V6",
                    verification_status=VerificationStatus.VERIFIED_SPEC,
                )
            )
            try:
                fut = self.resolve_active_contract(sym, Segment.FUTSTK, ref_date)
                self.instrument_master.register(
                    InstrumentMasterRecord(
                        instrument_id=fut.canonical_instrument_id,
                        exchange=Exchange.NSE,
                        segment=Segment.FUTSTK,
                        symbol=fut.active_contract_symbol,
                        underlying=sym,
                        contract_id=fut.active_contract_token,
                        instrument_type=InstrumentType.STOCK_FUTURE,
                        expiry_date=fut.expiry_date.isoformat(),
                        lot_size=fut.lot_size,
                        tick_size=fut.tick_size,
                        quote_currency="INR",
                        source_metadata=fut.source_ledger,
                        verification_status=(
                            VerificationStatus.VERIFIED_SPEC
                            if fut.source_ledger != "CALENDAR_DERIVED_PROVISIONAL"
                            else VerificationStatus.PROVISIONAL
                        ),
                    )
                )
            except ValueError:
                pass

        # Index Futures + Benchmarks
        for idx_sym in ("NIFTY", "BANKNIFTY"):
            try:
                fut = self.resolve_active_contract(idx_sym, Segment.FUTIDX, ref_date)
                self.instrument_master.register(
                    InstrumentMasterRecord(
                        instrument_id=fut.canonical_instrument_id,
                        exchange=Exchange.NSE,
                        segment=Segment.FUTIDX,
                        symbol=fut.active_contract_symbol,
                        underlying=idx_sym,
                        contract_id=fut.active_contract_token,
                        instrument_type=InstrumentType.INDEX_FUTURE,
                        expiry_date=fut.expiry_date.isoformat(),
                        lot_size=fut.lot_size,
                        tick_size=fut.tick_size,
                        quote_currency="INR",
                        source_metadata=fut.source_ledger,
                        verification_status=(
                            VerificationStatus.VERIFIED_SPEC
                            if fut.source_ledger != "CALENDAR_DERIVED_PROVISIONAL"
                            else VerificationStatus.PROVISIONAL
                        ),
                    )
                )
            except ValueError:
                pass

            self.instrument_master.register(
                InstrumentMasterRecord(
                    instrument_id=f"NSE:{idx_sym}:INDEX",
                    exchange=Exchange.NSE,
                    segment=Segment.EQ,
                    symbol=idx_sym,
                    underlying=idx_sym,
                    instrument_type=InstrumentType.INDEX_CASH,
                    lot_size=1,
                    tick_size=Decimal("0.05"),
                    quote_currency="INR",
                    source_metadata="CONTEXTUAL_INDEX_BENCHMARK",
                    verification_status=VerificationStatus.VERIFIED_SPEC,
                )
            )

    def resolve_active_contract(
        self,
        underlying: str,
        segment: Segment,
        as_of_date: Optional[date] = None,
    ) -> ResolvedContractState:
        """
        Resolve active non-expired contract dynamically from registered ledger/schedule.
        Raises `ValueError` if all known contracts for `(underlying, segment)` are expired.
        """
        sym = underlying.upper().strip()
        check_date = as_of_date or self.reference_date
        entries = self._contracts_by_key.get((sym, segment), [])
        non_expired = [e for e in entries if e.expiry_date >= check_date]
        if not non_expired:
            raise ValueError(
                f"No non-expired contract available for {sym} ({segment.value}) as of {check_date.isoformat()}"
            )

        near = non_expired[0]
        next_c = non_expired[1] if len(non_expired) > 1 else None
        days_left = (near.expiry_date - check_date).days

        if sym in self._halted or near.is_halted:
            status = LiveInstrumentStatus.HALTED
        elif near.is_suspended:
            status = LiveInstrumentStatus.SUSPENDED
        elif days_left <= near.rollover_days_before_expiry:
            status = LiveInstrumentStatus.PRE_ROLLOVER
        else:
            status = LiveInstrumentStatus.ACTIVE

        canonical_id = f"NSE:{sym}_FUT:CURRENT"
        return ResolvedContractState(
            canonical_instrument_id=canonical_id,
            underlying=sym,
            segment=segment,
            active_contract_symbol=near.trading_symbol,
            active_contract_token=near.contract_token,
            expiry_date=near.expiry_date,
            days_to_expiry=days_left,
            lot_size=near.lot_size,
            tick_size=near.tick_size,
            trading_session=near.trading_session,
            status=status,
            rollover_target_symbol=next_c.trading_symbol if next_c else None,
            rollover_target_expiry=next_c.expiry_date if next_c else None,
            source_ledger=near.source_ledger,
        )

    def validate_contract_not_expired(
        self, underlying: str, segment: Segment, expiry_date: date, as_of_date: Optional[date] = None
    ) -> LiveInstrumentStatus:
        check_date = as_of_date or self.reference_date
        if expiry_date < check_date:
            return LiveInstrumentStatus.EXPIRED
        if (expiry_date - check_date).days <= 2:
            return LiveInstrumentStatus.PRE_ROLLOVER
        return LiveInstrumentStatus.ACTIVE

    def map_event_separated(
        self,
        entity_id: Optional[str],
        event_family: str,
        tickers: Optional[List[str]] = None,
    ) -> SeparatedInstrumentMappings:
        """
        Route corporate announcements primarily to directly affected cash equity & SSF,
        macro/central-bank announcements to index futures, and always separate mappings cleanly.
        """
        norm_ent = (entity_id or "").upper().strip()
        fam = event_family.upper().strip()
        out = SeparatedInstrumentMappings()

        # Always keep spot index benchmarks clearly contextual
        out.contextual_benchmarks.append(
            InstrumentMappingRecord(
                instrument_id="NSE:NIFTY:INDEX",
                relevance=RelevanceTier.CONTEXTUAL,
                weight=1.0,
                notes="Spot NIFTY 50 contextual benchmark index",
            )
        )

        if fam in ("MACRO", "MACROECONOMIC") or norm_ent in ("RBI", "FINMIN", "MOSPI", "GOI", "INDIA_MACRO"):
            out.index_futures.append(
                InstrumentMappingRecord(
                    instrument_id="NSE:NIFTY_FUT:CURRENT",
                    relevance=RelevanceTier.DIRECT,
                    weight=1.0,
                    notes="Direct macro/policy exposure via NIFTY 50 futures",
                )
            )
            out.index_futures.append(
                InstrumentMappingRecord(
                    instrument_id="NSE:BANKNIFTY_FUT:CURRENT",
                    relevance=RelevanceTier.CONTEXTUAL,
                    weight=0.75,
                    notes="Banking rate-sensitive futures benchmark",
                )
            )
            return out

        alias_map = {
            "RELIANCE INDUSTRIES": "RELIANCE",
            "RIL": "RELIANCE",
            "TATA CONSULTANCY SERVICES": "TCS",
            "INFOSYS": "INFY",
            "HDFC BANK": "HDFCBANK",
            "ICICI BANK": "ICICIBANK",
            "LARSEN & TOUBRO": "LT",
            "LARSEN AND TOUBRO": "LT",
            "L&T": "LT",
            "STATE BANK OF INDIA": "SBIN",
            "BHARTI AIRTEL": "BHARTIARTL",
            "AXIS BANK": "AXISBANK",
            "KOTAK MAHINDRA BANK": "KOTAKBANK",
        }
        matched: Optional[str] = alias_map.get(norm_ent, norm_ent if norm_ent in self.underlyings else None)
        if matched is None and tickers:
            for t in tickers:
                c = t.upper().replace(".NS", "").replace(".BO", "").strip()
                if c in self.underlyings:
                    matched = c
                    break

        if matched:
            out.cash_equity.append(
                InstrumentMappingRecord(
                    instrument_id=f"NSE:{matched}:EQ",
                    relevance=RelevanceTier.DIRECT,
                    weight=1.0,
                    notes=f"Direct corporate cash equity for {matched}",
                )
            )
            out.single_stock_futures.append(
                InstrumentMappingRecord(
                    instrument_id=f"NSE:{matched}_FUT:CURRENT",
                    relevance=RelevanceTier.DIRECT,
                    weight=1.0,
                    notes=f"Direct corporate single-stock future for {matched}",
                )
            )
            w = DEFAULT_NIFTY_WEIGHTS.get(matched, 0.02)
            out.index_futures.append(
                InstrumentMappingRecord(
                    instrument_id="NSE:NIFTY_FUT:CURRENT",
                    relevance=RelevanceTier.CONTEXTUAL,
                    weight=w,
                    notes=f"Contextual index benchmark ({round(w * 100, 1)}% index weight)",
                )
            )
            return out

        out.index_futures.append(
            InstrumentMappingRecord(
                instrument_id="NSE:NIFTY_FUT:CURRENT",
                relevance=RelevanceTier.CONTEXTUAL,
                weight=0.0,
                notes="Unmapped corporate issuer; contextual market tracking only",
            )
        )
        return out

    def universe_digest(self) -> str:
        payload = {
            "version": self.version,
            "reference_date": self.reference_date.isoformat(),
            "underlyings": self.underlyings,
            "halted": sorted(self._halted),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
