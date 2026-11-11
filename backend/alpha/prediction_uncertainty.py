"""Probabilistic Forecasts, Uncertainty Calibration & Abstention Policy (Phase 5 WP-F).

Transforms raw point/distribution predictions into calibrated probabilities via
validation-only Platt Scaling or Isotonic Regression (PAVA), computes conformal
interval scale adjustments, evaluates Brier score / Log Loss / ECE / Reliability Diagrams /
Pinball Loss / Coverage, and enforces principled NO_TRADE abstention whenever confidence,
entity resolution, historical evidence, market context, uncertainty, or net cost edge fails.
"""

from __future__ import annotations

from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.feature_registry import ModelConfiguration
from backend.alpha.predictive_models import RawModelPrediction


class CalibrationMethod(str, Enum):
    PLATT = "PLATT"
    ISOTONIC = "ISOTONIC"
    NONE = "NONE"


class AbstentionReason(str, Enum):
    LOW_CLASSIFICATION_CONFIDENCE = "LOW_CLASSIFICATION_CONFIDENCE"
    AMBIGUOUS_ENTITY_RESOLUTION = "AMBIGUOUS_ENTITY_RESOLUTION"
    INSUFFICIENT_HISTORICAL_EVIDENCE = "INSUFFICIENT_HISTORICAL_EVIDENCE"
    UNAVAILABLE_OR_STALE_MARKET = "UNAVAILABLE_OR_STALE_MARKET"
    HIGH_PREDICTIVE_UNCERTAINTY = "HIGH_PREDICTIVE_UNCERTAINTY"
    EXPECTED_RETURN_BELOW_COSTS = "EXPECTED_RETURN_BELOW_COSTS"
    SUB_THRESHOLD_DIRECTIONAL_PROB = "SUB_THRESHOLD_DIRECTIONAL_PROB"


class ReliabilityBin(BaseModel):
    bin_index: int
    lower_bound: float
    upper_bound: float
    count: int
    mean_predicted_prob: float
    empirical_frequency: float
    calibration_gap: float


class CalibrationReport(BaseModel):
    n_samples: int
    brier_score: float
    log_loss: float
    ece: float
    pinball_loss_by_quantile: Dict[str, float] = Field(default_factory=dict)
    interval_coverage_80pct: Optional[float] = None
    interval_coverage_50pct: Optional[float] = None
    reliability_diagram: List[ReliabilityBin] = Field(default_factory=list)


class CalibratedPrediction(BaseModel):
    """Final probabilistic, uncertainty-aware forecast with explicit NO_TRADE state."""

    event_id: str
    instrument_id: str
    expected_return: float
    median_return: float
    p_positive: float = Field(..., ge=0.0, le=1.0)
    p_negative: float = Field(..., ge=0.0, le=1.0)
    p_material_move: float = Field(..., ge=0.0, le=1.0)
    q10: float
    q25: float
    q50: float
    q75: float
    q90: float
    predictive_std: float = Field(..., ge=0.0)
    signal_direction: int = 0  # +1 long, -1 short, 0 NO_TRADE
    abstain: bool = True
    action_state: str = "NO_TRADE"  # "LONG", "SHORT", "NO_TRADE"
    abstain_reasons: List[AbstentionReason] = Field(default_factory=list)
    estimated_round_trip_cost_bps: float = 0.0
    net_expected_edge_bps: float = 0.0


