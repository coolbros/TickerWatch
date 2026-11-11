"""Dense Semantic Embedding Engine for Financial Events.

Provides compact, CPU-friendly pretrained transformer inference (`BAAI/bge-small-en-v1.5`
INT8 quantized ONNX via `Xenova/bge-small-en-v1.5`, 384-dim, MIT License) alongside a
zero-weight financial concept projection baseline (`fin-concept-proj-256d-v1`).

Phase 5 (Work Package A3) Preprocessing Parity Specification:
- Pinned HuggingFace snapshot revision: `ea104dacec62c0de699686887e3f920caeb4f3e3`.
- Official BAAI/FlagEmbedding pooling: `[CLS]` token (index 0) + L2 unit normalization.
- Tokenizer parity: `[CLS]=101`, `[SEP]=102`, `[PAD]=0`, `max_length=512`, real `token_type_ids`.
- Asymmetric retrieval protocol: document vectors encoded unprefixed; retrieval queries
  prefixed with `"Represent this sentence for searching relevant passages: "`.
- Version isolation: bumped to `bge-small-en-v1.5-onnx-int8-cls512-v2` so earlier `v1` vectors
  are never silently mixed in cosine retrieval.
"""

from enum import Enum
import hashlib
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np
from pydantic import BaseModel, Field


class EmbeddingModelVersion(str, Enum):
    """Canonical model versions ensuring strict vector space compatibility."""

    BGE_SMALL_EN_V1_5_INT8 = "bge-small-en-v1.5-onnx-int8"  # Legacy Phase 4 v1 (max_length=192, no query prefix)
    BGE_SMALL_EN_V1_5_INT8_V2 = "bge-small-en-v1.5-onnx-int8-cls512-v2"  # Pinned Phase 5 v2 (CLS, 512, BGE query instruction)
    FIN_CONCEPT_PROJECTION_V1 = "fin-concept-proj-256d-v1"


class VersionedEmbedding(BaseModel):
    """A dense semantic vector tied to a specific immutable model version."""

    event_id: str
    model_version: str
    dimension: int
    vector: List[float]
    text_digest: str
    encoding_latency_ms: float = 0.0


def format_event_for_embedding(
    headline: str,
    body_snippet: Optional[str] = None,
    event_subtype: Optional[str] = None,
    primary_entity: Optional[str] = None,
) -> str:
    """
    Construct canonical semantic input text combining structured attributes
    with natural language headline/snippet for high-signal vector representation.
    """
    parts: List[str] = []
    if event_subtype:
        clean_sub = event_subtype.replace("_", " ").title()
        parts.append(f"[{clean_sub}]")
    if primary_entity:
        parts.append(f"[{primary_entity}]")
    parts.append(headline.strip())
    if body_snippet:
        snippet = body_snippet.strip()[:350]
        if snippet and snippet.lower() not in headline.lower():
            parts.append(snippet)
    return " ".join(parts)


