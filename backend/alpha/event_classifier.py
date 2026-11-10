"""Hierarchical Financial Event Taxonomy and Multi-Label Classification."""

from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class EventPrimaryFamily(str, Enum):
    CORPORATE_FINANCIAL = "CORPORATE_FINANCIAL"
    CORPORATE_OPERATIONAL = "CORPORATE_OPERATIONAL"
    MACROECONOMIC = "MACROECONOMIC"
    MARKET_SYSTEMIC = "MARKET_SYSTEMIC"
    GENERAL_CORPORATE = "GENERAL_CORPORATE"


class EventSubtype(str, Enum):
    # Corporate Financial
    EARNINGS_ANNOUNCEMENT = "EARNINGS_ANNOUNCEMENT"
    REVENUE_SURPRISE = "REVENUE_SURPRISE"
    PROFIT_SURPRISE = "PROFIT_SURPRISE"
    GUIDANCE_REVISION = "GUIDANCE_REVISION"
    MARGIN_CHANGE = "MARGIN_CHANGE"
    DIVIDEND_ANNOUNCEMENT = "DIVIDEND_ANNOUNCEMENT"
    BUYBACK = "BUYBACK"
    CAPITAL_EXPENDITURE = "CAPITAL_EXPENDITURE"
    DEBT_ISSUANCE = "DEBT_ISSUANCE"
    CREDIT_RATING_CHANGE = "CREDIT_RATING_CHANGE"

    # Corporate Operational
    CONTRACT_ORDER_WIN = "CONTRACT_ORDER_WIN"
    CONTRACT_CANCELLATION = "CONTRACT_CANCELLATION"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    PRODUCTION_DISRUPTION = "PRODUCTION_DISRUPTION"
    CAPACITY_EXPANSION = "CAPACITY_EXPANSION"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    STRATEGIC_INVESTMENT = "STRATEGIC_INVESTMENT"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    REGULATORY_INVESTIGATION = "REGULATORY_INVESTIGATION"
    LITIGATION = "LITIGATION"
    BANKRUPTCY_INSOLVENCY = "BANKRUPTCY_INSOLVENCY"

    # Macroeconomic
    INFLATION_CPI = "INFLATION_CPI"
    INTEREST_RATE_DECISION = "INTEREST_RATE_DECISION"
    MONETARY_POLICY_GUIDANCE = "MONETARY_POLICY_GUIDANCE"
    GDP_RELEASE = "GDP_RELEASE"
    EMPLOYMENT_DATA = "EMPLOYMENT_DATA"
    FISCAL_POLICY = "FISCAL_POLICY"
    CURRENCY_INTERVENTION = "CURRENCY_INTERVENTION"
    GEOPOLITICAL_DEVELOPMENT = "GEOPOLITICAL_DEVELOPMENT"

    # Market / Systemic
    EXCHANGE_MARKET_DISRUPTION = "EXCHANGE_MARKET_DISRUPTION"
    LIQUIDITY_EVENT = "LIQUIDITY_EVENT"
    BANK_DISTRESS = "BANK_DISTRESS"
    COMMODITY_SUPPLY_SHOCK = "COMMODITY_SUPPLY_SHOCK"

    # General / Routine
    ROUTINE_ADMINISTRATIVE = "ROUTINE_ADMINISTRATIVE"


class EventClassificationResult(BaseModel):
    """Output of hierarchical financial event classification."""

    primary_family: EventPrimaryFamily
    primary_subtype: EventSubtype
    secondary_subtypes: List[EventSubtype] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    severity_score: float = Field(default=0.5, ge=0.0, le=1.0, description="Inherent event severity [0.0, 1.0]")
    is_multi_label: bool = False
    evidence_keywords: List[str] = Field(default_factory=list)
    classifier_type: str = "DETERMINISTIC_RULE_HEAD"
    model_version: str = "1.0.0-alpha"


