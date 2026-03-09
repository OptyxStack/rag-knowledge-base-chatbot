"""Evidence Hygiene - Phase 0.5: boilerplate detection, content density, signatures.

Logging only, no gating. Use data to tune Phase 1 thresholds.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk

logger = get_logger(__name__)


@lru_cache(maxsize=64)
def _compile_union(patterns: tuple[str, ...]) -> re.Pattern[str]:
    valid: list[str] = []
    for pattern in patterns:
        try:
            re.compile(pattern)
            valid.append(pattern)
        except re.error:
            logger.warning("evidence_hygiene_invalid_pattern", pattern=pattern)
    if not valid:
        return re.compile(r"$^")
    return re.compile("|".join(f"(?:{p})" for p in valid), re.I)


def _boilerplate_re() -> re.Pattern[str]:
    settings = get_settings()
    patterns = tuple(
        str(pattern).strip()
        for pattern in (settings.hygiene_boilerplate_patterns or [])
        if str(pattern).strip()
    )
    return _compile_union(patterns)


# Number + unit patterns
NUMBER_UNIT_PATTERN = re.compile(
    r"\$[\d,]+\.?\d*|"
    r"[\d,]+\.?\d*\s*(?:USD|VND|EUR|GBP|/mo|/month|/year|%|MB|GB|TB)\b|"
    r"\b\d+\s*(?:USD|VND|EUR|GBP|%|MB|GB|TB)\b",
    re.I,
)

# URL pattern
URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+|"
    r"www\.[^\s<>\"']+",
    re.I,
)


def _transaction_path_re() -> re.Pattern[str]:
    settings = get_settings()
    patterns = tuple(
        str(pattern).strip()
        for pattern in (settings.hygiene_transaction_path_patterns or [])
        if str(pattern).strip()
    )
    return _compile_union(patterns)


@dataclass
class ChunkHygiene:
    """Per-chunk hygiene metrics."""

    chunk_id: str
    boilerplate_ratio: float
    content_density: float
    has_url: bool
    has_number_unit: bool
    has_transaction_link: bool
    sentence_count: int
    non_whitespace_chars: int


@dataclass
class EvidenceSignatures:
    """Top evidence signatures (aggregate, dashboard-friendly)."""

    pct_chunks_with_url: float
    pct_chunks_with_number_unit: float
    pct_chunks_boilerplate_gt_06: float
    median_content_density: float
    chunk_count: int
    per_chunk: list[ChunkHygiene] = field(default_factory=list)


def _boilerplate_ratio(text: str) -> float:
    """Ratio of boilerplate signatures vs substantive content. 0=good, 1=bad."""
    if not text or not text.strip():
        return 1.0
    matches = _boilerplate_re().findall(text)
    match_count = len(matches)
    words = len(text.split())
    if words < 5:
        return min(1.0, match_count * 0.5)
    return min(1.0, (match_count * 3) / max(1, words))


def _content_density(text: str) -> float:
    """Non-whitespace ratio + sentence structure. Higher = more substantive."""
    if not text:
        return 0.0
    stripped = text.strip()
    if not stripped:
        return 0.0
    non_ws = len(re.sub(r"\s+", "", stripped))
    total = len(stripped)
    ratio = non_ws / total if total > 0 else 0
    sentences = len(re.split(r"[.!?]+", stripped))
    # Bonus for structure (lists, numbered steps)
    has_structure = bool(
        re.search(r"\b\d+[.)]\s|\b(?:step|first|second)\b|\n\s*[-*]", text, re.I)
    )
    density = ratio * (1 + 0.1 * min(sentences, 5))
    if has_structure:
        density = min(1.0, density + 0.1)
    return min(1.0, density)


def _has_url(text: str) -> bool:
    return bool(URL_PATTERN.search(text or ""))


def _has_number_unit(text: str) -> bool:
    return bool(NUMBER_UNIT_PATTERN.search(text or ""))


def _has_transaction_link(text: str, source_url: str = "") -> bool:
    """Check if URL in text or source_url is transactional (order/store/checkout)."""
    combined = f"{text or ''} {source_url or ''}"
    return bool(_transaction_path_re().search(combined))


def compute_hygiene(chunks: list[EvidenceChunk]) -> EvidenceSignatures:
    """Compute hygiene metrics for evidence chunks. Logging only."""
    if not chunks:
        return EvidenceSignatures(
            pct_chunks_with_url=0.0,
            pct_chunks_with_number_unit=0.0,
            pct_chunks_boilerplate_gt_06=0.0,
            median_content_density=0.0,
            chunk_count=0,
        )

    per_chunk: list[ChunkHygiene] = []
    for c in chunks:
        text = (c.full_text or c.snippet) or ""
        boilerplate = _boilerplate_ratio(text)
        density = _content_density(text)
        has_url = _has_url(text) or _has_url(c.source_url or "")
        has_num = _has_number_unit(text)
        has_tx = _has_transaction_link(text, c.source_url or "")
        sentences = len(re.split(r"[.!?]+", text.strip()))
        non_ws = len(re.sub(r"\s+", "", text))
        per_chunk.append(
            ChunkHygiene(
                chunk_id=c.chunk_id,
                boilerplate_ratio=boilerplate,
                content_density=density,
                has_url=has_url,
                has_number_unit=has_num,
                has_transaction_link=has_tx,
                sentence_count=sentences,
                non_whitespace_chars=non_ws,
            )
        )

    n = len(per_chunk)
    pct_url = sum(1 for p in per_chunk if p.has_url) / n * 100
    pct_num = sum(1 for p in per_chunk if p.has_number_unit) / n * 100
    pct_boiler = sum(1 for p in per_chunk if p.boilerplate_ratio > 0.6) / n * 100
    densities = sorted([p.content_density for p in per_chunk])
    median_density = densities[len(densities) // 2] if densities else 0.0

    sigs = EvidenceSignatures(
        pct_chunks_with_url=pct_url,
        pct_chunks_with_number_unit=pct_num,
        pct_chunks_boilerplate_gt_06=pct_boiler,
        median_content_density=median_density,
        chunk_count=n,
        per_chunk=per_chunk,
    )

    logger.debug(
        "evidence_hygiene",
        pct_chunks_with_url=round(pct_url, 1),
        pct_chunks_with_number_unit=round(pct_num, 1),
        pct_chunks_boilerplate_gt_06=round(pct_boiler, 1),
        median_content_density=round(median_density, 3),
        chunk_count=n,
    )
    return sigs