def exact_cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute exact cosine similarity between two dense vectors."""
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    if a.shape != b.shape or a.size == 0:
        raise ValueError(f"Vector dimension mismatch or empty: {a.shape} vs {b.shape}")
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    sim = float(np.dot(a, b) / (na * nb))
    return max(-1.0, min(1.0, sim))


def batch_cosine_similarity(query_vec: Sequence[float], matrix: np.ndarray) -> np.ndarray:
    """
    Exact vectorized cosine similarity between normalized query vector [D]
    and candidate matrix [N, D]. Returns similarity scores [N].
    """
    if matrix.size == 0:
        return np.array([], dtype=np.float32)
    q = np.asarray(query_vec, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm < 1e-12:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    q = q / q_norm

    m_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe_norms = np.clip(m_norms, 1e-12, None)
    m_unit = matrix / safe_norms
    sims = m_unit @ q
    return np.clip(sims, -1.0, 1.0)


class BaseEventEncoder:
    """Abstract base interface for dense event encoders."""

    model_version: str
    dimension: int
    license_name: str

    def is_available(self) -> bool:
        return True

    def encode_batch(self, texts: List[str]) -> np.ndarray:
        raise NotImplementedError

    def encode_documents(self, texts: List[str]) -> np.ndarray:
        """Encode candidate corpus documents for vector indexing."""
        return self.encode_batch(texts)

    def encode_queries(self, texts: List[str]) -> np.ndarray:
        """Encode search queries (may apply model-specific retrieval instructions)."""
        return self.encode_batch(texts)

    def encode_single(self, text: str) -> List[float]:
        mat = self.encode_batch([text])
        return mat[0].tolist()

    def encode_query_single(self, text: str) -> List[float]:
        mat = self.encode_queries([text])
        return mat[0].tolist()


class ONNXBgeSmallEncoder(BaseEventEncoder):
    """
    CPU-optimized INT8 quantized ONNX inference for `BAAI/bge-small-en-v1.5`.
    Model: Xenova/bge-small-en-v1.5 (33M params, 384-dim, ~34MB INT8 ONNX, MIT License).
    Executes strictly offline using cached HuggingFace snapshot weights at pinned revision.
    """

    REPO_ID = "Xenova/bge-small-en-v1.5"
    PINNED_REVISION = "ea104dacec62c0de699686887e3f920caeb4f3e3"
    BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
    model_version = EmbeddingModelVersion.BGE_SMALL_EN_V1_5_INT8_V2.value
    dimension = 384
    license_name = "MIT"

    def __init__(
        self,
        max_length: int = 512,
        intra_op_threads: int = 2,
        pooling_mode: str = "cls",
        use_query_instruction: bool = True,
        revision: Optional[str] = PINNED_REVISION,
    ) -> None:
        if pooling_mode not in ("cls", "mean"):
            raise ValueError(f"Unsupported pooling_mode '{pooling_mode}'. Expected 'cls' or 'mean'.")
        self.max_length = max_length
        self.intra_op_threads = intra_op_threads
        self.pooling_mode = pooling_mode
        self.use_query_instruction = use_query_instruction
        self.revision = revision
        self._session: Any = None
        self._tokenizer: Any = None
        self._load_error: Optional[str] = None
        self._tried_load = False

    def _ensure_loaded(self) -> bool:
        if self._session is not None and self._tokenizer is not None:
            return True
        if self._tried_load:
            return False

        self._tried_load = True
        try:
            os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
            from huggingface_hub import hf_hub_download
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tok_path = hf_hub_download(
                self.REPO_ID,
                "tokenizer.json",
                revision=self.revision,
                local_files_only=True,
            )
            mdl_path = hf_hub_download(
                self.REPO_ID,
                "onnx/model_quantized.onnx",
                revision=self.revision,
                local_files_only=True,
            )

            tok = Tokenizer.from_file(tok_path)
            tok.enable_truncation(max_length=self.max_length)
            tok.enable_padding(pad_id=0, pad_token="[PAD]", length=None)

            sess_opts = ort.SessionOptions()
            sess_opts.intra_op_num_threads = self.intra_op_threads
            sess_opts.inter_op_num_threads = 1
            sess = ort.InferenceSession(mdl_path, sess_options=sess_opts, providers=["CPUExecutionProvider"])

            self._tokenizer = tok
            self._session = sess
            return True
        except Exception as e:
            self._load_error = str(e)
            return False

    def is_available(self) -> bool:
        return self._ensure_loaded()

    def _encode_internal(self, texts: List[str]) -> np.ndarray:
        if not self._ensure_loaded():
            raise RuntimeError(f"ONNX model {self.REPO_ID} unavailable offline: {self._load_error}")
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        encs = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encs], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encs], dtype=np.int64)

        outs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )
        last_hidden = outs[0]
        if self.pooling_mode == "cls":
            # BAAI/bge-small-en-v1.5 reference architecture pools [CLS] token at index 0
            pooled = last_hidden[:, 0, :].astype(np.float32)
        else:
            mask = attention_mask[:, :, None].astype(np.float32)
            pooled = ((last_hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-12, None)).astype(np.float32)

        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / np.clip(norms, 1e-12, None)

    def encode_batch(self, texts: List[str]) -> np.ndarray:
        return self._encode_internal(texts)

    def encode_documents(self, texts: List[str]) -> np.ndarray:
        """Encode documents cleanly without query instruction prefix."""
        return self._encode_internal(texts)

    def encode_queries(self, texts: List[str]) -> np.ndarray:
        """Encode search queries using official BGE v1.5 retrieval instruction prefix."""
        if self.use_query_instruction:
            prefixed = [
                t if t.startswith(self.BGE_QUERY_INSTRUCTION) else f"{self.BGE_QUERY_INSTRUCTION}{t}"
                for t in texts
            ]
            return self._encode_internal(prefixed)
        return self._encode_internal(texts)

    def inspect_tokenization(self, text: str) -> Dict[str, Any]:
        """Inspect exact tokenizer special tokens, truncation, and type_ids for parity testing."""
        if not self._ensure_loaded():
            raise RuntimeError(f"ONNX model {self.REPO_ID} unavailable offline: {self._load_error}")
        enc = self._tokenizer.encode(text)
        return {
            "ids": list(enc.ids),
            "tokens": list(enc.tokens),
            "attention_mask": list(enc.attention_mask),
            "type_ids": list(enc.type_ids),
            "max_length": self.max_length,
            "pooling_mode": self.pooling_mode,
            "revision": self.revision,
            "model_version": self.model_version,
        }


def verify_bge_preprocessing_parity() -> Dict[str, Any]:
    """
    Run reproducible offline verification confirming tokenizer special tokens,
    max_length=512 truncation, CLS pooling vs mean pooling sensitivity,
    L2 normalization, and query instruction behavior.
    """
    enc_cls = ONNXBgeSmallEncoder(max_length=512, pooling_mode="cls", use_query_instruction=True)
    if not enc_cls.is_available():
        return {"onnx_available": False}

    enc_mean = ONNXBgeSmallEncoder(max_length=512, pooling_mode="mean", use_query_instruction=True)
    tok_info = enc_cls.inspect_tokenization("TCS reports strong Q3 earnings")

    # Verify padding sensitivity & normalization
    sample_doc = "Infosys cuts full year revenue guidance amid banking weakness"
    sample_q = "IT major trims annual top-line outlook"
    d_cls = enc_cls.encode_documents([sample_doc])[0]
    q_cls = enc_cls.encode_queries([sample_q])[0]
    q_cls_noprefix = enc_cls.encode_documents([sample_q])[0]
    d_mean = enc_mean.encode_documents([sample_doc])[0]
    q_mean = enc_mean.encode_queries([sample_q])[0]

    # Truncation test over > 512 tokens
    long_text = " ".join(["revenue"] * 600)
    long_tok = enc_cls.inspect_tokenization(long_text)

    # Single vs padded batch cosine similarity (quantization invariance)
    v_single = enc_cls.encode_documents([sample_doc])[0]
    v_padded = enc_cls.encode_documents([sample_doc, "word " * 120])[0]

    return {
        "onnx_available": True,
        "model_version": enc_cls.model_version,
        "pinned_revision": enc_cls.PINNED_REVISION,
        "cls_token_id": tok_info["ids"][0],
        "sep_token_id": tok_info["ids"][-1],
        "max_length": enc_cls.max_length,
        "long_text_truncated_len": len(long_tok["ids"]),
        "doc_norm_cls": round(float(np.linalg.norm(d_cls)), 6),
        "cls_with_instruction_sim": round(float(np.dot(q_cls, d_cls)), 6),
        "cls_no_instruction_sim": round(float(np.dot(q_cls_noprefix, d_cls)), 6),
        "mean_with_instruction_sim": round(float(np.dot(q_mean, d_mean)), 6),
        "cls_vs_mean_doc_cosine": round(float(np.dot(d_cls, d_mean)), 6),
        "single_vs_padded_batch_cosine": round(float(np.dot(v_single, v_padded)), 6),
    }


class FinConceptProjectionEncoder(BaseEventEncoder):
    """
    Zero-dependency deterministic dense financial semantic encoder (256-dim).
    Maps financial synonym clusters (order/contract, profit/PAT, rate/MPC, etc.)
    and subword trigrams via orthogonal Johnson-Lindenstrauss random projections.
    Serves as a 100% air-gapped fallback and lightweight baseline for ablation studies.
    """

    model_version = EmbeddingModelVersion.FIN_CONCEPT_PROJECTION_V1.value
    dimension = 256
    license_name = "MIT (Internal Deterministic)"

    CONCEPT_GROUPS: Dict[str, List[str]] = {
        "CONTRACT_WIN": ["order", "contract", "deal", "bags", "wins", "award", "awarded", "secures", "secured", "tender", "epc", "project"],
        "EARNINGS": ["earnings", "profit", "pat", "ebitda", "revenue", "sales", "turnover", "results", "quarter", "q1", "q2", "q3", "q4"],
        "GROWTH_BEAT": ["beat", "surge", "jump", "rose", "rises", "climb", "strong", "growth", "higher", "expansion", "outperform"],
        "DECLINE_MISS": ["miss", "drop", "falls", "slump", "decline", "loss", "deficit", "lower", "weak", "contraction"],
        "MONETARY_POLICY": ["rbi", "fed", "fomc", "mpc", "repo", "rate", "basis", "bps", "inflation", "cpi", "monetary", "hawkish", "dovish"],
        "CAPITAL_ALLOCATION": ["dividend", "buyback", "repurchase", "capex", "expansion", "investment", "facility", "plant"],
        "CORPORATE_MNA": ["merger", "acquisition", "acquire", "stake", "buyout", "joint", "venture", "subsidiary", "demerger"],
        "REGULATORY_LEGAL": ["sebi", "sec", "investigation", "probe", "penalty", "fine", "lawsuit", "litigation", "compliance", "insolvency", "bankruptcy"],
        "GUIDANCE": ["guidance", "outlook", "forecast", "raises", "cuts", "trims", "fy25", "fy26", "fy27"],
    }

    def __init__(self) -> None:
        rng = np.random.default_rng(seed=4202026)
        self._concept_vectors: Dict[str, np.ndarray] = {}
        for cname in self.CONCEPT_GROUPS:
            v = rng.standard_normal(self.dimension).astype(np.float32)
            v /= np.linalg.norm(v)
            self._concept_vectors[cname] = v

    def _hash_token_vector(self, token: str) -> np.ndarray:
        seed = int(hashlib.blake2b(token.encode("utf-8"), digest_size=8).hexdigest(), 16)
        rng = np.random.default_rng(seed)
        return rng.standard_normal(self.dimension).astype(np.float32)

    def encode_batch(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        vecs = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, txt in enumerate(texts):
            tokens = re.findall(r"[a-z0-9]+", txt.lower())
            acc = np.zeros(self.dimension, dtype=np.float32)
            token_set = set(tokens)

            for cname, terms in self.CONCEPT_GROUPS.items():
                overlap = sum(1 for t in terms if t in token_set)
                if overlap > 0:
                    acc += (1.5 * math.sqrt(overlap)) * self._concept_vectors[cname]

            for t in tokens:
                acc += 0.35 * self._hash_token_vector(t)
                if len(t) >= 4:
                    for k in range(len(t) - 2):
                        trigram = t[k : k + 3]
                        acc += 0.10 * self._hash_token_vector(trigram)

            norm = float(np.linalg.norm(acc))
            if norm > 1e-12:
                vecs[i] = acc / norm
        return vecs


class EventEmbeddingEngine:
    """
    Unified, version-aware dense semantic embedding facade.
    Automatically selects `BGE_SMALL_EN_V1_5_INT8_V2` when offline quantized ONNX weights
    are cached locally, or operates using `FIN_CONCEPT_PROJECTION_V1`.
    """

    def __init__(self, prefer_onnx: bool = True) -> None:
        self.onnx_encoder = ONNXBgeSmallEncoder()
        self.fallback_encoder = FinConceptProjectionEncoder()
        self._prefer_onnx = prefer_onnx

    @property
    def active_encoder(self) -> BaseEventEncoder:
        if self._prefer_onnx and self.onnx_encoder.is_available():
            return self.onnx_encoder
        return self.fallback_encoder

    @property
    def active_model_version(self) -> str:
        return self.active_encoder.model_version

    @property
    def dimension(self) -> int:
        return self.active_encoder.dimension

    def encode_event(
        self,
        event_id: str,
        headline: str,
        body_snippet: Optional[str] = None,
        event_subtype: Optional[str] = None,
        primary_entity: Optional[str] = None,
        encoder_override: Optional[BaseEventEncoder] = None,
        as_query: bool = False,
    ) -> VersionedEmbedding:
        """Encode a financial event into a versioned dense semantic vector."""
        enc = encoder_override or self.active_encoder
        canonical_text = format_event_for_embedding(
            headline=headline,
            body_snippet=body_snippet,
            event_subtype=event_subtype,
            primary_entity=primary_entity,
        )
        t0 = time.perf_counter_ns()
        vec = enc.encode_query_single(canonical_text) if as_query else enc.encode_documents([canonical_text])[0].tolist()
        dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        digest = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()[:16]

        return VersionedEmbedding(
            event_id=event_id,
            model_version=enc.model_version,
            dimension=enc.dimension,
            vector=vec,
            text_digest=digest,
            encoding_latency_ms=round(dt_ms, 3),
        )

    def encode_texts(self, texts: List[str], encoder_override: Optional[BaseEventEncoder] = None) -> np.ndarray:
        """Batch-encode arbitrary strings as documents."""
        enc = encoder_override or self.active_encoder
        return enc.encode_documents(texts)


_embedding_engine: Optional[EventEmbeddingEngine] = None


def get_embedding_engine(prefer_onnx: bool = True) -> EventEmbeddingEngine:
    """Return shared singleton EventEmbeddingEngine."""
    global _embedding_engine
    if _embedding_engine is None:
        _embedding_engine = EventEmbeddingEngine(prefer_onnx=prefer_onnx)
    return _embedding_engine
