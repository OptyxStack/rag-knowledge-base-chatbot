"""Evidence Quality Gate – Phase 1: Domain-agnostic scoring by features.

Score by evidence features, not domain logic. doc_type only as weak prior.
PASS only when all required features >= per-feature thresholds.
"""

import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk

from app.services.evidence_hygiene import compute_hygiene

logger = get_logger(__name__)

# Number + unit patterns
NUMBER_UNIT_PATTERN = re.compile(
    r"\$[\d,]+\.?\d*|"
    r"[\d,]+\.?\d*\s*(?:USD|VND|EUR|GBP|/mo|/month|/year|%|MB|GB|TB)\b|"
    r"\b\d+\s*(?:USD|VND|EUR|GBP|%|MB|GB|TB)\b",
    re.I,
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.I)
TRANSACTION_PATH_PATTERN = re.compile(
    r"/(?:order|store|checkout|cart|buy|purchase|subscribe)/?", re.I
)

# Policy language – normative pattern groups
POLICY_OBLIGATION = re.compile(
    r"\b(must|shall|required|prohibited|obliged)\b", re.I
)
POLICY_ENTITLEMENT = re.compile(
    r"\b(eligible|refund|within|fee applies|entitled)\b", re.I
)
POLICY_SCOPE = re.compile(
    r"\b(terms|policy|SLA|abuse|cancellation)\b", re.I
)

# Steps structure
STEPS_PATTERN = re.compile(
    r"\b\d+[.)]\s|\b(?:step\s+\d+|first|second|third)\b|\n\s*[-*•]\s",
    re.I,
)

# Boilerplate
BOILERPLATE_PATTERNS = [
    r"\bcontact\s+(?:us|support)\b",
    r"\bcopyright\s+©?\s*\d{4}",
    r"\b(?:privacy|terms)\s+(?:of\s+)?(?:service|policy)\b",
    r"\bmenu\b",
    r"\ball\s+rights\s+reserved\b",
]
BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_PATTERNS), re.I)

# Trust tier: official > user-generated (weak prior)
TRUST_OFFICIAL = {"policy", "tos", "pricing", "docs", "faq"}
TRUST_USER = {"ticket", "forum", "user_generated"}


@dataclass
class QualityReport:
    """Explainable quality report."""

    quality_score: float  # 0–1 aggregate
    feature_scores: dict[str, float]
    missing_signals: list[str]
    staleness_risk: float | None
    boilerplate_risk: float | None


# required_evidence → feature mapping
REQUIRED_TO_FEATURE = {
    "numbers": "numbers_units",
    "numbers_units": "numbers_units",
    "links": "has_any_url",
    "has_any_url": "has_any_url",
    "transaction_link": "has_transaction_link",
    "has_transaction_link": "has_transaction_link",
    "policy_clause": "policy_language",
    "policy_language": "policy_language",
    "steps": "steps_structure",
    "steps_structure": "steps_structure",
    "citations": "has_any_url",  # links suffice for citations
}

# feature → missing_signal
FEATURE_TO_MISSING = {
    "numbers_units": "missing_numbers",
    "has_any_url": "missing_links",
    "has_transaction_link": "missing_transaction_link",
    "policy_language": "missing_policy",
    "steps_structure": "missing_steps",
    "content_density": "boilerplate_risk",
    "boilerplate_ratio": "boilerplate_risk",
}


def _score_numbers_units(chunks: list[EvidenceChunk]) -> float:
    n = len(chunks)
    if n == 0:
        return 0.0
    count = sum(
        1
        for c in chunks
        if NUMBER_UNIT_PATTERN.search((c.full_text or c.snippet) or "")
    )
    return count / n


def _score_has_any_url(chunks: list[EvidenceChunk]) -> float:
    n = len(chunks)
    if n == 0:
        return 0.0
    count = sum(
        1
        for c in chunks
        if URL_PATTERN.search((c.full_text or c.snippet) or "") or URL_PATTERN.search(c.source_url or "")
    )
    return count / n


def _score_has_transaction_link(chunks: list[EvidenceChunk]) -> float:
    n = len(chunks)
    if n == 0:
        return 0.0
    count = 0
    for c in chunks:
        text = (c.full_text or c.snippet) or ""
        combined = f"{text} {c.source_url or ''}"
        if TRANSACTION_PATH_PATTERN.search(combined):
            count += 1
    return count / n


def _score_policy_language(chunks: list[EvidenceChunk]) -> float:
    """Score based on normative patterns (obligation, entitlement, scope)."""
    n = len(chunks)
    if n == 0:
        return 0.0
    scores = []
    for c in chunks:
        text = (c.full_text or c.snippet) or ""
        groups_matched = 0
        if POLICY_OBLIGATION.search(text):
            groups_matched += 1
        if POLICY_ENTITLEMENT.search(text):
            groups_matched += 1
        if POLICY_SCOPE.search(text):
            groups_matched += 1
        # 2+ groups → high confidence
        scores.append(1.0 if groups_matched >= 2 else (0.5 if groups_matched >= 1 else 0.0))
    return sum(scores) / n


def _score_steps_structure(chunks: list[EvidenceChunk]) -> float:
    n = len(chunks)
    if n == 0:
        return 0.0
    count = sum(
        1
        for c in chunks
        if STEPS_PATTERN.search((c.full_text or c.snippet) or "")
    )
    return count / n


def _score_content_density(chunks: list[EvidenceChunk]) -> float:
    sigs = compute_hygiene(chunks)
    return sigs.median_content_density


def _score_boilerplate_ratio(chunks: list[EvidenceChunk]) -> float:
    """Lower boilerplate = better. Return 1 - risk."""
    sigs = compute_hygiene(chunks)
    pct_bad = sigs.pct_chunks_boilerplate_gt_06 / 100.0
    return 1.0 - min(1.0, pct_bad)


