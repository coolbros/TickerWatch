"""Evaluation, Benchmarking and Calibration Framework for Financial NLP."""

from datetime import datetime, timezone
import os
import psutil
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.alpha.entities import ResolutionState
from backend.alpha.event_classifier import EventSubtype
from backend.alpha.materiality import MaterialityTier
from backend.alpha.nlp_pipeline import FinancialNLPPipeline, get_nlp_pipeline


class GoldEvaluationCase(BaseModel):
    """Ground truth gold evaluation record."""

    case_id: str
    headline: str
    body_text: Optional[str] = None
    expected_entity_id: Optional[str] = None
    expected_primary_subtype: str
    expected_monetary_value: Optional[float] = None
    expected_percentage_value: Optional[float] = None
    expected_materiality_tier: str
    category: str


def get_gold_evaluation_dataset() -> List[GoldEvaluationCase]:
    """
    Returns 52 diverse, hand-curated financial test cases across Indian corporate filings,
    macroeconomic releases, SEC filings, routine notices, and adversarial ticker collisions.
    """
    return [
        # --- 1. Indian Corporate Earnings & Dividends (10 cases) ---
        GoldEvaluationCase(
            case_id="IND_EARN_01",
            headline="TCS reports Q3 net profit of Rs 12,434 crore, declares interim dividend of Rs 10 per share",
            expected_entity_id="TCS",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=1.2434e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_02",
            headline="Infosys Q2 net profit rises 5% YoY to Rs 6,212 crore, raises FY25 revenue guidance",
            expected_entity_id="INFY",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=6.212e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_03",
            headline="Reliance Industries net profit falls 4.8% to Rs 16,563 cr in Q1",
            expected_entity_id="RELIANCE",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=1.6563e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_04",
            headline="HDFC Bank posts quarterly profit of Rs 16,821 crore as net interest income surges",
            expected_entity_id="HDFCBANK",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=1.6821e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_05",
            headline="ICICI Bank standalone net profit surges 14.5% to Rs 11,746 cr",
            expected_entity_id="ICICIBANK",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=1.1746e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_06",
            headline="State Bank of India Q3 PAT jumps 35% YoY to Rs 18,844 crore",
            expected_entity_id="SBIN",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=1.8844e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_07",
            headline="Tata Motors reports consolidated net profit of Rs 3,450 crore in Q2",
            expected_entity_id="TATAMOTORS",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=3.45e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_08",
            headline="ITC recommends special dividend of Rs 6.50 per share",
            expected_entity_id="ITC",
            expected_primary_subtype="DIVIDEND_ANNOUNCEMENT",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_2_MODERATE",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_09",
            headline="Bharti Airtel Q4 net profit drops 31% to Rs 2,072 crore",
            expected_entity_id="BHARTIARTL",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=2.072e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),
        GoldEvaluationCase(
            case_id="IND_EARN_10",
            headline="Kotak Mahindra Bank quarterly profit rises 18% to Rs 3,250 cr",
            expected_entity_id="KOTAKBANK",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=3.25e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_EARNINGS",
        ),

        # --- 2. Indian Commercial Orders & Capex (10 cases) ---
        GoldEvaluationCase(
            case_id="IND_ORD_01",
            headline="L&T bags mega order worth Rs 2,500 crore from Middle East client",
            expected_entity_id="LT",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=2.5e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_02",
            headline="Larsen & Toubro wins contract worth Rs 1,450 cr for renewable energy transmission",
            expected_entity_id="LT",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=1.45e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_03",
            headline="Tata Motors secures order for 1,000 electric buses from state transport undertaking",
            expected_entity_id="TATAMOTORS",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_2_MODERATE",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_04",
            headline="Reliance Industries approves capital expenditure of Rs 75,000 crore for green energy gigafactories",
            expected_entity_id="RELIANCE",
            expected_primary_subtype="CAPITAL_EXPENDITURE",
            expected_monetary_value=7.5e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_05",
            headline="Infosys signs pact worth $1.5 billion with global enterprise client for cloud transformation",
            expected_entity_id="INFY",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=1.5e9,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_06",
            headline="TCS signs pact with UK insurance major worth $1.9 billion over 10 years",
            expected_entity_id="TCS",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=1.9e9,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_07",
            headline="Jio Infocomm awards contract worth Rs 8,000 crore for 5G network equipment",
            expected_entity_id="RELIANCE",  # Subsidiary resolves to parent
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=8.0e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_08",
            headline="Reliance Retail to acquire Metro Cash & Carry India for Rs 2,850 crore",
            expected_entity_id="RELIANCE",  # Subsidiary resolves to parent
            expected_primary_subtype="MERGER_ACQUISITION",
            expected_monetary_value=2.85e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_09",
            headline="Bharti Airtel signs commercial agreement worth Rs 1,200 cr with enterprise customers",
            expected_entity_id="BHARTIARTL",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=1.2e10,
            expected_materiality_tier="TIER_2_MODERATE",
            category="INDIAN_OPERATIONAL",
        ),
        GoldEvaluationCase(
            case_id="IND_ORD_10",
            headline="Larsen and Toubro awarded contract worth Rs 3,500 crore for metro rail expansion",
            expected_entity_id="LT",
            expected_primary_subtype="CONTRACT_ORDER_WIN",
            expected_monetary_value=3.5e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="INDIAN_OPERATIONAL",
        ),

        # --- 3. Macroeconomic & Policy Releases (10 cases) ---
        GoldEvaluationCase(
            case_id="MACRO_01",
            headline="RBI Monetary Policy Committee keeps repo rate unchanged at 6.5%",
            expected_entity_id="RBI",
            expected_primary_subtype="INTEREST_RATE_DECISION",
            expected_monetary_value=None,
            expected_percentage_value=6.5,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_02",
            headline="RBI MPC hikes repo rate by 50 bps to fight inflation pressures",
            expected_entity_id="RBI",
            expected_primary_subtype="INTEREST_RATE_DECISION",
            expected_monetary_value=None,
            expected_percentage_value=0.5,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_03",
            headline="India retail inflation CPI eases to 4.85% in March from 5.09% in February",
            expected_entity_id=None,
            expected_primary_subtype="INFLATION_CPI",
            expected_monetary_value=None,
            expected_percentage_value=4.85,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_04",
            headline="India CPI inflation rises to 5.4% YoY as food prices spike",
            expected_entity_id=None,
            expected_primary_subtype="INFLATION_CPI",
            expected_monetary_value=None,
            expected_percentage_value=5.4,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_05",
            headline="Reserve Bank of India maintains policy stance on withdrawal of accommodation",
            expected_entity_id="RBI",
            expected_primary_subtype="INTEREST_RATE_DECISION",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_06",
            headline="Wholesale inflation WPI rises to 1.26% in April",
            expected_entity_id=None,
            expected_primary_subtype="INFLATION_CPI",
            expected_monetary_value=None,
            expected_percentage_value=1.26,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_07",
            headline="SEBI order imposes show cause notice on market intermediary for disclosure lapse",
            expected_entity_id="SEBI",
            expected_primary_subtype="REGULATORY_INVESTIGATION",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_08",
            headline="SEBI penalty of Rs 25 lakh on company promoters for insider trading violations",
            expected_entity_id="SEBI",
            expected_primary_subtype="REGULATORY_INVESTIGATION",
            expected_monetary_value=2.5e6,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_09",
            headline="Finance Ministry clarifies policy stance on windfall tax on crude oil",
            expected_entity_id="FINMIN",
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_2_MODERATE",
            category="MACRO",
        ),
        GoldEvaluationCase(
            case_id="MACRO_10",
            headline="Consumer price index CPI inflation drops 20 bps sequential",
            expected_entity_id=None,
            expected_primary_subtype="INFLATION_CPI",
            expected_monetary_value=None,
            expected_percentage_value=0.20,
            expected_materiality_tier="TIER_3_HIGH",
            category="MACRO",
        ),

        # --- 4. Routine Administrative Disclosures (8 cases) ---
        GoldEvaluationCase(
            case_id="ROUTINE_01",
            headline="Intimation of Board Meeting to consider financial results and trading window closure",
            expected_entity_id=None,
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_02",
            headline="Intimation regarding loss of share certificate under Regulation 39(3)",
            expected_entity_id=None,
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_03",
            headline="Trading window closure notice for designated persons pursuant to SEBI regulations",
            expected_entity_id=None,
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_04",
            headline="Postal ballot notice to shareholders for routine corporate authorizations",
            expected_entity_id=None,
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_05",
            headline="TCS board meeting intimation to consider quarterly audited results",
            expected_entity_id="TCS",
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_06",
            headline="Infosys intimates schedule of analyst and institutional investor meeting",
            expected_entity_id="INFY",
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_07",
            headline="Reliance Industries intimates date of Annual General Meeting and book closure",
            expected_entity_id="RELIANCE",
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),
        GoldEvaluationCase(
            case_id="ROUTINE_08",
            headline="Intimation of duplicate share certificate issuance to registered holder",
            expected_entity_id=None,
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ROUTINE",
        ),

        # --- 5. Adversarial Ticker Collisions & Multi-label Edge Cases (8 cases) ---
        GoldEvaluationCase(
            case_id="ADV_01",
            headline="Government policy focus on IT infrastructure growth across rural districts",
            expected_entity_id=None,  # "IT" is a collision stopword without financial context
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_02",
            headline="Looking AT the macroeconomic horizon ahead of annual central bank symposium",
            expected_entity_id=None,  # "AT" is a stopword
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_03",
            headline="Mukesh Ambani announces new retail expansion strategy across tier-2 cities",
            expected_entity_id="RELIANCE",  # Executive co-occurrence
            expected_primary_subtype="CAPITAL_EXPENDITURE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_2_MODERATE",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_04",
            headline="Company posts (Rs 120 cr) loss, margin contracts 75 bps",
            expected_entity_id=None,
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=-1.2e9,  # Accounting negative loss parsed accurately
            expected_materiality_tier="TIER_2_MODERATE",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_05",
            headline="L&T approves share buyback of Rs 10,000 crore at Rs 3,000 per share",
            expected_entity_id="LT",
            expected_primary_subtype="BUYBACK",
            expected_monetary_value=1.0e11,
            expected_materiality_tier="TIER_3_HIGH",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_06",
            headline="Capex commitment between Rs 500 cr and Rs 600 cr planned for next fiscal",
            expected_entity_id=None,
            expected_primary_subtype="CAPITAL_EXPENDITURE",
            expected_monetary_value=5.5e9,  # Range midpoint
            expected_materiality_tier="TIER_2_MODERATE",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_07",
            headline="CEO steps down following board reconstitution and management restructuring",
            expected_entity_id=None,
            expected_primary_subtype="MANAGEMENT_CHANGE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_2_MODERATE",
            category="ADVERSARIAL",
        ),
        GoldEvaluationCase(
            case_id="ADV_08",
            headline="Operating margin contracts 120 bps despite higher quarterly sales volume",
            expected_entity_id=None,
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_percentage_value=-1.20,
            expected_materiality_tier="TIER_1_ROUTINE",
            category="ADVERSARIAL",
        ),

        # --- 6. Global US Mega-Caps (SEC EDGAR Style) (6 cases) ---
        GoldEvaluationCase(
            case_id="US_01",
            headline="Apple reports quarterly revenue of $94.9 billion, operating profit rises",
            expected_entity_id="AAPL",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=9.49e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="US_MARKET",
        ),
        GoldEvaluationCase(
            case_id="US_02",
            headline="Amazon Q3 net profit reaches $15.3 billion as AWS cloud revenue grows 19%",
            expected_entity_id="AMZN",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=1.53e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="US_MARKET",
        ),
        GoldEvaluationCase(
            case_id="US_03",
            headline="Microsoft posts quarterly net income of $24.7 billion on strong Azure demand",
            expected_entity_id="MSFT",
            expected_primary_subtype="EARNINGS_ANNOUNCEMENT",
            expected_monetary_value=2.47e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="US_MARKET",
        ),
        GoldEvaluationCase(
            case_id="US_04",
            headline="Tim Cook confirms Apple intelligence rollout and expanded R&D investments",
            expected_entity_id="AAPL",  # Executive co-occurrence
            expected_primary_subtype="ROUTINE_ADMINISTRATIVE",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_2_MODERATE",
            category="US_MARKET",
        ),
        GoldEvaluationCase(
            case_id="US_05",
            headline="Amazon authorizes new share repurchase program of up to $10 billion",
            expected_entity_id="AMZN",
            expected_primary_subtype="BUYBACK",
            expected_monetary_value=1.0e10,
            expected_materiality_tier="TIER_3_HIGH",
            category="US_MARKET",
        ),
        GoldEvaluationCase(
            case_id="US_06",
            headline="Microsoft cuts revenue guidance for gaming segment due to hardware supply constraints",
            expected_entity_id="MSFT",
            expected_primary_subtype="GUIDANCE_REVISION",
            expected_monetary_value=None,
            expected_materiality_tier="TIER_3_HIGH",
            category="US_MARKET",
        ),
    ]


