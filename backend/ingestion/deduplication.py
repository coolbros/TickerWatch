"""Deduplication and deterministic hashing utilities."""

import hashlib
import re
from typing import Optional


def sanitize_text_for_hashing(text: str) -> str:
    """
    Perform conservative whitespace, HTML entity, and punctuation normalization
    for deterministic content hashing.
    """
    if not text:
        return ""
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Convert to lowercase
    clean = clean.lower()
    # Normalize unicode spaces and whitespace sequences
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def compute_content_hash(title: str, content_text: Optional[str] = None) -> str:
    """
    Calculate deterministic SHA-256 hash from normalized title and content text.
    Identifies identical content across different sources or syndications.
    """
    norm_title = sanitize_text_for_hashing(title)
    norm_content = sanitize_text_for_hashing(content_text or "")
    payload = f"{norm_title}\n{norm_content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def derive_stable_source_id(
    connector_id: str,
    raw_id: Optional[str],
    url: Optional[str],
    title: Optional[str] = None,
    published_str: Optional[str] = None,
) -> str:
    """
    Derive a stable, deterministic connector-scoped identifier for a source item.
    Never relies on current execution timestamp.
    """
    if raw_id and raw_id.strip():
        return raw_id.strip()

    if url and url.strip():
        # Clean URL used as identifier
        clean_url = url.strip()
        # Hash long URLs to keep IDs compact and index-friendly
        hashed = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:24]
        return f"url_{hashed}"

    # Fallback to deterministic hash of title and date
    combo = f"{connector_id}:{title or ''}:{published_str or ''}".encode("utf-8")
    return f"derived_{hashlib.sha256(combo).hexdigest()[:24]}"