def _score_freshness(chunks: list[EvidenceChunk]) -> float | None:
    """effective_date decay if metadata available. None = neutral."""
    # EvidenceChunk doesn't have effective_date; would need metadata
    return None


def _score_trust_tier(chunks: list[EvidenceChunk]) -> float:
    """doc_type weak prior: official > user-generated."""
    n = len(chunks)
    if n == 0:
        return 0.5
    official = sum(1 for c in chunks if (c.doc_type or "").lower() in TRUST_OFFICIAL)
    user = sum(1 for c in chunks if (c.doc_type or "").lower() in TRUST_USER)
    # 0.5 base, +0.25 if more official, -0.25 if more user
    base = 0.5
    if official > user:
        base += 0.25 * min(1.0, official / n)
    elif user > official:
        base -= 0.25 * min(1.0, user / n)
    return max(0.0, min(1.0, base))


def _derive_missing_signals(
    feature_scores: dict[str, float],
    required_evidence: list[str],
    thresholds: dict[str, float],
) -> list[str]:
    """Derive missing_signals from feature_scores and required_evidence."""
    missing: list[str] = []
    required_features = set()
    for req in required_evidence:
        feat = REQUIRED_TO_FEATURE.get(req, req)
        required_features.add(feat)

    for feat, score in feature_scores.items():
        thresh = thresholds.get(feat, 0.3)
        if feat in required_features and score < thresh:
            sig = FEATURE_TO_MISSING.get(feat, f"missing_{feat}")
            if sig not in missing:
                missing.append(sig)
        if feat == "boilerplate_ratio" and score < 0.4:
            if "boilerplate_risk" not in missing:
                missing.append("boilerplate_risk")

    return missing


def evaluate_quality(
    chunks: list[EvidenceChunk],
    required_evidence: list[str] | None = None,
) -> QualityReport:
    """Evaluate evidence quality. Domain-agnostic, feature-based."""
    settings = get_settings()
    thresholds = getattr(settings, "evidence_feature_thresholds", None) or {
        "numbers_units": 0.3,
        "has_any_url": 0.2,
        "has_transaction_link": 0.2,
        "policy_language": 0.3,
        "steps_structure": 0.2,
        "content_density": 0.3,
        "boilerplate_ratio": 0.4,
    }

    if not chunks:
        return QualityReport(
            quality_score=0.0,
            feature_scores={},
            missing_signals=["missing_evidence"] if required_evidence else [],
            staleness_risk=None,
            boilerplate_risk=1.0,
        )

    feature_scores = {
        "numbers_units": _score_numbers_units(chunks),
        "has_any_url": _score_has_any_url(chunks),
        "has_transaction_link": _score_has_transaction_link(chunks),
        "policy_language": _score_policy_language(chunks),
        "steps_structure": _score_steps_structure(chunks),
        "content_density": _score_content_density(chunks),
        "boilerplate_ratio": _score_boilerplate_ratio(chunks),
        "trust_tier": _score_trust_tier(chunks),
    }
    freshness = _score_freshness(chunks)
    if freshness is not None:
        feature_scores["freshness"] = freshness

    aggregate = sum(feature_scores.values()) / len(feature_scores)
    missing_signals = _derive_missing_signals(
        feature_scores,
        required_evidence or [],
        thresholds,
    )

    sigs = compute_hygiene(chunks)
    boilerplate_risk = sigs.pct_chunks_boilerplate_gt_06 / 100.0

    return QualityReport(
        quality_score=round(aggregate, 3),
        feature_scores={k: round(v, 3) for k, v in feature_scores.items()},
        missing_signals=missing_signals,
        staleness_risk=None,  # would need metadata
        boilerplate_risk=round(boilerplate_risk, 3),
    )


def infer_required_evidence(query: str) -> list[str]:
    """Rule-based inference of required_evidence from query (transactional vs policy)."""
    q = query.lower().strip()
    required: list[str] = []
    if any(kw in q for kw in ["price", "cost", "pricing", "giá", "bao nhiêu"]):
        required.extend(["numbers_units", "transaction_link"])
    if any(kw in q for kw in ["link", "order", "mua", "buy", "subscribe"]):
        required.append("transaction_link")
    if any(kw in q for kw in ["refund", "policy", "terms", "hoàn tiền", "chính sách"]):
        required.append("policy_language")
    if any(kw in q for kw in ["how", "step", "cách", "hướng dẫn"]):
        required.append("steps_structure")
    # Comparison queries (diff, difference, compare): need specs + links
    if any(kw in q for kw in ["diff", "difference", "compare", "khác", "so sánh"]):
        required.extend(["numbers_units", "has_any_url"])
    return list(dict.fromkeys(required))


def passes_quality_gate(
    report: QualityReport,
    required_evidence: list[str] | None,
    thresholds: dict[str, float] | None = None,
) -> bool:
    """PASS only when all required features >= per-feature thresholds."""
    settings = get_settings()
    if not getattr(settings, "evidence_quality_enabled", True):
        return True

    thresh = thresholds or getattr(settings, "evidence_feature_thresholds", None) or {
        "numbers_units": 0.3,
        "has_any_url": 0.2,
        "has_transaction_link": 0.2,
        "policy_language": 0.3,
        "steps_structure": 0.2,
    }

    if not required_evidence:
        # No required evidence → optional aggregate check
        agg_thresh = getattr(settings, "evidence_quality_threshold", 0.6)
        return report.quality_score >= agg_thresh

    for req in required_evidence:
        feat = REQUIRED_TO_FEATURE.get(req, req)
        score = report.feature_scores.get(feat, 0.0)
        min_score = thresh.get(feat, 0.3)
        if score < min_score:
            return False
    return True