class _PlattScaler:
    """L2-regularized 1D logistic calibration fitted solely on validation predictions."""

    def __init__(self) -> None:
        self.a: float = 1.0
        self.b: float = 0.0
        self.is_fitted: bool = False

    def fit(self, raw_probs: np.ndarray, y_true: np.ndarray) -> "_PlattScaler":
        if raw_probs.size < 4 or np.all(y_true == y_true[0]):
            self.a = 1.0
            self.b = 0.0
            self.is_fitted = True
            return self
        p_clip = np.clip(raw_probs, 1e-4, 1.0 - 1e-4)
        logits = np.log(p_clip / (1.0 - p_clip))
        X = np.column_stack([logits, np.ones_like(logits)])
        prior = float(np.clip(np.mean(y_true), 0.02, 0.98))
        theta = np.array([0.5, math.log(prior / (1.0 - prior))], dtype=np.float64)
        reg = np.diag([1e-2, 1e-4])

        def _obj(th: np.ndarray) -> float:
            pr = np.clip(1.0 / (1.0 + np.exp(-np.clip(X @ th, -30.0, 30.0))), 1e-9, 1.0 - 1e-9)
            nll = -float(np.sum(y_true * np.log(pr) + (1.0 - y_true) * np.log(1.0 - pr)))
            return nll + 0.5 * float(th @ (reg @ th))

        cur_obj = _obj(theta)
        for _ in range(40):
            s = 1.0 / (1.0 + np.exp(-np.clip(X @ theta, -30.0, 30.0)))
            w = np.clip(s * (1.0 - s), 1e-6, 0.25)
            grad = X.T @ (s - y_true) + reg @ theta
            H = (X.T * w) @ X + reg
            try:
                step = np.linalg.solve(H, grad)
            except np.linalg.LinAlgError:
                break
            alpha_ls = 1.0
            accepted = False
            while alpha_ls >= 1e-3:
                cand = theta - alpha_ls * step
                cand_obj = _obj(cand)
                if cand_obj <= cur_obj + 1e-12:
                    theta = cand
                    cur_obj = cand_obj
                    accepted = True
                    break
                alpha_ls *= 0.5
            if not accepted or float(np.max(np.abs(alpha_ls * step))) < 1e-6:
                break
        # Keep monotonic sign (a >= 0.05) so probabilities remain non-inverted
        self.a = max(0.05, float(theta[0]))
        self.b = float(theta[1])
        self.is_fitted = True
        return self

    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        p_clip = np.clip(raw_probs, 1e-4, 1.0 - 1e-4)
        logits = np.log(p_clip / (1.0 - p_clip))
        z = np.clip(self.a * logits + self.b, -30.0, 30.0)
        return np.clip(1.0 / (1.0 + np.exp(-z)), 1e-4, 1.0 - 1e-4)


class _IsotonicPAVA:
    """Monotonic Pool Adjacent Violators Algorithm (PAVA) calibrator."""

    def __init__(self) -> None:
        self.thresholds: np.ndarray = np.array([0.0, 1.0])
        self.values: np.ndarray = np.array([0.5, 0.5])

    def fit(self, raw_probs: np.ndarray, y_true: np.ndarray) -> "_IsotonicPAVA":
        if raw_probs.size < 4:
            p = float(np.clip(np.mean(y_true), 0.01, 0.99)) if y_true.size else 0.5
            self.thresholds = np.array([0.0, 1.0])
            self.values = np.array([p, p])
            return self
        order = np.argsort(raw_probs)
        x_s = raw_probs[order]
        y_s = y_true[order].astype(np.float64)

        blocks: List[List[float]] = [[float(yi), 1.0, float(xi), float(xi)] for xi, yi in zip(x_s, y_s)]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][0] > blocks[i + 1][0]:
                tot_w = blocks[i][1] + blocks[i + 1][1]
                avg = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / tot_w
                blocks[i] = [avg, tot_w, blocks[i][2], blocks[i + 1][3]]
                blocks.pop(i + 1)
                if i > 0:
                    i -= 1
            else:
                i += 1
        self.thresholds = np.array([0.5 * (b[2] + b[3]) for b in blocks], dtype=np.float64)
        self.values = np.clip(np.array([b[0] for b in blocks], dtype=np.float64), 1e-4, 1.0 - 1e-4)
        return self

    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        return np.interp(raw_probs, self.thresholds, self.values)


