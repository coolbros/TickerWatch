"""Structured Financial Event and Numerical Extraction Engine."""

from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class CurrencyUnit(str, Enum):
    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    UNKNOWN = "UNKNOWN"


class TemporalComparison(str, Enum):
    YOY = "YOY"
    QOQ = "QOQ"
    SEQUENTIAL = "SEQUENTIAL"
    FULL_YEAR = "FULL_YEAR"
    STANDALONE = "STANDALONE"


class DirectionChange(str, Enum):
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    UNCHANGED = "UNCHANGED"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class FinancialMetricType(str, Enum):
    REVENUE = "REVENUE"
    EBITDA = "EBITDA"
    OPERATING_PROFIT = "OPERATING_PROFIT"
    NET_PROFIT_PAT = "NET_PROFIT_PAT"
    EPS = "EPS"
    MARGIN = "MARGIN"
    CONTRACT_VALUE = "CONTRACT_VALUE"
    CAPEX = "CAPEX"
    DIVIDEND_PER_SHARE = "DIVIDEND_PER_SHARE"
    TARGET_PRICE = "TARGET_PRICE"
    EXPENSE = "EXPENSE"
    DEBT = "DEBT"
    OTHER = "OTHER"


class ExtractedMetricValue(BaseModel):
    """A quantified financial metric extracted from text."""

    metric_type: FinancialMetricType
    raw_text: str
    normalized_value: float
    original_value: Optional[float] = None
    currency: CurrencyUnit = CurrencyUnit.UNKNOWN
    original_currency: CurrencyUnit = CurrencyUnit.UNKNOWN
    unit: str = ""
    is_percentage: bool = False
    is_basis_points: bool = False
    is_range: bool = False
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    comparison_period: Optional[TemporalComparison] = None
    reporting_period: Optional[str] = None
    direction: DirectionChange = DirectionChange.UNKNOWN
    text_section: str = "HEADLINE"  # "HEADLINE" or "BODY"
    start_char: int = -1
    end_char: int = -1
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class FinancialExtractionResult(BaseModel):
    """Aggregated financial extraction output for a document."""

    headline: str
    body_text: Optional[str] = None
    metrics: List[ExtractedMetricValue] = Field(default_factory=list)
    primary_metric: Optional[ExtractedMetricValue] = None
    confidence: float = Field(default=0.80, ge=0.0, le=1.0)
    extraction_metadata: Dict[str, Any] = Field(default_factory=dict)


# Multiplier lookup
UNIT_MULTIPLIERS: Dict[str, float] = {
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
    "arab": 1e9,
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "tn": 1e12,
    "trillion": 1e12,
}

CURRENCY_SYMBOLS: Dict[str, CurrencyUnit] = {
    "₹": CurrencyUnit.INR,
    "rs": CurrencyUnit.INR,
    "rs.": CurrencyUnit.INR,
    "inr": CurrencyUnit.INR,
    "rupees": CurrencyUnit.INR,
    "$": CurrencyUnit.USD,
    "usd": CurrencyUnit.USD,
    "dollars": CurrencyUnit.USD,
    "€": CurrencyUnit.EUR,
    "eur": CurrencyUnit.EUR,
    "euros": CurrencyUnit.EUR,
    "£": CurrencyUnit.GBP,
    "gbp": CurrencyUnit.GBP,
    "pounds": CurrencyUnit.GBP,
    "¥": CurrencyUnit.JPY,
    "jpy": CurrencyUnit.JPY,
}


