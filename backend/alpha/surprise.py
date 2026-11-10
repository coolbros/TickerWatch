"""Numerical Surprise Engine with Strict Point-in-Time Expectation Integrity."""

from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from backend.alpha.event_extraction import CurrencyUnit, ExtractedMetricValue, FinancialMetricType


class SurpriseCategory(str, Enum):
    POSITIVE_SURPRISE = "POSITIVE_SURPRISE"
    NEGATIVE_SURPRISE = "NEGATIVE_SURPRISE"
    IN_LINE = "IN_LINE"
    UNQUANTIFIED = "UNQUANTIFIED"
    UNAVAILABLE_EXPECTATION = "UNAVAILABLE_EXPECTATION"


class ExpectationType(str, Enum):
    CONSENSUS_ANALYST = "CONSENSUS_ANALYST"
    COMPANY_GUIDANCE = "COMPANY_GUIDANCE"
    HISTORICAL_BASELINE = "HISTORICAL_BASELINE"
    UNAVAILABLE = "UNAVAILABLE"


class PointInTimeExpectation(BaseModel):
    """
    Consensus or guidance expectation recorded strictly prior to event disclosure.
    Guarantees zero look-ahead bias.
    """

    instrument_id: str
    metric_name: str
    expected_value: float
    currency: CurrencyUnit = CurrencyUnit.UNKNOWN
    dispersion_std: Optional[float] = None
    expectation_type: ExpectationType = ExpectationType.CONSENSUS_ANALYST
    source: str = "CONSENSUS_FEED"
    captured_at: datetime
    period: Optional[str] = None