def compute_calibration_metrics(
    probs: Sequence[float],
    y_binary: Sequence[int],
    y_continuous: Optional[Sequence[float]] = None,
    q10_preds: Optional[Sequence[float]] = None,
    q25_preds: Optional[Sequence[float]] = None,
    q50_preds: Optional[Sequence[float]] = None,
    q75_preds: Optional[Sequence[float]] = None,
    q90_preds: Optional[Sequence[float]] = None,
    n_bins: int = 10,
) -> CalibrationReport:
    """Compute Brier score, Log Loss, ECE, Reliability Diagram, Pinball Loss, and Interval Coverage."""
    p = np.clip(np.asarray(probs, dtype=np.float64), 1e-6, 1.0 - 1e-6)
    y = np.asarray(y_binary, dtype=np.float64)
    n = len(p)
    if n == 0:
        return CalibrationReport(n_samples=0, brier_score=0.0, log_loss=0.0, ece=0.0)

    brier = float(np.mean((p - y) ** 2))
    log_l = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

    bins: List[ReliabilityBin] = []
    ece = 0.0
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    for b_idx in range(n_bins):
        lo, hi = float(edges[b_idx]), float(edges[b_idx + 1])
        mask = (p >= lo) & (p <= hi if b_idx == n_bins - 1 else p < hi)
        cnt = int(np.sum(mask))
        if cnt == 0:
            continue
        mp = float(np.mean(p[mask]))
        ef = float(np.mean(y[mask]))
        gap = abs(ef - mp)
        ece += (cnt / n) * gap
        bins.append(
            ReliabilityBin(
                bin_index=b_idx,
                lower_bound=round(lo, 3),
                upper_bound=round(hi, 3),
                count=cnt,
                mean_predicted_prob=round(mp, 6),
                empirical_frequency=round(ef, 6),
                calibration_gap=round(gap, 6),
            )
        )

    pinball: Dict[str, float] = {}
    cov80: Optional[float] = None
    cov50: Optional[float] = None
    if y_continuous is not None:
        yc = np.asarray(y_continuous, dtype=np.float64)
        for tau, arr in [(0.10, q10_preds), (0.25, q25_preds), (0.50, q50_preds), (0.75, q75_preds), (0.90, q90_preds)]:
            if arr is not None and len(arr) == n:
                qa = np.asarray(arr, dtype=np.float64)
                diff = yc - qa
                loss = np.maximum(tau * diff, (tau - 1.0) * diff)
                pinball[f"q{int(tau*100)}"] = round(float(np.mean(loss)), 6)
        if q10_preds is not None and q90_preds is not None:
            q10a = np.asarray(q10_preds, dtype=np.float64)
            q90a = np.asarray(q90_preds, dtype=np.float64)
            cov80 = round(float(np.mean((yc >= q10a) & (yc <= q90a))), 4)
        if q25_preds is not None and q75_preds is not None:
            q25a = np.asarray(q25_preds, dtype=np.float64)
            q75a = np.asarray(q75_preds, dtype=np.float64)
            cov50 = round(float(np.mean((yc >= q25a) & (yc <= q75a))), 4)

    return CalibrationReport(
        n_samples=n,
        brier_score=round(brier, 6),
        log_loss=round(log_l, 6),
        ece=round(float(ece), 6),
        pinball_loss_by_quantile=pinball,
        interval_coverage_80pct=cov80,
        interval_coverage_50pct=cov50,
        reliability_diagram=bins,
    )