# Mapping from subtype to primary family
SUBTYPE_TO_FAMILY: Dict[EventSubtype, EventPrimaryFamily] = {
    # Financial
    EventSubtype.EARNINGS_ANNOUNCEMENT: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.REVENUE_SURPRISE: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.PROFIT_SURPRISE: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.GUIDANCE_REVISION: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.MARGIN_CHANGE: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.DIVIDEND_ANNOUNCEMENT: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.BUYBACK: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.CAPITAL_EXPENDITURE: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.DEBT_ISSUANCE: EventPrimaryFamily.CORPORATE_FINANCIAL,
    EventSubtype.CREDIT_RATING_CHANGE: EventPrimaryFamily.CORPORATE_FINANCIAL,

    # Operational
    EventSubtype.CONTRACT_ORDER_WIN: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.CONTRACT_CANCELLATION: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.PRODUCT_LAUNCH: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.PRODUCTION_DISRUPTION: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.CAPACITY_EXPANSION: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.MERGER_ACQUISITION: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.STRATEGIC_INVESTMENT: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.MANAGEMENT_CHANGE: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.REGULATORY_INVESTIGATION: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.LITIGATION: EventPrimaryFamily.CORPORATE_OPERATIONAL,
    EventSubtype.BANKRUPTCY_INSOLVENCY: EventPrimaryFamily.CORPORATE_OPERATIONAL,

    # Macro
    EventSubtype.INFLATION_CPI: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.INTEREST_RATE_DECISION: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.MONETARY_POLICY_GUIDANCE: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.GDP_RELEASE: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.EMPLOYMENT_DATA: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.FISCAL_POLICY: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.CURRENCY_INTERVENTION: EventPrimaryFamily.MACROECONOMIC,
    EventSubtype.GEOPOLITICAL_DEVELOPMENT: EventPrimaryFamily.MACROECONOMIC,

    # Market / Systemic
    EventSubtype.EXCHANGE_MARKET_DISRUPTION: EventPrimaryFamily.MARKET_SYSTEMIC,
    EventSubtype.LIQUIDITY_EVENT: EventPrimaryFamily.MARKET_SYSTEMIC,
    EventSubtype.BANK_DISTRESS: EventPrimaryFamily.MARKET_SYSTEMIC,
    EventSubtype.COMMODITY_SUPPLY_SHOCK: EventPrimaryFamily.MARKET_SYSTEMIC,
    EventSubtype.ROUTINE_ADMINISTRATIVE: EventPrimaryFamily.GENERAL_CORPORATE,
}

# Baseline severity weights by event subtype [0.0, 1.0]
SUBTYPE_SEVERITY_WEIGHTS: Dict[EventSubtype, float] = {
    EventSubtype.BANKRUPTCY_INSOLVENCY: 1.0,
    EventSubtype.INTEREST_RATE_DECISION: 0.9,
    EventSubtype.MERGER_ACQUISITION: 0.85,
    EventSubtype.EARNINGS_ANNOUNCEMENT: 0.8,
    EventSubtype.INFLATION_CPI: 0.8,
    EventSubtype.GUIDANCE_REVISION: 0.75,
    EventSubtype.REGULATORY_INVESTIGATION: 0.75,
    EventSubtype.CONTRACT_ORDER_WIN: 0.7,
    EventSubtype.PRODUCTION_DISRUPTION: 0.7,
    EventSubtype.CREDIT_RATING_CHANGE: 0.65,
    EventSubtype.DIVIDEND_ANNOUNCEMENT: 0.5,
    EventSubtype.BUYBACK: 0.55,
    EventSubtype.CAPITAL_EXPENDITURE: 0.5,
    EventSubtype.MANAGEMENT_CHANGE: 0.45,
    EventSubtype.ROUTINE_ADMINISTRATIVE: 0.1,
}


