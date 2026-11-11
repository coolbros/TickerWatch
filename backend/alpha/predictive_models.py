"""Baseline & Classical Machine Learning Predictive Models (Phase 5 WP-E).

Implements deterministic, reproducible estimators operating identically across
ablation Model Configurations A-E (`M`, `E`, `E+H`, `E+H+M`, `E+M`):
1. Simple Baselines:
   - Zero-return baseline
   - Historical unconditional mean baseline
   - Causal rolling instrument-level baseline
   - Event family/subtype hierarchical shrinkage baseline
2. Classical ML Models (pure vectorized NumPy — zero heavyweight deps):
   - Ridge Regression + L2 Logistic probability heads
   - Elastic Net (L1+L2 Coordinate Descent)
   - L2 Logistic Direction Model
   - Random Forest Ensemble (with cross-tree uncertainty & quantiles)
   - Gradient-Boosted Decision Trees + Quantile GBDT (`q10`/`q50`/`q90` pinball loss)
   - Direct Linear Quantile Regressor (IRLS Pinball Loss)
   - Compact Sample-Gated 1-Hidden-Layer MLP (gated by minimum sample size)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.feature_registry import (
    FoldSafeFeatureTransformer,
    ModelConfiguration,
    get_feature_names_for_config,
)


class ModelAlgorithm(str, Enum):
    ZERO_RETURN = "ZERO_RETURN"
    UNCONDITIONAL_MEAN = "UNCONDITIONAL_MEAN"
    ROLLING_INSTRUMENT = "ROLLING_INSTRUMENT"
    EVENT_SUBTYPE_SHRINKAGE = "EVENT_SUBTYPE_SHRINKAGE"
    RIDGE = "RIDGE"
    ELASTIC_NET = "ELASTIC_NET"
    LOGISTIC_DIRECTION = "LOGISTIC_DIRECTION"
    RANDOM_FOREST = "RANDOM_FOREST"
    GBDT = "GBDT"
    QUANTILE_LINEAR = "QUANTILE_LINEAR"
    COMPACT_MLP = "COMPACT_MLP"


class RawModelPrediction(BaseModel):
    """Un-calibrated direct predictive output prior to WP-F uncertainty calibration & abstention."""

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


def _sigmoid(z: np.ndarray) -> np.ndarray:
    zc = np.clip(z, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-zc))


def _fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float = 1.0) -> Tuple[np.ndarray, float]:
    n, d = X.shape
    if n == 0:
        return np.zeros(d, dtype=np.float64), 0.0
    x_mean = np.mean(X, axis=0)
    y_mean = float(np.mean(y))
    Xc = X - x_mean
    yc = y - y_mean
    gram = Xc.T @ Xc + alpha * np.eye(d, dtype=np.float64)
    w = np.linalg.solve(gram, Xc.T @ yc)
    b = y_mean - float(x_mean @ w)
    return w, b


def _fit_elastic_net(
    X: np.ndarray,
    y: np.ndarray,
    l1_alpha: float = 1e-4,
    l2_alpha: float = 1e-2,
    max_iter: int = 200,
) -> Tuple[np.ndarray, float]:
    n, d = X.shape
    if n == 0 or d == 0:
        return np.zeros(d, dtype=np.float64), 0.0
    x_mean = np.mean(X, axis=0)
    y_mean = float(np.mean(y))
    Xc = X - x_mean
    yc = y - y_mean
    w = np.zeros(d, dtype=np.float64)
    residual = yc.copy()
    col_sq = np.sum(Xc * Xc, axis=0) / max(1, n)

    for _ in range(max_iter):
        max_delta = 0.0
        for j in range(d):
            denom = col_sq[j] + l2_alpha
            if denom < 1e-12:
                continue
            old_wj = w[j]
            r_no_j = residual + Xc[:, j] * old_wj
            rho = float(np.dot(Xc[:, j], r_no_j)) / n
            if rho > l1_alpha:
                new_wj = (rho - l1_alpha) / denom
            elif rho < -l1_alpha:
                new_wj = (rho + l1_alpha) / denom
            else:
                new_wj = 0.0
            if new_wj != old_wj:
                w[j] = new_wj
                residual = r_no_j - Xc[:, j] * new_wj
                max_delta = max(max_delta, abs(new_wj - old_wj))
        if max_delta < 1e-7:
            break
    b = y_mean - float(x_mean @ w)
    return w, b


def _fit_logistic_l2(
    X: np.ndarray,
    y_bin: np.ndarray,
    l2_reg: float = 1.0,
    max_iter: int = 25,
) -> Tuple[Optional[Tuple[np.ndarray, float]], float]:
    """Damped Newton-IRLS L2 logistic regression. Returns (weights_tuple_or_None, fallback_prior)."""
    n, d = X.shape
    if n == 0:
        return None, 0.5
    pos_rate = float(np.clip(np.mean(y_bin), 0.01, 0.99))
    if np.all(y_bin == y_bin[0]):
        return None, pos_rate

    w = np.zeros(d, dtype=np.float64)
    b = math.log(pos_rate / (1.0 - pos_rate))
    Xa = np.hstack([X, np.ones((n, 1), dtype=np.float64)])
    theta = np.concatenate([w, [b]])
    reg_diag = np.diag([l2_reg] * d + [1e-6])

    def _obj_log(th: np.ndarray) -> float:
        pr = np.clip(_sigmoid(Xa @ th), 1e-9, 1.0 - 1e-9)
        nll = -float(np.sum(y_bin * np.log(pr) + (1.0 - y_bin) * np.log(1.0 - pr)))
        return nll + 0.5 * float(th @ (reg_diag @ th))

    cur_obj = _obj_log(theta)
    for _ in range(max_iter):
        logits = Xa @ theta
        p = _sigmoid(logits)
        weights = np.clip(p * (1.0 - p), 1e-6, 0.25)
        grad = Xa.T @ (p - y_bin) + reg_diag @ theta
        H = (Xa.T * weights) @ Xa + reg_diag
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        alpha_ls = 1.0
        accepted = False
        while alpha_ls >= 1e-3:
            cand = theta - alpha_ls * step
            cand_obj = _obj_log(cand)
            if cand_obj <= cur_obj + 1e-12:
                theta = cand
                cur_obj = cand_obj
                accepted = True
                break
            alpha_ls *= 0.5
        if not accepted or float(np.max(np.abs(alpha_ls * step))) < 1e-6:
            break
    return (theta[:-1], float(theta[-1])), pos_rate


def _predict_logistic(model_tuple: Optional[Tuple[np.ndarray, float]], fallback_prior: float, X: np.ndarray) -> np.ndarray:
    if model_tuple is None:
        return np.full(X.shape[0], fallback_prior, dtype=np.float64)
    w, b = model_tuple
    return np.clip(_sigmoid(X @ w + b), 1e-4, 1.0 - 1e-4)


def _fit_quantile_irls(
    X: np.ndarray,
    y: np.ndarray,
    tau: float,
    l2_reg: float = 1.0,
    max_iter: int = 30,
) -> Tuple[np.ndarray, float]:
    """L2-regularized quantile regression via iteratively reweighted least squares."""
    n, d = X.shape
    if n == 0:
        return np.zeros(d, dtype=np.float64), 0.0
    w, b = _fit_ridge(X, y, alpha=l2_reg)
    eps = 1e-4
    Xa = np.hstack([X, np.ones((n, 1), dtype=np.float64)])
    theta = np.concatenate([w, [b]])
    reg_mat = np.diag([l2_reg] * d + [1e-8])

    for _ in range(max_iter):
        preds = Xa @ theta
        res = y - preds
        # Subgradient asymmetric weights for pinball loss
        w_pin = np.where(res >= 0, tau, 1.0 - tau)
        weights = w_pin / np.maximum(np.abs(res), eps)
        Xw = Xa.T * weights
        try:
            theta_new = np.linalg.solve(Xw @ Xa + reg_mat, Xw @ y)
        except np.linalg.LinAlgError:
            break
        if float(np.max(np.abs(theta_new - theta))) < 1e-6:
            theta = theta_new
            break
        theta = theta_new
    return theta[:-1], float(theta[-1])


@dataclass
class _TreeNode:
    feature_idx: int = -1
    threshold: float = 0.0
    left: Optional["_TreeNode"] = None
    right: Optional["_TreeNode"] = None
    value: float = 0.0
    leaf_values: Optional[np.ndarray] = None


class _SimpleCARTRegressor:
    """Fast deterministic CART regressor storing empirical leaf value distributions."""

    def __init__(
        self,
        max_depth: int = 4,
        min_samples_leaf: int = 4,
        max_features_ratio: float = 1.0,
        seed: int = 42,
    ) -> None:
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.max_features_ratio = max_features_ratio
        self.rng = np.random.default_rng(seed)
        self.root: Optional[_TreeNode] = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_SimpleCARTRegressor":
        self.root = self._build_tree(X, y, depth=0)
        return self

    def _build_tree(self, X: np.ndarray, y: np.ndarray, depth: int) -> _TreeNode:
        n, d = X.shape
        leaf_val = float(np.mean(y)) if n > 0 else 0.0
        if depth >= self.max_depth or n < 2 * self.min_samples_leaf or float(np.var(y)) < 1e-12:
            return _TreeNode(value=leaf_val, leaf_values=y.copy())

        n_cand = max(1, int(math.ceil(d * self.max_features_ratio)))
        feat_indices = (
            self.rng.choice(d, size=n_cand, replace=False) if n_cand < d else np.arange(d)
        )

        best_gain = 1e-12
        best_feat = -1
        best_thr = 0.0
        total_var = float(np.var(y)) * n

        for f_idx in feat_indices:
            col = X[:, f_idx]
            uniq = np.unique(col)
            if uniq.size <= 1:
                continue
            # Evaluate up to 16 evenly spaced quantile thresholds for speed & anti-overfit smoothing
            thresholds = (
                (uniq[:-1] + uniq[1:]) / 2.0
                if uniq.size <= 16
                else np.quantile(col, np.linspace(0.1, 0.9, 12))
            )
            for thr in thresholds:
                left_mask = col <= thr
                n_l = int(np.sum(left_mask))
                n_r = n - n_l
                if n_l < self.min_samples_leaf or n_r < self.min_samples_leaf:
                    continue
                y_l = y[left_mask]
                y_r = y[~left_mask]
                sse = float(np.var(y_l)) * n_l + float(np.var(y_r)) * n_r
                gain = total_var - sse
                if gain > best_gain:
                    best_gain = gain
                    best_feat = int(f_idx)
                    best_thr = float(thr)

        if best_feat < 0:
            return _TreeNode(value=leaf_val, leaf_values=y.copy())

        mask = X[:, best_feat] <= best_thr
        return _TreeNode(
            feature_idx=best_feat,
            threshold=best_thr,
            left=self._build_tree(X[mask], y[mask], depth + 1),
            right=self._build_tree(X[~mask], y[~mask], depth + 1),
        )

    def _predict_row(self, node: _TreeNode, x: np.ndarray) -> Tuple[float, np.ndarray]:
        cur = node
        while cur.feature_idx >= 0 and cur.left is not None and cur.right is not None:
            cur = cur.left if x[cur.feature_idx] <= cur.threshold else cur.right
        return cur.value, (cur.leaf_values if cur.leaf_values is not None else np.array([cur.value]))

    def predict(self, X: np.ndarray) -> np.ndarray:
        assert self.root is not None
        return np.array([self._predict_row(self.root, X[i])[0] for i in range(X.shape[0])], dtype=np.float64)

    def predict_quantiles(self, X: np.ndarray, quantiles: Sequence[float]) -> np.ndarray:
        assert self.root is not None
        out = np.zeros((X.shape[0], len(quantiles)), dtype=np.float64)
        for i in range(X.shape[0]):
            _, leaf_vals = self._predict_row(self.root, X[i])
            out[i] = np.quantile(leaf_vals, quantiles)
        return out


class TabularEventPredictor:
    """
    Unified Phase 5 Predictor supporting all Baseline + Classical ML estimators
    under any strictly projected `ModelConfiguration` (Models A-E).
    """

    def __init__(
        self,
        algorithm: ModelAlgorithm,
        config: ModelConfiguration,
        seed: int = 42,
        ridge_alpha: float = 5.0,
        rf_n_estimators: int = 25,
        gbdt_n_estimators: int = 30,
        gbdt_lr: float = 0.08,
        min_samples_for_neural: int = 100,
    ) -> None:
        self.algorithm = algorithm
        self.config = config
        self.seed = seed
        self.ridge_alpha = ridge_alpha
        self.rf_n_estimators = rf_n_estimators
        self.gbdt_n_estimators = gbdt_n_estimators
        self.gbdt_lr = gbdt_lr
        self.min_samples_for_neural = min_samples_for_neural

        self.feature_names = get_feature_names_for_config(config)
        self.transformer = FoldSafeFeatureTransformer(self.feature_names)
        self.is_fitted = False

        # Shared learned state
        self._train_y_mean: float = 0.0
        self._train_y_median: float = 0.0
        self._train_y_std: float = 0.01
        self._train_quantiles: np.ndarray = np.zeros(5, dtype=np.float64)
        self._pos_prior: float = 0.5
        self._neg_prior: float = 0.5
        self._move_prior: float = 0.5

        # Group lookup baselines
        self._inst_stats: Dict[str, Tuple[float, float, int]] = {}
        self._subtype_stats: Dict[str, Tuple[float, float, float, int]] = {}

        # Linear / classifier weights
        self._w_reg: Optional[np.ndarray] = None
        self._b_reg: float = 0.0
        self._pos_clf: Optional[Tuple[np.ndarray, float]] = None
        self._neg_clf: Optional[Tuple[np.ndarray, float]] = None
        self._move_clf: Optional[Tuple[np.ndarray, float]] = None
        self._quantile_models: Dict[float, Tuple[np.ndarray, float]] = {}

        # Tree ensembles & GBDT quantile heads
        self._trees: List[_SimpleCARTRegressor] = []
        self._gbdt_trees: List[_SimpleCARTRegressor] = []
        self._gbdt_base: float = 0.0
        self._gbdt_q_heads: Dict[float, Tuple[float, List[_SimpleCARTRegressor]]] = {}

        # Compact MLP weights
        self._mlp_weights: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, float]] = None

    def fit(
        self,
        feature_dicts: Sequence[Dict[str, Optional[float]]],
        y_continuous: Sequence[float],
        y_positive: Sequence[int],
        y_negative: Sequence[int],
        instrument_ids: Optional[Sequence[str]] = None,
        subtypes: Optional[Sequence[str]] = None,
    ) -> "TabularEventPredictor":
        n = len(feature_dicts)
        if n == 0 or len(y_continuous) != n:
            raise ValueError("Empty or misaligned training set supplied to TabularEventPredictor.fit()")

        y = np.asarray(y_continuous, dtype=np.float64)
        yp = np.asarray(y_positive, dtype=np.float64)
        yn = np.asarray(y_negative, dtype=np.float64)
        ym = np.clip(yp + yn, 0.0, 1.0)

        self._train_y_mean = float(np.mean(y))
        self._train_y_median = float(np.median(y))
        self._train_y_std = max(1e-5, float(np.std(y)))
        self._train_quantiles = np.quantile(y, [0.10, 0.25, 0.50, 0.75, 0.90])
        self._pos_prior = float(np.clip(np.mean(yp), 0.01, 0.99))
        self._neg_prior = float(np.clip(np.mean(yn), 0.01, 0.99))
        self._move_prior = float(np.clip(np.mean(ym), 0.01, 0.99))

        X = self.transformer.fit_transform(feature_dicts)

        if self.algorithm == ModelAlgorithm.ZERO_RETURN:
            self.is_fitted = True
            return self

        if self.algorithm == ModelAlgorithm.UNCONDITIONAL_MEAN:
            self.is_fitted = True
            return self

        if self.algorithm == ModelAlgorithm.ROLLING_INSTRUMENT:
            by_inst: Dict[str, List[float]] = {}
            for idx, inst in enumerate(instrument_ids or ["GLOBAL"] * n):
                by_inst.setdefault(inst, []).append(float(y[idx]))
            for inst, vals in by_inst.items():
                arr = np.asarray(vals, dtype=np.float64)
                self._inst_stats[inst] = (float(np.mean(arr)), max(1e-5, float(np.std(arr))), int(arr.size))
            self.is_fitted = True
            return self

        if self.algorithm == ModelAlgorithm.EVENT_SUBTYPE_SHRINKAGE:
            grouped: Dict[str, List[Tuple[float, float, float]]] = {}
            for idx, st in enumerate(subtypes or ["UNKNOWN"] * n):
                grouped.setdefault(st, []).append((float(y[idx]), float(yp[idx]), float(yn[idx])))
            shrink_k = 5.0
            for st, triples in grouped.items():
                cnt = len(triples)
                m_y = sum(t[0] for t in triples) / cnt
                m_p = sum(t[1] for t in triples) / cnt
                m_n = sum(t[2] for t in triples) / cnt
                # Shrink rare subtypes toward training-fold grand mean
                shr_y = (cnt * m_y + shrink_k * self._train_y_mean) / (cnt + shrink_k)
                shr_p = (cnt * m_p + shrink_k * self._pos_prior) / (cnt + shrink_k)
                shr_n = (cnt * m_n + shrink_k * self._neg_prior) / (cnt + shrink_k)
                self._subtype_stats[st] = (shr_y, shr_p, shr_n, cnt)
            self.is_fitted = True
            return self

        # Fit shared L2 logistic heads for P(pos), P(neg), P(material move)
        self._pos_clf = _fit_logistic_l2(X, yp, l2_reg=self.ridge_alpha)
        self._neg_clf = _fit_logistic_l2(X, yn, l2_reg=self.ridge_alpha)
        self._move_clf = _fit_logistic_l2(X, ym, l2_reg=self.ridge_alpha)

        if self.algorithm == ModelAlgorithm.RIDGE:
            self._w_reg, self._b_reg = _fit_ridge(X, y, alpha=self.ridge_alpha)
            res = y - (X @ self._w_reg + self._b_reg)
            self._train_y_std = max(1e-5, float(np.std(res)))
        elif self.algorithm == ModelAlgorithm.ELASTIC_NET:
            self._w_reg, self._b_reg = _fit_elastic_net(X, y, l1_alpha=1e-4, l2_alpha=self.ridge_alpha / max(1, n))
            res = y - (X @ self._w_reg + self._b_reg)
            self._train_y_std = max(1e-5, float(np.std(res)))
        elif self.algorithm == ModelAlgorithm.LOGISTIC_DIRECTION:
            # Expected return mapped from calibrated logistic directional bias & average train return scale
            self._w_reg, self._b_reg = _fit_ridge(X, y, alpha=self.ridge_alpha * 2.0)
        elif self.algorithm == ModelAlgorithm.QUANTILE_LINEAR:
            for tau in (0.10, 0.25, 0.50, 0.75, 0.90):
                self._quantile_models[tau] = _fit_quantile_irls(X, y, tau=tau, l2_reg=self.ridge_alpha)
            self._w_reg, self._b_reg = _fit_ridge(X, y, alpha=self.ridge_alpha)
        elif self.algorithm == ModelAlgorithm.RANDOM_FOREST:
            rng = np.random.default_rng(self.seed)
            self._trees = []
            for t_idx in range(self.rf_n_estimators):
                boot_idx = rng.integers(0, n, size=n)
                tree = _SimpleCARTRegressor(
                    max_depth=4,
                    min_samples_leaf=max(2, n // 20),
                    max_features_ratio=0.6,
                    seed=self.seed + t_idx,
                ).fit(X[boot_idx], y[boot_idx])
                self._trees.append(tree)
        elif self.algorithm == ModelAlgorithm.GBDT:
            self._gbdt_base = self._train_y_mean
            cur_pred = np.full(n, self._gbdt_base, dtype=np.float64)
            self._gbdt_trees = []
            for t_idx in range(self.gbdt_n_estimators):
                grad = y - cur_pred
                tree = _SimpleCARTRegressor(
                    max_depth=3,
                    min_samples_leaf=max(2, n // 15),
                    max_features_ratio=0.8,
                    seed=self.seed + t_idx,
                ).fit(X, grad)
                self._gbdt_trees.append(tree)
                cur_pred += self.gbdt_lr * tree.predict(X)
            res = y - cur_pred
            self._train_y_std = max(1e-5, float(np.std(res)))
            # Quantile gradient boosted heads for q10, q50, q90 via pinball pseudo-residuals
            self._gbdt_q_heads = {}
            for tau in (0.10, 0.50, 0.90):
                q_base = float(np.quantile(y, tau))
                q_pred = np.full(n, q_base, dtype=np.float64)
                q_trees: List[_SimpleCARTRegressor] = []
                for t_idx in range(min(15, self.gbdt_n_estimators)):
                    err = y - q_pred
                    pseudo_grad = np.where(err >= 0.0, tau, tau - 1.0) * np.minimum(np.abs(err), self._train_y_std * 2.0 + 1e-4)
                    qt = _SimpleCARTRegressor(max_depth=2, min_samples_leaf=max(2, n // 12), seed=self.seed + 500 + t_idx).fit(X, pseudo_grad)
                    q_trees.append(qt)
                    q_pred += 0.15 * qt.predict(X)
                self._gbdt_q_heads[tau] = (q_base, q_trees)
        elif self.algorithm == ModelAlgorithm.COMPACT_MLP:
            if n < self.min_samples_for_neural:
                raise ValueError(
                    f"Neural MLP gated: sample size {n} < minimum justified threshold {self.min_samples_for_neural}."
                )
            rng = np.random.default_rng(self.seed)
            h_dim = min(16, max(4, X.shape[1] // 2))
            W1 = rng.normal(0.0, 0.1, size=(X.shape[1], h_dim))
            b1 = np.zeros(h_dim, dtype=np.float64)
            W2 = rng.normal(0.0, 0.05, size=h_dim)
            b2 = self._train_y_mean
            lr = 0.01
            l2 = 1e-3
            for _ in range(120):
                z1 = X @ W1 + b1
                a1 = np.maximum(0.0, z1)
                pred = a1 @ W2 + b2
                err = pred - y
                d_pred = (2.0 / n) * err
                d_W2 = a1.T @ d_pred + l2 * W2
                d_b2 = float(np.sum(d_pred))
                d_a1 = np.outer(d_pred, W2) * (z1 > 0.0)
                d_W1 = X.T @ d_a1 + l2 * W1
                d_b1 = np.sum(d_a1, axis=0)
                W2 -= lr * d_W2
                b2 -= lr * d_b2
                W1 -= lr * d_W1
                b1 -= lr * d_b1
            self._mlp_weights = (W1, b1, W2, float(b2))

        self.is_fitted = True
        return self

    def predict_raw(
        self,
        feature_dicts: Sequence[Dict[str, Optional[float]]],
        instrument_ids: Optional[Sequence[str]] = None,
        subtypes: Optional[Sequence[str]] = None,
    ) -> List[RawModelPrediction]:
        if not self.is_fitted:
            raise RuntimeError("TabularEventPredictor must be fitted before predict_raw()")
        n = len(feature_dicts)
        if n == 0:
            return []

        X = self.transformer.transform(feature_dicts)
        results: List[RawModelPrediction] = []

        if self.algorithm == ModelAlgorithm.ZERO_RETURN:
            q = self._train_quantiles - self._train_y_median
            for _ in range(n):
                results.append(
                    RawModelPrediction(
                        expected_return=0.0,
                        median_return=0.0,
                        p_positive=0.5,
                        p_negative=0.5,
                        p_material_move=self._move_prior,
                        q10=float(q[0]), q25=float(q[1]), q50=0.0, q75=float(q[3]), q90=float(q[4]),
                        predictive_std=self._train_y_std,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.UNCONDITIONAL_MEAN:
            q = self._train_quantiles
            for _ in range(n):
                results.append(
                    RawModelPrediction(
                        expected_return=self._train_y_mean,
                        median_return=self._train_y_median,
                        p_positive=self._pos_prior,
                        p_negative=self._neg_prior,
                        p_material_move=self._move_prior,
                        q10=float(q[0]), q25=float(q[1]), q50=float(q[2]), q75=float(q[3]), q90=float(q[4]),
                        predictive_std=self._train_y_std,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.ROLLING_INSTRUMENT:
            for i in range(n):
                inst = (instrument_ids[i] if instrument_ids else "GLOBAL")
                m_i, s_i, cnt = self._inst_stats.get(inst, (self._train_y_mean, self._train_y_std, 0))
                shrink = cnt / (cnt + 5.0)
                mu = shrink * m_i + (1.0 - shrink) * self._train_y_mean
                sd = max(1e-5, shrink * s_i + (1.0 - shrink) * self._train_y_std)
                shift = self._train_quantiles - self._train_y_median + mu
                results.append(
                    RawModelPrediction(
                        expected_return=mu,
                        median_return=mu,
                        p_positive=self._pos_prior,
                        p_negative=self._neg_prior,
                        p_material_move=self._move_prior,
                        q10=float(shift[0]), q25=float(shift[1]), q50=float(shift[2]), q75=float(shift[3]), q90=float(shift[4]),
                        predictive_std=sd,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.EVENT_SUBTYPE_SHRINKAGE:
            for i in range(n):
                st = (subtypes[i] if subtypes else "UNKNOWN")
                mu, pp, pn, _ = self._subtype_stats.get(st, (self._train_y_mean, self._pos_prior, self._neg_prior, 0))
                shift = self._train_quantiles - self._train_y_median + mu
                results.append(
                    RawModelPrediction(
                        expected_return=mu,
                        median_return=mu,
                        p_positive=float(np.clip(pp, 1e-4, 1.0 - 1e-4)),
                        p_negative=float(np.clip(pn, 1e-4, 1.0 - 1e-4)),
                        p_material_move=float(np.clip(pp + pn, 1e-4, 1.0 - 1e-4)),
                        q10=float(shift[0]), q25=float(shift[1]), q50=float(shift[2]), q75=float(shift[3]), q90=float(shift[4]),
                        predictive_std=self._train_y_std,
                    )
                )
            return results

        p_pos = _predict_logistic(self._pos_clf[0] if self._pos_clf else None, self._pos_prior, X)
        p_neg = _predict_logistic(self._neg_clf[0] if self._neg_clf else None, self._neg_prior, X)
        p_move = _predict_logistic(self._move_clf[0] if self._move_clf else None, self._move_prior, X)

        if self.algorithm in (ModelAlgorithm.RIDGE, ModelAlgorithm.ELASTIC_NET):
            assert self._w_reg is not None
            exp_ret = X @ self._w_reg + self._b_reg
            for i in range(n):
                mu = float(exp_ret[i])
                sd = self._train_y_std
                results.append(
                    RawModelPrediction(
                        expected_return=mu,
                        median_return=mu,
                        p_positive=float(p_pos[i]),
                        p_negative=float(p_neg[i]),
                        p_material_move=float(p_move[i]),
                        q10=mu - 1.2816 * sd,
                        q25=mu - 0.6745 * sd,
                        q50=mu,
                        q75=mu + 0.6745 * sd,
                        q90=mu + 1.2816 * sd,
                        predictive_std=sd,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.LOGISTIC_DIRECTION:
            assert self._w_reg is not None
            lin_ret = X @ self._w_reg + self._b_reg
            dir_signal = (p_pos - p_neg) * self._train_y_std
            exp_ret = 0.5 * lin_ret + 0.5 * dir_signal
            for i in range(n):
                mu = float(exp_ret[i])
                sd = self._train_y_std
                results.append(
                    RawModelPrediction(
                        expected_return=mu,
                        median_return=mu,
                        p_positive=float(p_pos[i]),
                        p_negative=float(p_neg[i]),
                        p_material_move=float(p_move[i]),
                        q10=mu - 1.2816 * sd, q25=mu - 0.6745 * sd, q50=mu, q75=mu + 0.6745 * sd, q90=mu + 1.2816 * sd,
                        predictive_std=sd,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.QUANTILE_LINEAR:
            assert self._w_reg is not None
            exp_ret = X @ self._w_reg + self._b_reg
            qs_mat = np.column_stack([
                X @ self._quantile_models[tau][0] + self._quantile_models[tau][1]
                for tau in (0.10, 0.25, 0.50, 0.75, 0.90)
            ])
            # Sort predictions across quantile levels to guarantee monotonicity
            qs_mat = np.sort(qs_mat, axis=1)
            for i in range(n):
                sd = max(1e-5, float((qs_mat[i, 4] - qs_mat[i, 0]) / 2.563))
                results.append(
                    RawModelPrediction(
                        expected_return=float(exp_ret[i]),
                        median_return=float(qs_mat[i, 2]),
                        p_positive=float(p_pos[i]),
                        p_negative=float(p_neg[i]),
                        p_material_move=float(p_move[i]),
                        q10=float(qs_mat[i, 0]), q25=float(qs_mat[i, 1]), q50=float(qs_mat[i, 2]),
                        q75=float(qs_mat[i, 3]), q90=float(qs_mat[i, 4]),
                        predictive_std=sd,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.RANDOM_FOREST:
            tree_preds = np.column_stack([t.predict(X) for t in self._trees])
            leaf_qs = np.mean(
                np.stack([t.predict_quantiles(X, [0.10, 0.25, 0.50, 0.75, 0.90]) for t in self._trees], axis=0),
                axis=0,
            )
            leaf_qs = np.sort(leaf_qs, axis=1)
            for i in range(n):
                mu = float(np.mean(tree_preds[i]))
                ens_sd = max(1e-5, float(np.std(tree_preds[i])) + 0.5 * self._train_y_std)
                results.append(
                    RawModelPrediction(
                        expected_return=mu,
                        median_return=float(leaf_qs[i, 2]),
                        p_positive=float(p_pos[i]),
                        p_negative=float(p_neg[i]),
                        p_material_move=float(p_move[i]),
                        q10=float(leaf_qs[i, 0]), q25=float(leaf_qs[i, 1]), q50=float(leaf_qs[i, 2]),
                        q75=float(leaf_qs[i, 3]), q90=float(leaf_qs[i, 4]),
                        predictive_std=ens_sd,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.GBDT:
            exp_ret = np.full(n, self._gbdt_base, dtype=np.float64)
            for t in self._gbdt_trees:
                exp_ret += self.gbdt_lr * t.predict(X)
            q_vals: Dict[float, np.ndarray] = {}
            for tau, (qb, qtrees) in self._gbdt_q_heads.items():
                qp = np.full(n, qb, dtype=np.float64)
                for qt in qtrees:
                    qp += 0.15 * qt.predict(X)
                q_vals[tau] = qp
            q_mat = np.sort(np.column_stack([q_vals[0.10], q_vals[0.50], q_vals[0.90]]), axis=1)
            for i in range(n):
                q10, q50, q90 = float(q_mat[i, 0]), float(q_mat[i, 1]), float(q_mat[i, 2])
                q25 = 0.5 * (q10 + q50)
                q75 = 0.5 * (q50 + q90)
                sd = max(1e-5, (q90 - q10) / 2.563)
                results.append(
                    RawModelPrediction(
                        expected_return=float(exp_ret[i]),
                        median_return=q50,
                        p_positive=float(p_pos[i]),
                        p_negative=float(p_neg[i]),
                        p_material_move=float(p_move[i]),
                        q10=q10, q25=q25, q50=q50, q75=q75, q90=q90,
                        predictive_std=sd,
                    )
                )
            return results

        if self.algorithm == ModelAlgorithm.COMPACT_MLP and self._mlp_weights is not None:
            W1, b1, W2, b2 = self._mlp_weights
            exp_ret = np.maximum(0.0, X @ W1 + b1) @ W2 + b2
            sd = self._train_y_std
            for i in range(n):
                mu = float(exp_ret[i])
                results.append(
                    RawModelPrediction(
                        expected_return=mu, median_return=mu,
                        p_positive=float(p_pos[i]), p_negative=float(p_neg[i]), p_material_move=float(p_move[i]),
                        q10=mu - 1.2816 * sd, q25=mu - 0.6745 * sd, q50=mu, q75=mu + 0.6745 * sd, q90=mu + 1.2816 * sd,
                        predictive_std=sd,
                    )
                )
            return results

        raise RuntimeError(f"Unsupported model algorithm state: {self.algorithm}")