class UncertaintyCalibratorAndGate:
    """
    Fits validation-only probability & conformal interval calibration and applies
    6 explicit abstention checks producing `CalibratedPrediction`.
    """

    def __init__(
        self,
        method: CalibrationMethod = CalibrationMethod.PLATT,
        model_config: ModelConfiguration = ModelConfiguration.MODEL_D_FULL_SYSTEM,
        min_cls_confidence: float = 0.50,
        min_entity_confidence: float = 0.50,
        min_directional_prob: float = 0.52,
        max_predictive_std: float = 0.15,
        min_edge_over_cost_bps: float = 2.0,
        require_entity_for_event_models: bool = True,
        require_historical_for_h_models: bool = True,
        require_market_for_m_models: bool = True,
    ) -> None:
        self.method = method
        self.model_config = model_config
        self.min_cls_confidence = min_cls_confidence
        self.min_entity_confidence = min_entity_confidence
        self.min_directional_prob = min_directional_prob
        self.max_predictive_std = max_predictive_std
        self.min_edge_over_cost_bps = min_edge_over_cost_bps
        self.require_entity_for_event_models = require_entity_for_event_models
        self.require_historical_for_h_models = require_historical_for_h_models
        self.require_market_for_m_models = require_market_for_m_models

        self._pos_cal: Any = None
        self._neg_cal: Any = None
        self._move_cal: Any = None
        self._conformal_q_pad: float = 0.0
        self.is_calibrated: bool = False

    def _new_calibrator(self) -> Any:
        return _IsotonicPAVA() if self.method == CalibrationMethod.ISOTONIC else _PlattScaler()

    def fit_calibration(
        self,
        val_raw_preds: Sequence[RawModelPrediction],
        val_y_continuous: Sequence[float],
        val_y_pos: Sequence[int],
        val_y_neg: Sequence[int],
    ) -> "UncertaintyCalibratorAndGate":
        """Fit probability calibration & conformal symmetric padding strictly on held-out validation fold."""
        if not val_raw_preds:
            self.is_calibrated = False
            return self

        p_pos = np.array([r.p_positive for r in val_raw_preds], dtype=np.float64)
        p_neg = np.array([r.p_negative for r in val_raw_preds], dtype=np.float64)
        p_mov = np.array([r.p_material_move for r in val_raw_preds], dtype=np.float64)
        yp = np.asarray(val_y_pos, dtype=np.float64)
        yn = np.asarray(val_y_neg, dtype=np.float64)
        ym = np.clip(yp + yn, 0.0, 1.0)
        yc = np.asarray(val_y_continuous, dtype=np.float64)

        if self.method != CalibrationMethod.NONE:
            self._pos_cal = self._new_calibrator().fit(p_pos, yp)
            self._neg_cal = self._new_calibrator().fit(p_neg, yn)
            self._move_cal = self._new_calibrator().fit(p_mov, ym)

        # Split-conformal nonconformity score on [q10, q90] targeting >= 80% coverage
        q10s = np.array([r.q10 for r in val_raw_preds], dtype=np.float64)
        q90s = np.array([r.q90 for r in val_raw_preds], dtype=np.float64)
        scores = np.maximum(q10s - yc, yc - q90s)
        if scores.size >= 5:
            q_level = min(1.0, (0.80 * (scores.size + 1)) / scores.size)
            self._conformal_q_pad = max(0.0, float(np.quantile(scores, q_level)))
        else:
            self._conformal_q_pad = 0.0

        self.is_calibrated = True
        return self

    def calibrate_and_gate(
        self,
        event_ids: Sequence[str],
        instrument_ids: Sequence[str],
        raw_preds: Sequence[RawModelPrediction],
        feature_dicts: Sequence[Dict[str, Optional[float]]],
        round_trip_cost_bps_list: Optional[Sequence[float]] = None,
    ) -> List[CalibratedPrediction]:
        if not raw_preds:
            return []

        raw_pos = np.array([r.p_positive for r in raw_preds], dtype=np.float64)
        raw_neg = np.array([r.p_negative for r in raw_preds], dtype=np.float64)
        raw_mov = np.array([r.p_material_move for r in raw_preds], dtype=np.float64)

        cal_pos = self._pos_cal.transform(raw_pos) if (self.is_calibrated and self._pos_cal) else raw_pos
        cal_neg = self._neg_cal.transform(raw_neg) if (self.is_calibrated and self._neg_cal) else raw_neg
        cal_mov = self._move_cal.transform(raw_mov) if (self.is_calibrated and self._move_cal) else raw_mov

        # Renormalize mutually exclusive directional probabilities when sum > 1
        pair_sum = cal_pos + cal_neg
        over = pair_sum > 1.0
        cal_pos = np.where(over, cal_pos / np.maximum(pair_sum, 1e-8), cal_pos)
        cal_neg = np.where(over, cal_neg / np.maximum(pair_sum, 1e-8), cal_neg)

        groups = self.model_config.active_groups
        uses_e = any(g.value == "E" for g in groups)
        uses_h = any(g.value == "H" for g in groups)
        uses_m = any(g.value == "M" for g in groups)

        outputs: List[CalibratedPrediction] = []
        for i, rp in enumerate(raw_preds):
            feats = feature_dicts[i]
            cost_bps = float(round_trip_cost_bps_list[i]) if round_trip_cost_bps_list else 8.0
            reasons: List[AbstentionReason] = []

            if uses_e:
                cls_c = feats.get("e_cls_confidence")
                if cls_c is not None and cls_c < self.min_cls_confidence:
                    reasons.append(AbstentionReason.LOW_CLASSIFICATION_CONFIDENCE)
                if self.require_entity_for_event_models:
                    ent_res = feats.get("e_entity_resolved")
                    ent_c = feats.get("e_entity_confidence")
                    if (ent_res is not None and ent_res < 0.5) or (ent_c is not None and ent_c < self.min_entity_confidence):
                        reasons.append(AbstentionReason.AMBIGUOUS_ENTITY_RESOLUTION)

            if uses_h and self.require_historical_for_h_models:
                insuff = feats.get("h_insufficient_evidence")
                if insuff is not None and insuff > 0.5:
                    reasons.append(AbstentionReason.INSUFFICIENT_HISTORICAL_EVIDENCE)

            if uses_m and self.require_market_for_m_models:
                m_av = feats.get("m_available")
                m_st = feats.get("m_is_stale")
                if (m_av is not None and m_av < 0.5) or (m_st is not None and m_st > 0.5):
                    reasons.append(AbstentionReason.UNAVAILABLE_OR_STALE_MARKET)

            if rp.predictive_std > self.max_predictive_std:
                reasons.append(AbstentionReason.HIGH_PREDICTIVE_UNCERTAINTY)

            gross_edge_bps = abs(rp.expected_return) * 10000.0
            net_edge_bps = gross_edge_bps - cost_bps
            if net_edge_bps < self.min_edge_over_cost_bps:
                reasons.append(AbstentionReason.EXPECTED_RETURN_BELOW_COSTS)

            pp = float(cal_pos[i])
            pn = float(cal_neg[i])
            candidate_dir = 1 if rp.expected_return >= 0 else -1
            dir_prob = pp if candidate_dir > 0 else pn
            if dir_prob < self.min_directional_prob:
                reasons.append(AbstentionReason.SUB_THRESHOLD_DIRECTIONAL_PROB)

            abstain = len(reasons) > 0
            sig_dir = 0 if abstain else candidate_dir
            action_state = "NO_TRADE" if abstain else ("LONG" if sig_dir > 0 else "SHORT")

            outputs.append(
                CalibratedPrediction(
                    event_id=event_ids[i],
                    instrument_id=instrument_ids[i],
                    expected_return=round(rp.expected_return, 6),
                    median_return=round(rp.median_return, 6),
                    p_positive=round(pp, 6),
                    p_negative=round(pn, 6),
                    p_material_move=round(float(cal_mov[i]), 6),
                    q10=round(rp.q10 - self._conformal_q_pad, 6),
                    q25=round(rp.q25 - 0.5 * self._conformal_q_pad, 6),
                    q50=round(rp.q50, 6),
                    q75=round(rp.q75 + 0.5 * self._conformal_q_pad, 6),
                    q90=round(rp.q90 + self._conformal_q_pad, 6),
                    predictive_std=round(rp.predictive_std, 6),
                    signal_direction=sig_dir,
                    abstain=abstain,
                    action_state=action_state,
                    abstain_reasons=reasons,
                    estimated_round_trip_cost_bps=round(cost_bps, 3),
                    net_expected_edge_bps=round(net_edge_bps, 3),
                )
            )
        return outputs
