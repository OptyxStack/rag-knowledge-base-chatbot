"""Reviewer gate: rule-based quality checks. Workstream 5: claim-level trim + lane downgrade."""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk

from app.services.claim_parser import (
    segment_claims,
    is_risky_claim,
    is_policy_claim,
    is_number_claim,
    trim_unsupported_claims,
)

logger = get_logger(__name__)


class ReviewerStatus(str, Enum):
    PASS = "PASS"
    ASK_USER = "ASK_USER"
    RETRIEVE_MORE = "RETRIEVE_MORE"  # legacy compatibility
    ESCALATE = "ESCALATE"
    TRIM_UNSUPPORTED = "TRIM_UNSUPPORTED"
    DOWNGRADE_LANE = "DOWNGRADE_LANE"


@dataclass
class ReviewerResult:
    """Result of reviewer gate."""

    status: ReviewerStatus
    reasons: list[str]
    suggested_queries: list[str]
    missing_fields: list[str]
    trimmed_answer: str | None = None
    final_lane: str | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    weakly_supported_claims: list[str] = field(default_factory=list)
    claim_to_citation_map: dict[str, list[str]] = field(default_factory=dict)


def _is_high_risk_query(query: str) -> bool:
    """Check if query matches configured high-risk patterns."""
    patterns = [p for p in (get_settings().reviewer_high_risk_patterns or []) if str(p).strip()]
    if not patterns:
        return False
    q = query.lower()
    for pattern in patterns:
        try:
            if re.search(pattern, q, re.I):
                return True
        except re.error:
            logger.warning("reviewer_invalid_high_risk_pattern", pattern=pattern)
    return False


def _has_policy_citation(citations: list[dict], evidence: list[EvidenceChunk]) -> bool:
    """Check if any citation references configured policy-like doc_type."""
    cited_ids = {c.get("chunk_id") for c in citations}
    required_types = {
        str(t).strip().lower()
        for t in (get_settings().reviewer_policy_doc_types or [])
        if str(t).strip()
    }
    if not required_types:
        evidence_ids = {e.chunk_id for e in evidence}
        return bool(cited_ids & evidence_ids)
    for e in evidence:
        if e.chunk_id in cited_ids and (e.doc_type or "").lower() in required_types:
            return True
    return False


def _citation_coverage(answer: str, citations: list[dict]) -> float:
    """Estimate how much of answer is cited (rough heuristic)."""
    if not citations:
        return 0.0
    # Count sentences in answer
    sentences = re.split(r"[.!?]+", answer)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 1.0
    # Assume each citation covers at least one claim
    return min(1.0, len(citations) / max(1, len(sentences)))


def _has_uncited_numbers(answer: str) -> bool:
    """Check for numbers/prices that might need citation."""
    # Look for price-like patterns: $X, X%, dates, etc.
    price_pattern = r"\$[\d,]+\.?\d*|[\d]+%|\d{1,2}/\d{1,2}/\d{2,4}"
    matches = re.findall(price_pattern, answer)
    return len(matches) > 0


def _has_uncited_policy_claims(answer: str) -> bool:
    """Heuristic: configured policy-like phrases that should be cited."""
    for pattern in (get_settings().reviewer_policy_claim_patterns or []):
        try:
            if re.search(pattern, answer, re.I):
                return True
        except re.error:
            logger.warning("reviewer_invalid_policy_claim_pattern", pattern=pattern)
            continue
    return False


def _is_bounded_answer(
    answer: str,
    answer_policy: str,
    lane: str | None,
) -> bool:
    """Detect bounded-answer mode from lane, policy, or explicit wording."""
    if answer_policy == "bounded" or lane == "PASS_WEAK":
        return True

    lowered = answer.lower()
    bounded_markers = (
        "not verified",
        "not confirmed",
        "unverified",
        "i only confirmed",
        "available evidence",
        "could not verify",
    )
    return any(marker in lowered for marker in bounded_markers)