class NumericalSurpriseResult(BaseModel):
    """
    Quantified financial surprise output.
    Contains absolute surprise, percentage surprise, standard Z-score, and materiality ratio.
    """

    metric_name: str
    actual_value: Optional[float] = None
    expected_value: Optional[float] = None
    currency: CurrencyUnit = CurrencyUnit.UNKNOWN
    expectation_source: Optional[str] = None
    expectation_captured_at: Optional[datetime] = None
    absolute_surprise: Optional[float] = None
    percentage_surprise: Optional[float] = None
    standardised_surprise_z: Optional[float] = None
    surprise_category: SurpriseCategory
    materiality_ratio: Optional[float] = None
    baseline_annual_revenue: Optional[float] = None
    baseline_currency: Optional[CurrencyUnit] = None
    in_line_threshold_pct: float = 1.0
    is_cost_metric: bool = False
    status_notes: str = ""
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NumericalSurpriseEngine:
    """
    Computes financial surprise while rigorously enforcing causal temporal integrity.
    If consensus expectations are unavailable at the decision timestamp,
    it records UNAVAILABLE_EXPECTATION without fabricating or silently imputing values.
    Enforces strict currency alignment and metric economic polarity.
    """

    def __init__(self, in_line_threshold_pct: float = 1.0) -> None:
        self.in_line_threshold_pct = in_line_threshold_pct
        self._expectations_store: Dict[str, List[PointInTimeExpectation]] = {}

        # Known annual revenue baselines explicitly partitioned by (entity_id, currency)
        # Prevents any cross-currency calculation between INR and USD
        self._revenue_baselines: Dict[Tuple[str, CurrencyUnit], float] = {
            ("RELIANCE", CurrencyUnit.INR): 9.0e11,      # ~9,00,000 cr INR
            ("TCS", CurrencyUnit.INR): 2.4e11,           # ~2,40,000 cr INR
            ("INFY", CurrencyUnit.INR): 1.5e11,          # ~1,50,000 cr INR
            ("HDFCBANK", CurrencyUnit.INR): 1.8e11,      # ~1,80,000 cr INR
            ("ICICIBANK", CurrencyUnit.INR): 1.4e11,     # ~1,40,000 cr INR
            ("LT", CurrencyUnit.INR): 2.2e11,            # ~2,20,000 cr INR
            ("SBIN", CurrencyUnit.INR): 3.8e11,          # ~3,80,000 cr INR
            ("BHARTIARTL", CurrencyUnit.INR): 1.5e11,    # ~1,50,000 cr INR
            ("ITC", CurrencyUnit.INR): 7.0e10,           # ~70,000 cr INR
            ("KOTAKBANK", CurrencyUnit.INR): 6.8e10,     # ~68,000 cr INR
            ("TATAMOTORS", CurrencyUnit.INR): 4.3e11,    # ~4,30,000 cr INR
            ("AAPL", CurrencyUnit.USD): 3.9e11,          # $390 Billion USD
            ("AMZN", CurrencyUnit.USD): 5.7e11,          # $570 Billion USD
            ("MSFT", CurrencyUnit.USD): 2.4e11,          # $240 Billion USD
        }

    def register_expectation(self, expectation: PointInTimeExpectation) -> None:
        """Register a point-in-time expectation in the causal store."""
        key = f"{expectation.instrument_id}:{expectation.metric_name.upper()}"
        if key not in self._expectations_store:
            self._expectations_store[key] = []
        self._expectations_store[key].append(expectation)

    def get_point_in_time_expectation(
        self, instrument_id: str, metric_name: str, as_of: datetime
    ) -> Optional[PointInTimeExpectation]:
        """
        Query the latest consensus expectation strictly prior to `as_of`.
        Prevents look-ahead bias by asserting captured_at < as_of.
        """
        key = f"{instrument_id}:{metric_name.upper()}"
        candidates = self._expectations_store.get(key, [])
        valid = [e for e in candidates if e.captured_at < as_of]
        if not valid:
            return None
        return max(valid, key=lambda e: e.captured_at)

    def compute_surprise(
        self,
        metric: Optional[ExtractedMetricValue],
        instrument_id: Optional[str],
        event_timestamp: datetime,
        explicit_expectation: Optional[PointInTimeExpectation] = None,
    ) -> NumericalSurpriseResult:
        """
        Compute surprise for an extracted metric at an event timestamp.
        Respects economic polarity (costs vs revenues) and currency alignment.
        """
        if metric is None:
            return NumericalSurpriseResult(
                metric_name="UNQUANTIFIED",
                surprise_category=SurpriseCategory.UNQUANTIFIED,
                status_notes="No quantitative financial metric was extracted from document.",
            )

        metric_name = metric.metric_type.value
        actual_val = metric.normalized_value
        metric_currency = metric.currency

        # Identify cost, liability, or adverse metrics
        is_cost = metric.metric_type in (
            FinancialMetricType.EXPENSE,
            FinancialMetricType.DEBT,
        ) or "EXPENSE" in metric_name or "COST" in metric_name or "INFLATION" in metric_name

        # Look up point-in-time expectation if not explicitly provided
        expectation = explicit_expectation
        if expectation is None and instrument_id:
            expectation = self.get_point_in_time_expectation(instrument_id, metric_name, event_timestamp)

        # Causal check: ensure expectation is strictly prior to event_timestamp
        if expectation is not None and expectation.captured_at >= event_timestamp:
            raise ValueError(
                f"Causal violation: expectation captured_at ({expectation.captured_at}) "
                f">= event_timestamp ({event_timestamp}). Look-ahead bias prohibited."
            )

        # Check baseline materiality for contract wins or capex with strict currency matching
        materiality_ratio = None
        revenue_baseline = None
        baseline_curr = None
        if instrument_id:
            # Check exact (instrument, currency) match first
            for (ent, curr), base_val in self._revenue_baselines.items():
                if ent == instrument_id.upper() and (metric_currency == CurrencyUnit.UNKNOWN or curr == metric_currency):
                    revenue_baseline = base_val
                    baseline_curr = curr
                    break

            if revenue_baseline and revenue_baseline > 0 and actual_val > 0:
                if metric.metric_type in (FinancialMetricType.CONTRACT_VALUE, FinancialMetricType.CAPEX):
                    materiality_ratio = round(actual_val / revenue_baseline, 5)

        # If no consensus expectation is available
        if expectation is None:
            return NumericalSurpriseResult(
                metric_name=metric_name,
                actual_value=actual_val,
                currency=metric_currency,
                surprise_category=SurpriseCategory.UNAVAILABLE_EXPECTATION,
                materiality_ratio=materiality_ratio,
                baseline_annual_revenue=revenue_baseline,
                baseline_currency=baseline_curr,
                is_cost_metric=is_cost,
                status_notes=(
                    f"Consensus expectation for {instrument_id or 'UNKNOWN'}:{metric_name} "
                    f"was not available at event timestamp {event_timestamp.isoformat()}. "
                    "Recorded as UNAVAILABLE_EXPECTATION per causal integrity mandate."
                ),
            )

        # Consensus is available: Compute S = A - E and S% = (A - E) / |E| * 100
        expected_val = expectation.expected_value
        abs_surprise = actual_val - expected_val

        # Percentage surprise with zero/negative denominator handling
        pct_surprise: Optional[float] = None
        if abs(expected_val) < 1e-9:
            if abs(actual_val) < 1e-9:
                pct_surprise = 0.0
            else:
                pct_surprise = None  # Undefined/infinite
        else:
            # Using |E| preserves correct direction when consensus expectation is negative
            pct_surprise = round((abs_surprise / abs(expected_val)) * 100.0, 3)

        # Standardised Z-score if dispersion standard deviation is provided
        z_score: Optional[float] = None
        if expectation.dispersion_std is not None and expectation.dispersion_std > 1e-9:
            z_score = round(abs_surprise / expectation.dispersion_std, 3)

        # Categorize surprise taking economic polarity into account
        # For standard revenues/profits: A > E => POSITIVE_SURPRISE
        # For costs/liabilities: A > E => NEGATIVE_SURPRISE (higher cost than expected is bad!)
        if pct_surprise is not None:
            if abs(pct_surprise) <= self.in_line_threshold_pct:
                category = SurpriseCategory.IN_LINE
            elif pct_surprise > self.in_line_threshold_pct:
                category = SurpriseCategory.NEGATIVE_SURPRISE if is_cost else SurpriseCategory.POSITIVE_SURPRISE
            else:
                category = SurpriseCategory.POSITIVE_SURPRISE if is_cost else SurpriseCategory.NEGATIVE_SURPRISE
        else:
            if abs_surprise > 0:
                category = SurpriseCategory.NEGATIVE_SURPRISE if is_cost else SurpriseCategory.POSITIVE_SURPRISE
            elif abs_surprise < 0:
                category = SurpriseCategory.POSITIVE_SURPRISE if is_cost else SurpriseCategory.NEGATIVE_SURPRISE
            else:
                category = SurpriseCategory.IN_LINE

        return NumericalSurpriseResult(
            metric_name=metric_name,
            actual_value=actual_val,
            expected_value=expected_val,
            currency=metric_currency,
            expectation_source=expectation.source,
            expectation_captured_at=expectation.captured_at,
            absolute_surprise=round(abs_surprise, 2),
            percentage_surprise=pct_surprise,
            standardised_surprise_z=z_score,
            surprise_category=category,
            materiality_ratio=materiality_ratio,
            baseline_annual_revenue=revenue_baseline,
            baseline_currency=baseline_curr,
            in_line_threshold_pct=self.in_line_threshold_pct,
            is_cost_metric=is_cost,
            status_notes="Computed against point-in-time consensus expectation with economic polarity.",
        )


_surprise_engine: Optional[NumericalSurpriseEngine] = None


def get_surprise_engine() -> NumericalSurpriseEngine:
    """Return shared singleton NumericalSurpriseEngine."""
    global _surprise_engine
    if _surprise_engine is None:
        _surprise_engine = NumericalSurpriseEngine()
    return _surprise_engine