class FinancialEventExtractor:
    """
    High-precision financial entity & numerical extractor.
    Extracts monetary quantities (Lakh, Crore, Million, Billion), percentage changes,
    directional guidance, and comparative periods with explicit char spans across
    both headlines and body text.
    """

    def __init__(self) -> None:
        self._currency_re = r"(₹|rs\.?|inr|rupees|\$|usd|dollars|€|eur|euros|£|gbp)"
        self._unit_re = r"(lakhs?|lacs?|crores?|cr|arab|thousand|million|mn|billion|bn|trillion|tn|k|m|b|t)"
        self._number_re = r"(\d+(?:,\d+)*(?:\.\d+)?)"

    def extract_metrics(self, text: str, body_text: Optional[str] = None) -> FinancialExtractionResult:
        """
        Extract all financial metrics, values, directions, and comparisons from full document.
        Processes headline first, then extends extraction to body text with intelligent deduplication.
        """
        metrics: List[ExtractedMetricValue] = []

        # 1. Headline extractions
        hl_metrics = self._extract_section(text, section_name="HEADLINE")
        metrics.extend(hl_metrics)

        # 2. Body extractions (if body_text provided)
        if body_text and body_text.strip():
            body_metrics = self._extract_section(body_text.strip(), section_name="BODY")
            # Deduplicate body metrics that duplicate headline metrics
            for bm in body_metrics:
                is_duplicate = any(
                    hm.metric_type == bm.metric_type
                    and abs(hm.normalized_value - bm.normalized_value) < max(1.0, 0.01 * abs(hm.normalized_value))
                    for hm in hl_metrics
                )
                if not is_duplicate:
                    metrics.append(bm)

        # Disambiguate and rank primary metric
        primary_metric = self._select_primary_metric(metrics, text)

        overall_conf = 0.85 if metrics else 0.50
        if primary_metric:
            overall_conf = max(overall_conf, primary_metric.confidence)

        return FinancialExtractionResult(
            headline=text,
            body_text=body_text,
            metrics=metrics,
            primary_metric=primary_metric,
            confidence=round(overall_conf, 3),
            extraction_metadata={
                "metric_count": len(metrics),
                "headline_metrics_count": len(hl_metrics),
                "has_monetary": any(m.currency != CurrencyUnit.UNKNOWN for m in metrics),
                "has_percentage": any(m.is_percentage for m in metrics),
                "has_range": any(m.is_range for m in metrics),
                "processed_body": bool(body_text),
            },
        )

    def _extract_section(self, text: str, section_name: str = "HEADLINE") -> List[ExtractedMetricValue]:
        """Extract metrics from a specific document section."""
        extracted: List[ExtractedMetricValue] = []

        # 1. Range Extractions (e.g., 'between Rs 500 crore and Rs 600 crore' or 'Rs 500 - 600 cr')
        range_metrics, range_spans = self._extract_ranges(text, section_name=section_name)
        extracted.extend(range_metrics)

        # 2. Standalone Monetary and Quantitative Values (excluding covered range spans)
        single_metrics = self._extract_monetary_values(text, ignore_spans=range_spans, section_name=section_name)
        extracted.extend(single_metrics)
        all_monetary_spans = range_spans + [(m.start_char, m.end_char) for m in single_metrics]

        # 3. Per-share monetary values (e.g. 'Rs 15 per share', 'dividend of $2.50 per share')
        per_share_metrics = self._extract_per_share_values(
            text, ignore_spans=all_monetary_spans, section_name=section_name
        )
        extracted.extend(per_share_metrics)
        covered_for_pct = all_monetary_spans + [(m.start_char, m.end_char) for m in per_share_metrics]

        # 4. Percentages & Basis Points
        pct_metrics = self._extract_percentages(text, ignore_spans=covered_for_pct, section_name=section_name)
        extracted.extend(pct_metrics)

        return extracted

    def _extract_ranges(
        self, text: str, section_name: str = "HEADLINE"
    ) -> Tuple[List[ExtractedMetricValue], List[Tuple[int, int]]]:
        """Extract numerical ranges like 'between Rs 500 cr and Rs 600 cr' or '500-600 crore'."""
        extracted: List[ExtractedMetricValue] = []
        covered_spans: List[Tuple[int, int]] = []

        range_pattern = re.compile(
            rf"(?:between\s+)?(?P<curr1>{self._currency_re})?\s*(?P<num1>{self._number_re})\s*(?P<unit1>{self._unit_re})?\s*(?:-|to|\band\b)\s*(?P<curr2>{self._currency_re})?\s*(?P<num2>{self._number_re})\s*(?P<unit2>{self._unit_re})\b",
            re.IGNORECASE,
        )

        for m in range_pattern.finditer(text):
            c1 = m.group("curr1")
            n1 = float(m.group("num1").replace(",", ""))
            u1 = m.group("unit1")
            c2 = m.group("curr2")
            n2 = float(m.group("num2").replace(",", ""))
            u2 = m.group("unit2")

            currency_str = c1 or c2 or ""
            currency = self._resolve_currency(currency_str)

            unit_str = u2 or u1 or ""
            mult = UNIT_MULTIPLIERS.get(unit_str.lower(), 1.0)

            val_low = n1 * mult
            val_high = n2 * mult
            midpoint = (val_low + val_high) / 2.0

            span = (m.start(), m.end())
            covered_spans.append(span)

            metric_type = self._infer_metric_type(text, m.start(), m.end())
            direction = self._infer_direction(text, m.start(), m.end())
            comp = self._infer_comparison(text, m.start(), m.end())
            rep_period = self._infer_reporting_period(text, m.start(), m.end())

            extracted.append(
                ExtractedMetricValue(
                    metric_type=metric_type,
                    raw_text=m.group(0),
                    normalized_value=midpoint,
                    original_value=(n1 + n2) / 2.0,
                    currency=currency,
                    original_currency=currency,
                    unit=unit_str,
                    is_range=True,
                    range_low=val_low,
                    range_high=val_high,
                    comparison_period=comp,
                    reporting_period=rep_period,
                    direction=direction,
                    text_section=section_name,
                    start_char=m.start(),
                    end_char=m.end(),
                    confidence=0.92,
                )
            )

        return extracted, covered_spans

    def _extract_monetary_values(
        self, text: str, ignore_spans: List[Tuple[int, int]], section_name: str = "HEADLINE"
    ) -> List[ExtractedMetricValue]:
        """Extract standalone monetary quantities with currency and Indian/Western units."""
        extracted: List[ExtractedMetricValue] = []

        pattern = re.compile(
            rf"(?P<neg>-|\()?\s*(?P<curr>{self._currency_re})?\s*(?P<num>{self._number_re})\s*(?P<unit>{self._unit_re})\b(?P<close_paren>\))?",
            re.IGNORECASE,
        )

        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if self._is_span_covered(start, end, ignore_spans):
                continue

            c_str = m.group("curr") or ""
            currency = self._resolve_currency(c_str)
            raw_num = float(m.group("num").replace(",", ""))
            u_str = m.group("unit").lower()
            mult = UNIT_MULTIPLIERS.get(u_str, 1.0)

            # Check loss / negative indications
            is_negative = bool(m.group("neg") or (m.group("close_paren") and "(" in m.group(0)))
            prefix_window = text[max(0, start - 25) : start].lower()
            if "loss" in prefix_window or "deficit" in prefix_window:
                is_negative = True

            norm_val = - (raw_num * mult) if is_negative else (raw_num * mult)

            metric_type = self._infer_metric_type(text, start, end)
            direction = self._infer_direction(text, start, end)
            comp = self._infer_comparison(text, start, end)
            rep_period = self._infer_reporting_period(text, start, end)

            extracted.append(
                ExtractedMetricValue(
                    metric_type=metric_type,
                    raw_text=m.group(0),
                    normalized_value=norm_val,
                    original_value=-raw_num if is_negative else raw_num,
                    currency=currency,
                    original_currency=currency,
                    unit=u_str,
                    is_range=False,
                    comparison_period=comp,
                    reporting_period=rep_period,
                    direction=direction,
                    text_section=section_name,
                    start_char=start,
                    end_char=end,
                    confidence=0.90 if currency != CurrencyUnit.UNKNOWN else 0.80,
                )
            )

        return extracted

    def _extract_per_share_values(
        self, text: str, ignore_spans: List[Tuple[int, int]], section_name: str = "HEADLINE"
    ) -> List[ExtractedMetricValue]:
        """Extract per-share quantities (e.g. dividend per share, EPS, target prices)."""
        extracted: List[ExtractedMetricValue] = []

        pattern = re.compile(
            rf"(?P<curr>{self._currency_re})?\s*(?P<num>{self._number_re})\s*(?P<unit>per\s+share|/\s*share|\bapiece\b)",
            re.IGNORECASE,
        )

        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if self._is_span_covered(start, end, ignore_spans):
                continue

            c_str = m.group("curr") or ""
            currency = self._resolve_currency(c_str)
            raw_num = float(m.group("num").replace(",", ""))

            metric_type = self._infer_metric_type(text, start, end)
            if metric_type == FinancialMetricType.OTHER:
                metric_type = FinancialMetricType.DIVIDEND_PER_SHARE
            direction = self._infer_direction(text, start, end)
            comp = self._infer_comparison(text, start, end)
            rep_period = self._infer_reporting_period(text, start, end)

            extracted.append(
                ExtractedMetricValue(
                    metric_type=metric_type,
                    raw_text=m.group(0),
                    normalized_value=raw_num,
                    original_value=raw_num,
                    currency=currency,
                    original_currency=currency,
                    unit="per share",
                    is_percentage=False,
                    is_basis_points=False,
                    is_range=False,
                    comparison_period=comp,
                    reporting_period=rep_period,
                    direction=direction,
                    text_section=section_name,
                    start_char=start,
                    end_char=end,
                    confidence=0.90 if currency != CurrencyUnit.UNKNOWN else 0.85,
                )
            )

        return extracted

    def _extract_percentages(
        self, text: str, ignore_spans: List[Tuple[int, int]], section_name: str = "HEADLINE"
    ) -> List[ExtractedMetricValue]:
        """Extract percentages and basis points."""
        extracted: List[ExtractedMetricValue] = []

        pattern = re.compile(
            rf"(?P<sign>[+-])?\s*(?P<num>{self._number_re})\s*(?P<unit>%|percent\b|pct\b|bps\b|basis\s+points\b)",
            re.IGNORECASE,
        )

        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if self._is_span_covered(start, end, ignore_spans):
                continue

            raw_val = float(m.group("num").replace(",", ""))
            unit_str = m.group("unit").lower()
            is_bps = "bp" in unit_str

            sign = m.group("sign")
            prefix_window = text[max(0, start - 20) : start].lower()

            is_negative = sign == "-" or "down" in prefix_window or "falls" in prefix_window or "drop" in prefix_window
            if is_bps:
                norm_val = -(raw_val * 0.01) if is_negative else (raw_val * 0.01)
            else:
                norm_val = -raw_val if is_negative else raw_val

            metric_type = self._infer_metric_type(text, start, end)
            direction = self._infer_direction(text, start, end)
            comp = self._infer_comparison(text, start, end)
            rep_period = self._infer_reporting_period(text, start, end)

            extracted.append(
                ExtractedMetricValue(
                    metric_type=metric_type if metric_type != FinancialMetricType.OTHER else FinancialMetricType.MARGIN,
                    raw_text=m.group(0),
                    normalized_value=norm_val,
                    original_value=-raw_val if is_negative else raw_val,
                    unit="bps" if is_bps else "%",
                    is_percentage=not is_bps,
                    is_basis_points=is_bps,
                    comparison_period=comp,
                    reporting_period=rep_period,
                    direction=direction,
                    text_section=section_name,
                    start_char=start,
                    end_char=end,
                    confidence=0.88,
                )
            )

        return extracted

    def _is_span_covered(self, start: int, end: int, spans: List[Tuple[int, int]]) -> bool:
        """Check if [start, end] intersects any covered span."""
        for s_start, s_end in spans:
            if not (end <= s_start or start >= s_end):
                return True
        return False

    def _resolve_currency(self, curr_str: str) -> CurrencyUnit:
        clean = curr_str.lower().strip()
        return CURRENCY_SYMBOLS.get(clean, CurrencyUnit.UNKNOWN)

    def _infer_reporting_period(self, text: str, start: int, end: int) -> Optional[str]:
        """Infer explicit financial reporting period like FY26, Q3, etc."""
        window_start = max(0, start - 40)
        window_end = min(len(text), end + 40)
        context = text[window_start:window_end].upper()
        match = re.search(r"\b(FY\d{2,4}|Q[1-4]|H[1-2]|9M)\b", context)
        if match:
            return match.group(1)
        return None

    def _infer_metric_type(self, text: str, start: int, end: int) -> FinancialMetricType:
        """Infer financial metric type by finding the closest keyword to the value span."""
        window_start = max(0, start - 60)
        window_end = min(len(text), end + 60)
        window_text = text[window_start:window_end].lower()
        val_offset_in_window = start - window_start

        # Priority keyword rules - order matters for compound terms
        keyword_rules = [
            (FinancialMetricType.MARGIN, ["operating margin", "ebitda margin", "gross margin", "margin"]),
            (FinancialMetricType.NET_PROFIT_PAT, ["net profit", "pat", "bottomline", "net loss", "loss", "profit", "net income"]),
            (FinancialMetricType.REVENUE, ["revenue", "turnover", "sales", "topline"]),
            (FinancialMetricType.EBITDA, ["ebitda", "operating profit"]),
            (FinancialMetricType.CONTRACT_VALUE, ["order win", "bags order", "contract worth", "deal worth", "awarded contract", "order", "deal"]),
            (FinancialMetricType.CAPEX, ["capital expenditure", "capex", "plant setup", "investment"]),
            (FinancialMetricType.DIVIDEND_PER_SHARE, ["dividend", "dps"]),
            (FinancialMetricType.TARGET_PRICE, ["target price", "tp"]),
            (FinancialMetricType.EPS, ["eps", "earnings per share"]),
            (FinancialMetricType.EXPENSE, ["total expense", "operating expenses", "operating expenditure", "costs", "expenses"]),
            (FinancialMetricType.DEBT, ["total debt", "borrowings", "liabilities"]),
        ]

        best_metric = FinancialMetricType.OTHER
        min_dist = float("inf")

        for metric_type, keywords in keyword_rules:
            for kw in keywords:
                for match in re.finditer(re.escape(kw), window_text):
                    dist = abs(match.start() - val_offset_in_window)
                    if dist < min_dist:
                        min_dist = dist
                        best_metric = metric_type

        return best_metric

    def _infer_direction(self, text: str, start: int, end: int) -> DirectionChange:
        """Infer direction of change (INCREASE, DECREASE, UNCHANGED) from surrounding context."""
        window_start = max(0, start - 30)
        window_end = min(len(text), end + 30)
        context = text[window_start:window_end].lower()

        if any(w in context for w in ["up", "rises", "jump", "surges", "gain", "higher", "climb", "increased", "advance"]):
            return DirectionChange.INCREASE
        if any(w in context for w in ["down", "falls", "drop", "slump", "loss", "decline", "lower", "decreased", "slip", "contracts", "contraction"]):
            return DirectionChange.DECREASE
        if any(w in context for w in ["flat", "unchanged", "steady", "stagnant"]):
            return DirectionChange.UNCHANGED

        return DirectionChange.UNKNOWN

    def _infer_comparison(self, text: str, start: int, end: int) -> Optional[TemporalComparison]:
        """Infer temporal comparison basis (YoY, QoQ, sequential)."""
        window_start = max(0, start - 30)
        window_end = min(len(text), end + 30)
        context = text[window_start:window_end].lower()

        if any(w in context for w in ["yoy", "year-on-year", "annual", "from a year ago"]):
            return TemporalComparison.YOY
        if any(w in context for w in ["qoq", "quarter-on-quarter", "sequential"]):
            return TemporalComparison.QOQ
        if any(w in context for w in ["full year", "fy24", "fy25", "fy26", "fy27"]):
            return TemporalComparison.FULL_YEAR

        return None

    def _select_primary_metric(
        self, metrics: List[ExtractedMetricValue], text: str
    ) -> Optional[ExtractedMetricValue]:
        """Select the most prominent/market-relevant metric from the extracted set."""
        if not metrics:
            return None

        # Prioritize headline metrics over body metrics if available
        hl_metrics = [m for m in metrics if m.text_section == "HEADLINE"]
        candidates = hl_metrics if hl_metrics else metrics

        priority_map = {
            FinancialMetricType.NET_PROFIT_PAT: 10,
            FinancialMetricType.REVENUE: 9,
            FinancialMetricType.CONTRACT_VALUE: 8,
            FinancialMetricType.EBITDA: 7,
            FinancialMetricType.CAPEX: 6,
            FinancialMetricType.DIVIDEND_PER_SHARE: 5,
            FinancialMetricType.MARGIN: 4,
            FinancialMetricType.EXPENSE: 4,
            FinancialMetricType.DEBT: 4,
            FinancialMetricType.EPS: 3,
            FinancialMetricType.OTHER: 1,
        }

        return max(
            candidates,
            key=lambda m: (
                priority_map.get(m.metric_type, 0),
                1 if m.currency != CurrencyUnit.UNKNOWN else 0,
                m.confidence,
            ),
        )


_extractor: Optional[FinancialEventExtractor] = None


def get_event_extractor() -> FinancialEventExtractor:
    """Return shared singleton FinancialEventExtractor."""
    global _extractor
    if _extractor is None:
        _extractor = FinancialEventExtractor()
    return _extractor