class BenchmarkResults(BaseModel):
    """Structured benchmark evaluation scorecard."""

    total_test_cases: int
    entity_precision: float
    entity_recall: float
    entity_f1: float
    ticker_collision_false_positive_rate: float
    event_classification_accuracy: float
    numerical_extraction_accuracy: float
    materiality_tier_accuracy: float
    latency_p50_us: float
    latency_p95_us: float
    latency_p99_us: float
    memory_rss_mb: float
    all_gates_passed: bool
    evaluation_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def run_nlp_benchmark(pipeline: Optional[FinancialNLPPipeline] = None) -> BenchmarkResults:
    """Run full benchmark against gold evaluation dataset."""
    pipe = pipeline or get_nlp_pipeline()
    dataset = get_gold_evaluation_dataset()

    entity_tp = 0
    entity_fp = 0
    entity_fn = 0
    ticker_collision_fp = 0
    ticker_collision_total = 0

    class_correct = 0
    extraction_correct = 0
    extraction_evaluated = 0
    materiality_tier_correct = 0

    latencies_us: List[int] = []

    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / (1024 * 1024)

    for case in dataset:
        t_start = time.perf_counter_ns()
        rep = pipe.process_raw_text(headline=case.headline, body_text=case.body_text)
        latency = (time.perf_counter_ns() - t_start) // 1000
        latencies_us.append(latency)

        # Entity Evaluation
        actual_entity = rep.entity_resolution.entity_id if rep.entity_resolution.resolution_state == ResolutionState.RESOLVED else None
        if case.expected_entity_id:
            if actual_entity == case.expected_entity_id:
                entity_tp += 1
            else:
                entity_fn += 1
                if actual_entity is not None:
                    entity_fp += 1
        else:
            if actual_entity is not None:
                entity_fp += 1

        # Ticker collision stopword check
        if case.category == "ADVERSARIAL" and case.expected_entity_id is None:
            ticker_collision_total += 1
            if actual_entity is not None:
                ticker_collision_fp += 1

        # Classification Evaluation
        if rep.classification.primary_subtype.value == case.expected_primary_subtype:
            class_correct += 1

        # Numerical Extraction Evaluation
        if case.expected_monetary_value is not None:
            extraction_evaluated += 1
            extracted_val = rep.extraction.primary_metric.normalized_value if rep.extraction.primary_metric else None
            if extracted_val is not None:
                # Check within 5% relative tolerance or exact match
                if abs(extracted_val - case.expected_monetary_value) <= max(1.0, 0.05 * abs(case.expected_monetary_value)):
                    extraction_correct += 1

        # Materiality Tier Evaluation
        if rep.materiality.materiality_tier.value == case.expected_materiality_tier:
            materiality_tier_correct += 1

    mem_after = process.memory_info().rss / (1024 * 1024)

    # Compute Metrics
    precision = entity_tp / (entity_tp + entity_fp) if (entity_tp + entity_fp) > 0 else 1.0
    recall = entity_tp / (entity_tp + entity_fn) if (entity_tp + entity_fn) > 0 else 1.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    ticker_fp_rate = (ticker_collision_fp / ticker_collision_total) if ticker_collision_total > 0 else 0.0
    class_acc = class_correct / len(dataset)
    extract_acc = (extraction_correct / extraction_evaluated) if extraction_evaluated > 0 else 1.0
    mat_acc = materiality_tier_correct / len(dataset)

    latencies_us.sort()
    n = len(latencies_us)
    p50 = latencies_us[int(0.50 * n)]
    p95 = latencies_us[int(0.95 * n)]
    p99 = latencies_us[min(n - 1, int(0.99 * n))]

    # Quality Control Gate Assertions
    # Targets: Entity F1 >= 0.85, Ticker FP <= 0.05, Class Acc >= 0.85, Extract Acc >= 0.85, P95 < 5000 us (5ms)
    gates_passed = (
        f1 >= 0.85
        and ticker_fp_rate <= 0.05
        and class_acc >= 0.85
        and extract_acc >= 0.85
        and p95 < 5000
    )

    return BenchmarkResults(
        total_test_cases=len(dataset),
        entity_precision=round(precision, 4),
        entity_recall=round(recall, 4),
        entity_f1=round(f1, 4),
        ticker_collision_false_positive_rate=round(ticker_fp_rate, 4),
        event_classification_accuracy=round(class_acc, 4),
        numerical_extraction_accuracy=round(extract_acc, 4),
        materiality_tier_accuracy=round(mat_acc, 4),
        latency_p50_us=round(p50, 1),
        latency_p95_us=round(p95, 1),
        latency_p99_us=round(p99, 1),
        memory_rss_mb=round(mem_after, 2),
        all_gates_passed=gates_passed,
    )
