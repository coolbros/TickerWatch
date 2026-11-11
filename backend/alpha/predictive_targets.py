"""Predictive Forecast Target & Label Engineering Schema (Phase 5 WP-D).

Separates Reaction labels (`PRE_EVENT_AS_OF`) from Executable labels (`POST_DECISION_EXECUTABLE`),
encodes explicit transaction-cost / slippage neutral (`NO_TRADE`) hurdles so economically
negligible moves are not forced into directional classes, and verifies horizon resolution
and maturity provenance prior to walk-forward training.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple
from pydantic import BaseModel, Field

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.outcomes import (
    BenchmarkAdjustmentMethod,
    EventOutcomeLabel,
    OutcomeDataQualityStatus,
    OutcomeHorizon,
    OutcomeLabelType,
)


TARGET_SCHEMA_VERSION = "5.0.0-alpha"

TRAINABLE_QUALITY_STATUSES = {
    OutcomeDataQualityStatus.VERIFIED,
    OutcomeDataQualityStatus.CORPORATE_ACTION_ADJUSTED,
    OutcomeDataQualityStatus.ROLLOVER_ADJUSTED,
}


class ActionableDirectionClass(int, Enum):
    """3-class economic label enforcing explicit neutral no-trade buffer."""

    SHORT_NEGATIVE = -1
    NEUTRAL_NO_TRADE = 0
    LONG_POSITIVE = 1


class CostAssumptionSpec(BaseModel):
    """Explicit transaction cost and friction model in basis points (round-trip or one-way)."""

    scenario_name: str = "IN_NIFTY_FUT_CONSERVATIVE"
    brokerage_bps_round_trip: float = 2.0
    exchange_sebi_clearing_bps_round_trip: float = 0.6
    stt_stamp_gst_bps_round_trip: float = 2.5
    default_slippage_bps_round_trip: float = 2.0
    uncertainty_hurdle_buffer_bps: float = 1.5  # Minimum net edge cushion above bare costs

    @classmethod
    def indian_nifty_fut_conservative(cls) -> "CostAssumptionSpec":
        """Conservative NIFTY Futures round-trip friction profile (all-in ~7.1 bps + 1.5 bps buffer)."""
        return cls()

    @classmethod
    def indian_equity_intraday_conservative(cls) -> "CostAssumptionSpec":
        """Conservative NSE large-cap equity intraday/swing profile (~10.5 bps base + 2.0 bps buffer)."""
        return cls(
            scenario_name="IN_NSE_EQUITY_CONSERVATIVE",
            brokerage_bps_round_trip=4.0,
            exchange_sebi_clearing_bps_round_trip=0.8,
            stt_stamp_gst_bps_round_trip=3.2,
            default_slippage_bps_round_trip=2.5,
            uncertainty_hurdle_buffer_bps=2.0,
        )

    @classmethod
    def us_equity_conservative(cls) -> "CostAssumptionSpec":
        return cls(
            scenario_name="US_EQUITY_CONSERVATIVE",
            brokerage_bps_round_trip=1.0,
            exchange_sebi_clearing_bps_round_trip=0.4,
            stt_stamp_gst_bps_round_trip=0.2,
            default_slippage_bps_round_trip=2.0,
            uncertainty_hurdle_buffer_bps=1.5,
        )

    def total_round_trip_cost_bps(self, observed_spread_bps: Optional[float] = None) -> float:
        spread_cost = float(observed_spread_bps) if (observed_spread_bps is not None and observed_spread_bps >= 0) else 2.0
        return round(
            self.brokerage_bps_round_trip
            + self.exchange_sebi_clearing_bps_round_trip
            + self.stt_stamp_gst_bps_round_trip
            + self.default_slippage_bps_round_trip
            + spread_cost,
            4,
        )

    def actionable_hurdle_fraction(self, observed_spread_bps: Optional[float] = None) -> float:
        total_bps = self.total_round_trip_cost_bps(observed_spread_bps) + self.uncertainty_hurdle_buffer_bps
        return total_bps / 10000.0


class PredictiveTargetRecord(BaseModel):
    """Machine-readable target label record completely decoupled from input feature matrices."""

    event_id: str
    instrument_id: str
    jurisdiction: MarketJurisdiction
    decision_timestamp: datetime
    horizon: OutcomeHorizon
    label_type: OutcomeLabelType

    entry_timestamp: Optional[datetime] = None
    entry_price: Optional[float] = None
    exit_timestamp: Optional[datetime] = None
    exit_price: Optional[float] = None

    simple_return: Optional[float] = None
    log_return: Optional[float] = None
    benchmark_return: Optional[float] = None
    market_adjusted_return: Optional[float] = None
    benchmark_adjustment_method: BenchmarkAdjustmentMethod = BenchmarkAdjustmentMethod.NONE

    cost_scenario: str
    transaction_cost_assumption_bps: Optional[float] = None
    spread_bps: Optional[float] = None
    estimated_slippage_bps: Optional[float] = None
    actionable_hurdle_bps: Optional[float] = None

    # Classification & multi-view targets respecting neutral cost band
    y_continuous: Optional[float] = None
    y_direction_3class: Optional[ActionableDirectionClass] = None
    y_positive_material: Optional[int] = None   # 1 iff simple_return > actionable_hurdle
    y_negative_material: Optional[int] = None   # 1 iff simple_return < -actionable_hurdle
    y_material_move: Optional[int] = None       # 1 iff |simple_return| > actionable_hurdle
    y_positive_raw: Optional[int] = None        # 1 iff simple_return > 0 (unthresholded sign)

    data_quality_status: OutcomeDataQualityStatus
    is_matured: bool = False
    is_trainable: bool = False
    source_provenance: str
    target_schema_version: str = TARGET_SCHEMA_VERSION
    is_synthetic: bool = False


class PredictiveTargetBuilder:
    """Converts verified Phase 4/5 `EventOutcomeLabel` objects into Phase 5 training targets."""

    def __init__(
        self,
        cost_spec: Optional[CostAssumptionSpec] = None,
        use_market_adjusted_as_continuous: bool = False,
        min_trainable_samples_per_horizon: int = 20,
    ) -> None:
        self.cost_spec = cost_spec or CostAssumptionSpec.indian_nifty_fut_conservative()
        self.use_market_adjusted_as_continuous = use_market_adjusted_as_continuous
        self.min_trainable_samples_per_horizon = min_trainable_samples_per_horizon

    def build_target(
        self,
        outcome_label: EventOutcomeLabel,
        jurisdiction: MarketJurisdiction,
        evaluation_cutoff_ts: Optional[datetime] = None,
    ) -> PredictiveTargetRecord:
        """Build a point-in-time verified PredictiveTargetRecord from an EventOutcomeLabel."""
        spread = outcome_label.entry_spread_bps_cost if outcome_label.entry_spread_bps_cost is not None else outcome_label.avg_spread_bps
        rt_cost_bps = self.cost_spec.total_round_trip_cost_bps(spread)
        hurdle_frac = self.cost_spec.actionable_hurdle_fraction(spread)
        hurdle_bps = round(hurdle_frac * 10000.0, 4)

        matured = (
            outcome_label.is_matured_at(evaluation_cutoff_ts)
            if evaluation_cutoff_ts is not None
            else (outcome_label.outcome_end_price_ts is not None and outcome_label.data_quality_status != OutcomeDataQualityStatus.IMMATURE)
        )
        valid_quality = outcome_label.data_quality_status in TRAINABLE_QUALITY_STATUSES

        # Determine target return scalar strictly if matured and valid
        cont_ret: Optional[float] = None
        if matured and valid_quality and outcome_label.simple_return is not None:
            if self.use_market_adjusted_as_continuous and outcome_label.benchmark_adjusted_return is not None:
                cont_ret = float(outcome_label.benchmark_adjusted_return)
            else:
                cont_ret = float(outcome_label.simple_return)

        dir_cls: Optional[ActionableDirectionClass] = None
        y_pos_mat: Optional[int] = None
        y_neg_mat: Optional[int] = None
        y_move_mat: Optional[int] = None
        y_pos_raw: Optional[int] = None

        if cont_ret is not None:
            y_pos_raw = 1 if cont_ret > 0.0 else 0
            if cont_ret > hurdle_frac:
                dir_cls = ActionableDirectionClass.LONG_POSITIVE
                y_pos_mat = 1
                y_neg_mat = 0
                y_move_mat = 1
            elif cont_ret < -hurdle_frac:
                dir_cls = ActionableDirectionClass.SHORT_NEGATIVE
                y_pos_mat = 0
                y_neg_mat = 1
                y_move_mat = 1
            else:
                dir_cls = ActionableDirectionClass.NEUTRAL_NO_TRADE
                y_pos_mat = 0
                y_neg_mat = 0
                y_move_mat = 0

        is_trainable = bool(matured and valid_quality and cont_ret is not None)

        return PredictiveTargetRecord(
            event_id=outcome_label.event_id,
            instrument_id=outcome_label.instrument_id,
            jurisdiction=jurisdiction,
            decision_timestamp=outcome_label.decision_timestamp_utc,
            horizon=outcome_label.horizon,
            label_type=outcome_label.label_type,
            entry_timestamp=outcome_label.outcome_start_price_ts,
            entry_price=outcome_label.start_price,
            exit_timestamp=outcome_label.outcome_end_price_ts,
            exit_price=outcome_label.end_price,
            simple_return=outcome_label.simple_return,
            log_return=outcome_label.raw_log_return,
            benchmark_return=outcome_label.benchmark_return,
            market_adjusted_return=outcome_label.benchmark_adjusted_return,
            benchmark_adjustment_method=outcome_label.benchmark_adjustment_method,
            cost_scenario=self.cost_spec.scenario_name,
            transaction_cost_assumption_bps=rt_cost_bps,
            spread_bps=spread,
            estimated_slippage_bps=self.cost_spec.default_slippage_bps_round_trip,
            actionable_hurdle_bps=hurdle_bps,
            y_continuous=cont_ret,
            y_direction_3class=dir_cls,
            y_positive_material=y_pos_mat,
            y_negative_material=y_neg_mat,
            y_material_move=y_move_mat,
            y_positive_raw=y_pos_raw,
            data_quality_status=outcome_label.data_quality_status,
            is_matured=matured,
            is_trainable=is_trainable,
            source_provenance=outcome_label.market_data_source,
            is_synthetic=outcome_label.is_synthetic,
        )

    def check_horizon_trainability(
        self,
        records: Sequence[PredictiveTargetRecord],
        horizon: OutcomeHorizon,
        label_type: OutcomeLabelType,
    ) -> Tuple[bool, int, str]:
        """Gate modeling so low-resolution or undersampled horizons are excluded cleanly."""
        matching = [r for r in records if r.horizon == horizon and r.label_type == label_type]
        trainable = [r for r in matching if r.is_trainable]
        res_blocked = sum(1 for r in matching if r.data_quality_status == OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION)
        if not matching:
            return False, 0, f"No records available for horizon={horizon.value}, label_type={label_type.value}."
        if res_blocked == len(matching):
            return False, 0, f"All {len(matching)} observations blocked by INSUFFICIENT_RESOLUTION for {horizon.value}."
        if len(trainable) < self.min_trainable_samples_per_horizon:
            return (
                False,
                len(trainable),
                f"Trainable sample count {len(trainable)} below minimum {self.min_trainable_samples_per_horizon} for {horizon.value}.",
            )
        return True, len(trainable), "TRAINABLE"