def _build_claim_to_citation_map(
    answer: str,
    citations: list[dict],
    evidence: list[EvidenceChunk],
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Build claim_to_citation_map, unsupported_claims, weakly_supported_claims.

    Heuristic: risky claims need strong citation support. With few citations
    we cannot attribute them to specific claims, so risky claims are unsupported.
    """
    claim_to_citation: dict[str, list[str]] = {}
    unsupported: list[str] = []
    weakly: list[str] = []
    cited_ids = {c.get("chunk_id") for c in citations if c.get("chunk_id")}
    evidence_ids = {e.chunk_id for e in evidence}
    has_valid_citations = bool(cited_ids & evidence_ids)

    claims = segment_claims(answer)
    has_policy_citation = _has_policy_citation(citations, evidence)
    # Policy claims + policy/tos citation: 1 citation is enough
    # Number claims: still need 2 (price/specs need stronger support)

    for c in claims:
        claim_to_citation[c.text] = list(cited_ids & evidence_ids)
        if is_risky_claim(c.text):
            if not has_valid_citations:
                unsupported.append(c.text)
            elif is_policy_claim(c.text) and has_policy_citation:
                weakly.append(c.text)  # 1 policy citation ok for policy claims
            elif is_number_claim(c.text) and len(citations) < 2:
                unsupported.append(c.text)
            elif len(citations) < 2:
                unsupported.append(c.text)
            else:
                weakly.append(c.text)
        else:
            if not has_valid_citations:
                weakly.append(c.text)

    return claim_to_citation, unsupported, weakly


def _try_trim_or_downgrade(
    answer: str,
    citations: list[dict],
    evidence: list[EvidenceChunk],
    failure_reason: str,
    is_bounded: bool,
) -> tuple[ReviewerStatus | None, str | None, list[str], list[str], dict[str, list[str]]]:
    """Try trim or downgrade instead of ASK_USER. Returns (status, trimmed_answer, unsupported, weakly, claim_map)."""
    if not getattr(get_settings(), "claim_level_review_enabled", True):
        return None, None, [], [], {}

    claim_to_citation, unsupported, weakly = _build_claim_to_citation_map(
        answer, citations, evidence
    )
    has_valid_citations = bool(citations) and any(
        c.get("chunk_id") in {e.chunk_id for e in evidence} for c in citations
    )

    if unsupported and has_valid_citations:
        unsupported_indices = [
            i for i, c in enumerate(segment_claims(answer))
            if c.text in unsupported
        ]
        trimmed = trim_unsupported_claims(answer, unsupported_indices)
        if trimmed and len(trimmed) >= 30:
            return (
                ReviewerStatus.TRIM_UNSUPPORTED,
                trimmed,
                unsupported,
                weakly,
                claim_to_citation,
            )

    soft_failures = (
        "insufficient citations",
        "low citation coverage",
        "numbers",
        "policy",
    )
    if has_valid_citations and is_bounded and any(s in failure_reason.lower() for s in soft_failures):
        return (
            ReviewerStatus.DOWNGRADE_LANE,
            None,
            unsupported,
            weakly,
            claim_to_citation,
        )

    return None, None, unsupported, weakly, claim_to_citation


class ReviewerGate:
    """Rule-based reviewer gate."""

    def __init__(
        self,
        require_citations_on_pass: bool = True,
        require_policy_for_high_risk: bool = True,
        min_citation_coverage: float = 0.3,
    ) -> None:
        self.require_citations_on_pass = require_citations_on_pass
        self.require_policy_for_high_risk = require_policy_for_high_risk
        self.min_citation_coverage = min_citation_coverage

    def review(
        self,
        decision: str,
        answer: str,
        citations: list[dict[str, Any]],
        evidence: list[EvidenceChunk],
        query: str,
        confidence: float,
        retrieval_attempt: int = 1,
        max_attempts: int = 2,
        answer_policy: str = "direct",
        lane: str | None = None,
    ) -> ReviewerResult:
        """Run reviewer checks. Returns status and reasons."""
        _ = (retrieval_attempt, max_attempts, confidence, query)
        reasons: list[str] = []
        missing_fields: list[str] = []
        is_bounded = _is_bounded_answer(answer, answer_policy, lane)

        # 1. PASS decision checks
        if decision == "PASS":
            if self.require_citations_on_pass and not citations:
                reasons.append("PASS requires at least one citation")
                return ReviewerResult(
                    status=ReviewerStatus.ASK_USER,
                    reasons=reasons,
                    suggested_queries=[],
                    missing_fields=["citations"],
                )

            # Citations must correspond to evidence
            evidence_ids = {e.chunk_id for e in evidence}
            for c in citations:
                cid = c.get("chunk_id")
                if cid and cid not in evidence_ids:
                    reasons.append(f"Citation chunk_id {cid} not in evidence")
                    return ReviewerResult(
                        status=ReviewerStatus.ASK_USER,
                        reasons=reasons,
                        suggested_queries=[],
                        missing_fields=[],
                    )

            # Numbers/prices without citation
            if _has_uncited_numbers(answer) and len(citations) < 2 and not is_bounded:
                reasons.append("Answer contains numbers/prices but insufficient citations")
                alt_status, trimmed, u, w, cm = _try_trim_or_downgrade(
                    answer, citations, evidence, "numbers", is_bounded
                )
                if alt_status:
                    return ReviewerResult(
                        status=alt_status,
                        reasons=reasons,
                        suggested_queries=[],
                        missing_fields=[],
                        trimmed_answer=trimmed,
                        final_lane="PASS_WEAK" if alt_status == ReviewerStatus.DOWNGRADE_LANE else None,
                        unsupported_claims=u,
                        weakly_supported_claims=w,
                        claim_to_citation_map=cm,
                    )
                return ReviewerResult(
                    status=ReviewerStatus.ASK_USER,
                    reasons=reasons,
                    suggested_queries=[],
                    missing_fields=[],
                )

            # Policy claims without citation
            if (
                _has_uncited_policy_claims(answer)
                and len(citations) < 2
                and not _has_policy_citation(citations, evidence)
                and not is_bounded
            ):
                reasons.append("Answer contains policy-like claims but insufficient citations")
                alt_status, trimmed, u, w, cm = _try_trim_or_downgrade(
                    answer, citations, evidence, "policy", is_bounded
                )
                if alt_status:
                    return ReviewerResult(
                        status=alt_status,
                        reasons=reasons,
                        suggested_queries=[],
                        missing_fields=[],
                        trimmed_answer=trimmed,
                        final_lane="PASS_WEAK" if alt_status == ReviewerStatus.DOWNGRADE_LANE else None,
                        unsupported_claims=u,
                        weakly_supported_claims=w,
                        claim_to_citation_map=cm,
                    )
                return ReviewerResult(
                    status=ReviewerStatus.ASK_USER,
                    reasons=reasons,
                    suggested_queries=[],
                    missing_fields=[],
                )

            # High-risk query: require policy citation
            if self.require_policy_for_high_risk and _is_high_risk_query(query):
                if not _has_policy_citation(citations, evidence):
                    reasons.append("High-risk query requires policy/tos citation")
                    return ReviewerResult(
                        status=ReviewerStatus.ESCALATE,
                        reasons=reasons,
                        suggested_queries=[],
                        missing_fields=[],
                    )

            # Citation coverage
            cov = _citation_coverage(answer, citations)
            if cov < self.min_citation_coverage and len(citations) < 2 and not is_bounded:
                reasons.append(f"Low citation coverage ({cov:.2f})")
                alt_status, trimmed, u, w, cm = _try_trim_or_downgrade(
                    answer, citations, evidence, "low citation coverage", is_bounded
                )
                if alt_status:
                    return ReviewerResult(
                        status=alt_status,
                        reasons=reasons,
                        suggested_queries=[],
                        missing_fields=[],
                        trimmed_answer=trimmed,
                        final_lane="PASS_WEAK" if alt_status == ReviewerStatus.DOWNGRADE_LANE else None,
                        unsupported_claims=u,
                        weakly_supported_claims=w,
                        claim_to_citation_map=cm,
                    )
                return ReviewerResult(
                    status=ReviewerStatus.ASK_USER,
                    reasons=reasons,
                    suggested_queries=[],
                    missing_fields=[],
                )

            return ReviewerResult(
                status=ReviewerStatus.PASS,
                reasons=[],
                suggested_queries=[],
                missing_fields=[],
            )

        # 2. ASK_USER - no additional checks
        if decision == "ASK_USER":
            return ReviewerResult(
                status=ReviewerStatus.ASK_USER,
                reasons=reasons,
                suggested_queries=[],
                missing_fields=missing_fields,
            )

        # 3. ESCALATE
        if decision == "ESCALATE":
            return ReviewerResult(
                status=ReviewerStatus.ESCALATE,
                reasons=reasons,
                suggested_queries=[],
                missing_fields=[],
            )

        reasons.append("Unsupported reviewer input decision; defaulting to ASK_USER")
        return ReviewerResult(
            status=ReviewerStatus.ASK_USER,
            reasons=reasons,
            suggested_queries=[],
            missing_fields=["clarification"],
        )
