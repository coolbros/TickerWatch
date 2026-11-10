"""Composite Financial Materiality and Event Relevance Engine."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.alpha.event_classifier import EventClassificationResult, EventPrimaryFamily, EventSubtype
from backend.alpha.event_extraction import FinancialExtractionResult
from backend.alpha.surprise import NumericalSurpriseResult, SurpriseCategory


class MaterialityTier(str, Enum):
    TIER_1_ROUTINE = "TIER_1_ROUTINE"          # [0.00, 0.35)
    TIER_2_MODERATE = "TIER_2_MODERATE"        # [0.35, 0.70)
    TIER_3_HIGH = "TIER_3_HIGH"                # [0.70, 1.00]


class MaterialityAssessment(BaseModel):
    """
    Multidimensional, composite materiality evaluation of a financial event.
    Strictly distinct from sentiment and financial surprise.
    """

    composite_materiality_score: float = Field(..., ge=0.0, le=1.0)
    materiality_tier: MaterialityTier
    confidence: float = Field(..., ge=0.0, le=1.0)

    # Component sub-scores [0.0, 1.0]
    category_intrinsic_score: float = Field(..., ge=0.0, le=1.0)
    quantitative_magnitude_score: float = Field(..., ge=0.0, le=1.0)
    forward_guidance_score: float = Field(..., ge=0.0, le=1.0)
    novelty_score: float = Field(..., ge=0.0, le=1.0)
    spillover_potential_score: float = Field(..., ge=0.0, le=1.0)

    # Explanation and audit trail
    rationale: str = ""
    weights: Dict[str, float] = Field(default_factory=dict)
    evaluator_version: str = "1.0.0-alpha"


class FinancialMaterialityEngine:
    """
    Computes a calibrated, scalar materiality score [0.0, 1.0] for any financial event.
    Evaluates intrinsic event severity, numerical magnitude/surprise ratio, forward-looking
    guidance prominence, announcement novelty, and market/systemic spillover.
    """

    DEFAULT_WEIGHTS = {
        "category": 0.35,
        "magnitude": 0.25,
        "forward": 0.15,
        "novelty": 0.15,
        "spillover": 0.10,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None) -> None:
        self.weights = weights or self.DEFAULT_WEIGHTS
        # Normalize weights to sum to 1.0
        total_w = sum(self.weights.values())
        self.weights = {k: v / total_w for k, v in self.weights.items()}

    def evaluate_materiality(
        self,
        classification: EventClassificationResult,
        extraction: Optional[FinancialExtractionResult] = None,
        surprise: Optional[NumericalSurpriseResult] = None,
        is_first_announcement: bool = True,
        is_index_constituent: bool = True,
    ) -> MaterialityAssessment:
        """
        Evaluate composite materiality given event classification, numerical extraction, and surprise.
        """
        # 1. Category Intrinsic Score
        cat_score = classification.severity_score

        # 2. Quantitative Magnitude Score
        mag_score = 0.30  # Default baseline for unquantified events
        if surprise is not None:
            if surprise.materiality_ratio is not None:
                # Order / Capex ratio relative to annual revenue
                ratio = surprise.materiality_ratio
                if ratio >= 0.10:      # >= 10% of annual revenue
                    mag_score = 1.0
                elif ratio >= 0.05:    # >= 5% of annual revenue
                    mag_score = 0.85
                elif ratio >= 0.02:    # >= 2% of annual revenue
                    mag_score = 0.65
                elif ratio >= 0.005:   # >= 0.5% of annual revenue
                    mag_score = 0.45
                else:
                    mag_score = 0.25
            elif surprise.percentage_surprise is not None:
                # Based on percentage beat/miss
                abs_pct = abs(surprise.percentage_surprise)
                if abs_pct >= 25.0:
                    mag_score = 0.95
                elif abs_pct >= 15.0:
                    mag_score = 0.80
                elif abs_pct >= 5.0:
                    mag_score = 0.60
                elif abs_pct >= 1.0:
                    mag_score = 0.40
                else:
                    mag_score = 0.20  # In-line
        elif extraction is not None and extraction.metrics:
            # We have monetary metrics but no consensus expectation
            has_large_monetary = any(
                m.normalized_value >= 1e9 for m in extraction.metrics if not m.is_percentage
            )
            mag_score = 0.60 if has_large_monetary else 0.40

        # 3. Forward-looking Guidance Score
        fwd_score = 0.20
        if classification.primary_subtype == EventSubtype.GUIDANCE_REVISION:
            fwd_score = 0.95
        elif EventSubtype.GUIDANCE_REVISION in classification.secondary_subtypes:
            fwd_score = 0.80
        elif classification.primary_family == EventPrimaryFamily.MACROECONOMIC:
            fwd_score = 0.70
        elif classification.primary_subtype in (
            EventSubtype.CAPITAL_EXPENDITURE,
            EventSubtype.MERGER_ACQUISITION,
            EventSubtype.CONTRACT_ORDER_WIN,
        ):
            fwd_score = 0.65

        # 4. Novelty Score
        nov_score = 0.90 if is_first_announcement else 0.40
        if classification.primary_subtype == EventSubtype.ROUTINE_ADMINISTRATIVE:
            nov_score = 0.15

        # 5. Spillover Potential Score
        if classification.primary_family in (
            EventPrimaryFamily.MACROECONOMIC,
            EventPrimaryFamily.MARKET_SYSTEMIC,
        ):
            spillover_score = 0.95
        elif is_index_constituent:
            spillover_score = 0.65
        else:
            spillover_score = 0.20

        # Composite weighted sum
        composite = (
            self.weights["category"] * cat_score
            + self.weights["magnitude"] * mag_score
            + self.weights["forward"] * fwd_score
            + self.weights["novelty"] * nov_score
            + self.weights["spillover"] * spillover_score
        )
        composite = round(max(0.0, min(1.0, composite)), 3)

        # Determine Tier
        if composite < 0.35:
            tier = MaterialityTier.TIER_1_ROUTINE
        elif composite < 0.70:
            tier = MaterialityTier.TIER_2_MODERATE
        else:
            tier = MaterialityTier.TIER_3_HIGH

        # Confidence based on inputs completeness
        confidence = 0.85
        if surprise is None or surprise.surprise_category == SurpriseCategory.UNAVAILABLE_EXPECTATION:
            confidence -= 0.10
        if not extraction or not extraction.metrics:
            confidence -= 0.05
        confidence = round(max(0.50, min(0.98, confidence)), 2)

        rationale = (
            f"Event classified as {classification.primary_subtype.value} (severity {cat_score:.2f}). "
            f"Magnitude {mag_score:.2f}, Guidance {fwd_score:.2f}, Novelty {nov_score:.2f}, "
            f"Spillover {spillover_score:.2f} -> Composite Materiality {composite:.3f} ({tier.value})."
        )

        return MaterialityAssessment(
            composite_materiality_score=composite,
            materiality_tier=tier,
            confidence=confidence,
            category_intrinsic_score=cat_score,
            quantitative_magnitude_score=mag_score,
            forward_guidance_score=fwd_score,
            novelty_score=nov_score,
            spillover_potential_score=spillover_score,
            rationale=rationale,
            weights=self.weights,
        )


_materiality_engine: Optional[FinancialMaterialityEngine] = None


def get_materiality_engine() -> FinancialMaterialityEngine:
    """Return shared singleton FinancialMaterialityEngine."""
    global _materiality_engine
    if _materiality_engine is None:
        _materiality_engine = FinancialMaterialityEngine()
    return _materiality_engine