class FinancialEventClassifier:
    """
    Hierarchical, multi-label event classifier.
    Combines rule-based weighted keyword matching with strict category boundaries
    to classify news headlines and disclosures in sub-millisecond execution time.
    """

    def __init__(self) -> None:
        self._keyword_rules = self._build_keyword_rules()

    def _build_keyword_rules(self) -> Dict[EventSubtype, Dict[str, Any]]:
        """Define weighted keyword grammars for each event subtype."""
        return {
            # Earnings & Results
            EventSubtype.EARNINGS_ANNOUNCEMENT: {
                "patterns": [
                    r"\b(financial\s+results|q[1-4]\s+(results|net\s+profit|pat|earnings|net\s+income)|quarterly\s+(results|profit|earnings|revenue|net\s+income)|audited\s+results)\b",
                    r"\b(net\s+profit|pat|consolidated\s+profit|standalone\s+profit|quarterly\s+net\s+income)\s+(rises|falls|jumps|drops|up|down|surges|soars|grows|slumps|dips|climbs)\b",
                    r"\b(posts\s+(?:\([^\)]+\)\s+)?profit|reports\s+(?:\([^\)]+\)\s+)?profit|posts\s+(?:\([^\)]+\)\s+)?loss|reports\s+(?:\([^\)]+\)\s+)?loss)\b",
                    r"\b(quarterly\s+revenue\s+of|operating\s+profit\s+rises)\b",
                ],
                "keywords": ["earnings", "financial results", "quarterly results", "quarterly profit", "pat", "ebitda", "net profit", "net income", "results"],
                "base_confidence": 0.95,
            },
            # Guidance
            EventSubtype.GUIDANCE_REVISION: {
                "patterns": [
                    r"\b(cuts\s+guidance|raises\s+guidance|lowers\s+outlook|revenue\s+guidance|forecast\s+revision|cuts\s+revenue\s+guidance)\b",
                ],
                "keywords": ["guidance", "outlook revision", "revenue forecast", "lowers guidance", "raises guidance"],
                "base_confidence": 0.85,
            },
            # Dividend
            EventSubtype.DIVIDEND_ANNOUNCEMENT: {
                "patterns": [
                    r"\b(recommends\s+dividend|declares\s+dividend|special\s+dividend|interim\s+dividend|recommends\s+special\s+dividend)\b",
                ],
                "keywords": ["dividend", "interim dividend", "dividend payout", "special dividend"],
                "base_confidence": 0.88,
            },
            # Buyback
            EventSubtype.BUYBACK: {
                "patterns": [
                    r"\b(share\s+buyback|approves\s+buyback|tender\s+offer\s+buyback|share\s+repurchase|repurchase\s+program|authorizes\s+buyback)\b",
                ],
                "keywords": ["buyback", "share repurchase", "tender offer", "repurchase program"],
                "base_confidence": 0.90,
            },
            # Order / Contract Win
            EventSubtype.CONTRACT_ORDER_WIN: {
                "patterns": [
                    r"\b(bags\s+order|wins\s+contract|secures\s+deal|secures\s+order|awarded\s+contract|awards\s+contract|order\s+win|order\s+worth)\b",
                    r"\b(signs\s+pact|commercial\s+agreement\s+worth)\b",
                ],
                "keywords": ["bags order", "wins contract", "secures deal", "order win", "awarded contract", "secures order"],
                "base_confidence": 0.90,
            },
            # M&A
            EventSubtype.MERGER_ACQUISITION: {
                "patterns": [
                    r"\b(to\s+acquire|completes\s+acquisition|merger|takeover|buys\s+stake\s+in)\b",
                ],
                "keywords": ["acquisition", "merger", "takeover", "buys stake"],
                "base_confidence": 0.85,
            },
            # Management Change
            EventSubtype.MANAGEMENT_CHANGE: {
                "patterns": [
                    r"\b(appoints\s+ceo|steps\s+down|resigns\s+as|new\s+cfo|md\s+resignation|board\s+reconstitution)\b",
                ],
                "keywords": ["appoints", "resigns", "steps down", "chief executive", "new ceo"],
                "base_confidence": 0.85,
            },
            # Regulatory / Investigation
            EventSubtype.REGULATORY_INVESTIGATION: {
                "patterns": [
                    r"\b(sebi\s+order|show\s+cause\s+notice|probe\s+into|raids|inspection|enforcement\s+action|sebi\s+penalty|penalty\s+of|insider\s+trading)\b",
                ],
                "keywords": ["show cause", "investigation", "sebi penalty", "probe", "insider trading"],
                "base_confidence": 0.85,
            },
            # Interest Rate / Monetary Policy
            EventSubtype.INTEREST_RATE_DECISION: {
                "patterns": [
                    r"\b(repo\s+rate|rbi\s+monetary\s+policy|mpc\s+meeting|rate\s+hike|rate\s+cut|keeps\s+rates\s+unchanged|keeps\s+repo\s+rate\s+unchanged)\b",
                    r"\b(interest\s+rate\s+decision|monetary\s+policy\s+stance)\b",
                ],
                "keywords": ["repo rate", "mpc", "monetary policy", "rate hike", "rate cut"],
                "base_confidence": 0.95,
            },
            # Inflation
            EventSubtype.INFLATION_CPI: {
                "patterns": [
                    r"\b(cpi\s+inflation|retail\s+inflation|wholesale\s+inflation|wpi\s+inflation|consumer\s+price\s+index)\b",
                ],
                "keywords": ["cpi inflation", "retail inflation", "inflation rate", "consumer price index"],
                "base_confidence": 0.90,
            },
            # Capex & Expansion
            EventSubtype.CAPITAL_EXPENDITURE: {
                "patterns": [
                    r"\b(capital\s+expenditure|capex\s+plan|capex\s+commitment|investment\s+of\s+rs|to\s+invest\s+rs|expansion\s+strategy|capacity\s+expansion)\b",
                ],
                "keywords": ["capex", "capital expenditure", "green energy investment", "expansion strategy"],
                "base_confidence": 0.85,
            },
            # Routine
            EventSubtype.ROUTINE_ADMINISTRATIVE: {
                "patterns": [
                    r"\b(board\s+meeting\s+intimation|trading\s+window\s+closure|trading\s+window|loss\s+of\s+share\s+certificate|duplicate\s+share\s+certificate|postal\s+ballot|intimation\s+of\s+board\s+meeting|date\s+of\s+annual\s+general\s+meeting|schedule\s+of\s+analyst)\b",
                ],
                "keywords": ["board meeting intimation", "trading window", "share certificate", "postal ballot", "analyst meeting", "annual general meeting"],
                "base_confidence": 0.92,
            },
        }

    def classify_text(self, headline: str, body_text: Optional[str] = None) -> EventClassificationResult:
        """
        Classify news text into primary and secondary event taxonomy subtypes.
        """
        combined = f"{headline} {body_text or ''}".lower()
        scored_subtypes: List[Tuple[EventSubtype, float, List[str]]] = []

        for subtype, rule in self._keyword_rules.items():
            score = 0.0
            matched_evidence: List[str] = []

            # Check regex patterns
            for pattern in rule["patterns"]:
                match = re.search(pattern, combined)
                if match:
                    score += 0.5
                    matched_evidence.append(match.group(0))

            # Check keyword occurrences
            for kw in rule["keywords"]:
                if kw in combined:
                    score += 0.2
                    matched_evidence.append(kw)

            if score > 0.3:
                conf = min(rule["base_confidence"] * (score / 0.7), 0.98)
                scored_subtypes.append((subtype, conf, list(set(matched_evidence))))

        # Sort by confidence descending
        scored_subtypes.sort(key=lambda x: x[1], reverse=True)

        if not scored_subtypes:
            # Fallback to routine administrative or general corporate
            return EventClassificationResult(
                primary_family=EventPrimaryFamily.GENERAL_CORPORATE,
                primary_subtype=EventSubtype.ROUTINE_ADMINISTRATIVE,
                confidence=0.50,
                severity_score=0.1,
                classifier_type="DETERMINISTIC_RULE_HEAD",
            )

        # Precedence Override: Routine Board Meeting Intimations
        if re.search(
            r"\b(intimation\s+of\s+board\s+meeting|board\s+meeting\s+to\s+consider|trading\s+window\s+closure|loss\s+of\s+share\s+certificate|duplicate\s+share\s+certificate)\b",
            combined,
        ):
            return EventClassificationResult(
                primary_family=EventPrimaryFamily.GENERAL_CORPORATE,
                primary_subtype=EventSubtype.ROUTINE_ADMINISTRATIVE,
                confidence=0.95,
                severity_score=0.10,
                evidence_keywords=["board meeting intimation"],
                classifier_type="DETERMINISTIC_RULE_HEAD",
            )

        # Precedence Rule: Earnings vs Dividend Co-occurrence
        # If an announcement reports both quarterly results and dividend declaration,
        # EARNINGS_ANNOUNCEMENT is primary and DIVIDEND_ANNOUNCEMENT is secondary.
        subtypes_present = {st for st, _, _ in scored_subtypes}
        if EventSubtype.EARNINGS_ANNOUNCEMENT in subtypes_present and EventSubtype.DIVIDEND_ANNOUNCEMENT in subtypes_present:
            # Reorder so EARNINGS_ANNOUNCEMENT is first
            earn_tuple = next(t for t in scored_subtypes if t[0] == EventSubtype.EARNINGS_ANNOUNCEMENT)
            scored_subtypes = [earn_tuple] + [t for t in scored_subtypes if t[0] != EventSubtype.EARNINGS_ANNOUNCEMENT]

        top_subtype, top_conf, top_evidence = scored_subtypes[0]
        top_family = SUBTYPE_TO_FAMILY.get(top_subtype, EventPrimaryFamily.GENERAL_CORPORATE)
        severity = SUBTYPE_SEVERITY_WEIGHTS.get(top_subtype, 0.5)

        # Secondary subtypes (multi-label)
        secondary: List[EventSubtype] = []
        for st, c, _ in scored_subtypes[1:]:
            if c >= 0.60:
                secondary.append(st)

        return EventClassificationResult(
            primary_family=top_family,
            primary_subtype=top_subtype,
            secondary_subtypes=secondary,
            confidence=round(top_conf, 3),
            severity_score=severity,
            is_multi_label=len(secondary) > 0,
            evidence_keywords=top_evidence,
            classifier_type="DETERMINISTIC_RULE_HEAD",
        )


_event_classifier: Optional[FinancialEventClassifier] = None


def get_event_classifier() -> FinancialEventClassifier:
    """Return shared FinancialEventClassifier singleton."""
    global _event_classifier
    if _event_classifier is None:
        _event_classifier = FinancialEventClassifier()
    return _event_classifier
