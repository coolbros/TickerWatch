# Phase 3 Causal Historical Event Retrieval and Contextual Feature Engine

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 3 — Financial NLP, Entity Resolution & Numerical Surprise Detection  
**Status:** Canonical Implementation Specification  

---

## 1. Executive Summary

A core premise of TickerWatch Alpha is that financial events cannot be evaluated in isolation. How the market responds to a ₹2,500 crore order win by Larsen & Toubro is conditioned on how the stock reacted to similar order wins in the past, how frequently such wins occur, and whether order wins in that sector have recently catalyzed sustained price appreciation.

This module (`backend/alpha/event_retrieval.py`) provides causal historical event retrieval and contextual feature synthesis for every evaluated news event.

---

## 2. Strict Causal Temporal Invariance

The most critical hazard in historical retrieval is **information leakage from the future**. If an event occurring at timestamp $\tau_{\text{decision}}$ could retrieve events from $\tau_{\text{decision}} + 1\text{ hour}$, the model would be conditioned on post-hoc future outcomes.

### The Causal Retrieval Axiom
$$\forall e_h \in \text{Candidate Matches}, \quad \tau(e_h) < \tau_{\text{decision}}$$

Before any semantic similarity or lexical scoring is performed, candidate records are pre-filtered with an immutable timestamp condition. Future events are strictly excluded from the index search space.

---

## 3. Hybrid Similarity Function

To identify truly comparable financial catalysts, the retrieval engine evaluates a hybrid metric balancing lexical terminology, categorical event taxonomy, instrument identity, and economic materiality:

$$\text{Similarity}(e_q, e_h) = 0.35 \cdot J(T_q, T_h) + 0.30 \cdot \mathbf{1}_{\text{subtype}}(e_q, e_h) + 0.20 \cdot \mathbf{1}_{\text{instrument}}(e_q, e_h) + 0.15 \cdot (1 - |\Delta M|)$$

Where:
- $J(T_q, T_h) = \frac{|T_q \cap T_h|}{|T_q \cup T_h|}$: Token Jaccard overlap of non-stopword financial terms.
- $\mathbf{1}_{\text{subtype}}$: Binary indicator ($1.0$ if both events share the exact `EventSubtype`, else $0.0$).
- $\mathbf{1}_{\text{instrument}}$: Binary indicator ($1.0$ if same company ticker/ID, else $0.0$).
- $1 - |\Delta M|$: Materiality score proximity, where $\Delta M = M(e_q) - M(e_h)$.

---

## 4. Synthesized Contextual Features

For each query event, the engine synthesizes an `EventRetrievalContext` containing:
1. `matches`: Ranked list of top-$k$ comparable historical events with similarity scores and historical timestamps.
2. `total_comparable_count`: Number of past comparable events recorded in the repository.
3. `mean_historical_return_1d`: Average subsequent 1-day percentage return across top comparable events (when historical market data is aligned).
4. `recency_days_latest`: Number of days elapsed since the most recent comparable event occurred.

These contextual features feed directly into the unified Phase 3 representation (`FinancialEventRepresentation`) for future conditioning.
