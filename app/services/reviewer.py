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
from app.services.retry_planner import plan_targeted_retry_queries

logger = get_logger(__name__)

_DEFAULT_EXACT_ANSWER_TYPES = {"direct_link", "pricing", "policy"}
_SOFT_DOC_TYPES = {"faq", "blog", "conversation"}
_PARTIAL_DISCLAIMER_MARKERS = (
    "closest related",
    "closest official",
    "closest official page",
    "closest",
    "best available information",
    "best available official info",
    "best we have",
    "related official page",
    "not verified",
    "not confirmed",
    "unverified",
    "could not verify",
    "we don't have that",
    "couldn't find",
    "don't have that",
)
_PARTIAL_DEFAULT_DISCLAIMER = "That's the best we have from our docs."
_URL_PATTERN = re.compile(r"https?://\S+", re.I)


def _configured_exact_answer_types() -> set[str]:
    raw = getattr(get_settings(), "exact_answer_types", None)
    if isinstance(raw, str):
        configured = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        configured = list(raw)
    else:
        configured = []
    normalized = {
        str(item).strip().lower()
        for item in configured
        if str(item).strip()
    }
    return normalized or set(_DEFAULT_EXACT_ANSWER_TYPES)


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
    calibrated_confidence: float | None = None
    retry_reason: str | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    weakly_supported_claims: list[str] = field(default_factory=list)
    claim_to_citation_map: dict[str, list[str]] = field(default_factory=dict)


def _normalize_answer_type(value: Any, *, default: str = "general") -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "link": "direct_link",
        "order_link": "direct_link",
        "buy_link": "direct_link",
        "price": "pricing",
    }
    normalized = aliases.get(raw, raw)
    return normalized or default


def _normalize_answer_mode(value: Any, *, default: str = "PASS_EXACT") -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "EXACT": "PASS_EXACT",
        "PARTIAL": "PASS_PARTIAL",
        "PASS_WEAK": "PASS_PARTIAL",
        "PASS_STRONG": "PASS_EXACT",
        "CLARIFY": "ASK_USER",
    }
    normalized = aliases.get(raw, raw)
    if normalized in {"PASS_EXACT", "PASS_PARTIAL", "ASK_USER"}:
        return normalized
    return default


def _normalize_support_level(value: Any, *, default: str = "strong") -> str:
    raw = str(value or "").strip().lower()
    if raw in {"strong", "partial", "weak"}:
        return raw
    return default


def _to_str_list(value: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _citation_doc_types(
    citations: list[dict[str, Any]],
    evidence: list[EvidenceChunk],
) -> set[str]:
    by_chunk_id = {e.chunk_id: (e.doc_type or "").strip().lower() for e in evidence}
    doc_types: set[str] = set()
    for c in citations:
        doc_type = str(c.get("doc_type") or "").strip().lower()
        if not doc_type:
            chunk_id = str(c.get("chunk_id") or "").strip()
            if chunk_id:
                doc_type = by_chunk_id.get(chunk_id, "")
        if doc_type:
            doc_types.add(doc_type)
    return doc_types


def _has_partial_disclaimer(answer: str, disclaimers: list[str]) -> bool:
    lowered = (answer or "").lower()
    disclaimer_text = " ".join(disclaimers).lower()
    combined = f"{lowered}\n{disclaimer_text}"
    return any(marker in combined for marker in _PARTIAL_DISCLAIMER_MARKERS)


def _ensure_partial_disclaimer(answer: str, disclaimers: list[str]) -> str:
    text = (answer or "").strip()
    if _has_partial_disclaimer(text, disclaimers):
        return text
    if disclaimers:
        disclaimer_text = " ".join(disclaimers).strip()
        if disclaimer_text:
            return f"{text.rstrip()}\n\n{disclaimer_text}"
    return f"{text.rstrip()}\n\n{_PARTIAL_DEFAULT_DISCLAIMER}".strip()


def _has_link_signal(answer: str, citations: list[dict[str, Any]]) -> bool:
    if _URL_PATTERN.search(answer or ""):
        return True
    return any(str(c.get("source_url") or "").strip().startswith("http") for c in citations)


def _supports_exact_answer_type(
    *,
    expected_answer_type: str,
    answer: str,
    citations: list[dict[str, Any]],
    evidence: list[EvidenceChunk],
) -> bool:
    if not citations:
        return False

    doc_types = _citation_doc_types(citations, evidence)
    expected = _normalize_answer_type(expected_answer_type)

    if expected == "direct_link":
        if not _has_link_signal(answer, citations):
            return False
        if not doc_types:
            return True
        return not doc_types.issubset(_SOFT_DOC_TYPES)

    if expected == "pricing":
        if _has_uncited_numbers(answer):
            return True
        return "pricing" in doc_types

    if expected == "policy":
        return _has_policy_citation(citations, evidence)

    return True


def _looks_overclaiming(
    *,
    expected_answer_type: str,
    answer: str,
    has_partial_disclaimer: bool,
) -> bool:
    if has_partial_disclaimer:
        return False

    lowered = (answer or "").lower()
    if expected_answer_type == "direct_link":
        if _URL_PATTERN.search(answer or ""):
            return True
        markers = (
            "official page",
            "official link",
            "direct link",
            "order here",
            "this is the link",
        )
        return any(marker in lowered for marker in markers)

    if expected_answer_type == "pricing":
        if _has_uncited_numbers(answer):
            return True
        return "price is" in lowered

    if expected_answer_type == "policy":
        if _has_uncited_policy_claims(answer):
            return True
        markers = (
            "policy says",
            "you are eligible",
            "you are entitled",
        )
        return any(marker in lowered for marker in markers)

    return False


def _calibrate_confidence(
    *,
    mode: str,
    support_level: str,
    confidence: float,
) -> float:
    value = max(0.0, min(1.0, float(confidence)))
    if mode == "ASK_USER":
        return min(value, 0.3)
    if mode == "PASS_PARTIAL":
        cap = 0.5 if support_level == "weak" else 0.6
        return min(value, cap)
    cap = 0.92
    if support_level == "partial":
        cap = 0.78
    elif support_level == "weak":
        cap = 0.68
    return min(value, cap)


class AnswerCalibrator:
    """Lightweight verifier for exact-answer tasks (type, overclaim, confidence)."""

    def calibrate(
        self,
        *,
        expected_answer_type: str | None,
        acceptable_related_types: list[str] | None,
        answer_expectation: str | None,
        target_entity: str | None,
        query: str,
        answer: str,
        citations: list[dict[str, Any]],
        evidence: list[EvidenceChunk],
        confidence: float,
        answer_candidate: dict[str, Any] | None,
    ) -> ReviewerResult | None:
        expected = _normalize_answer_type(expected_answer_type)
        if expected not in _configured_exact_answer_types():
            return None

        candidate = answer_candidate or {}
        candidate_type = _normalize_answer_type(candidate.get("answer_type"), default=expected)
        candidate_mode = _normalize_answer_mode(
            candidate.get("answer_mode"),
            default="PASS_EXACT",
        )
        support_level = _normalize_support_level(
            candidate.get("support_level"),
            default="partial" if candidate_mode == "PASS_PARTIAL" else "strong",
        )
        disclaimers = _to_str_list(candidate.get("disclaimers"), limit=3)
        has_partial = _has_partial_disclaimer(answer, disclaimers)
        exact_support = _supports_exact_answer_type(
            expected_answer_type=expected,
            answer=answer,
            citations=citations,
            evidence=evidence,
        )

        acceptable = {
            _normalize_answer_type(item)
            for item in (acceptable_related_types or [])
            if str(item).strip()
        }
        related_type = candidate_type in acceptable
        if expected == "direct_link" and candidate_type in {"pricing", "general"}:
            related_type = True

        overclaim = _looks_overclaiming(
            expected_answer_type=expected,
            answer=answer,
            has_partial_disclaimer=has_partial,
        )

        # Strong exact pass.
        if candidate_type == expected and exact_support:
            return ReviewerResult(
                status=ReviewerStatus.PASS,
                reasons=[],
                suggested_queries=[],
                missing_fields=[],
                final_lane="PASS_EXACT",
                calibrated_confidence=_calibrate_confidence(
                    mode="PASS_EXACT",
                    support_level=support_level,
                    confidence=confidence,
                ),
            )

        # Exact requested, but response is related + explicitly bounded.
        if has_partial and (related_type or candidate_type == expected or candidate_mode == "PASS_PARTIAL"):
            return ReviewerResult(
                status=ReviewerStatus.DOWNGRADE_LANE,
                reasons=["Exact answer unavailable; returning closest related official information."],
                suggested_queries=[],
                missing_fields=[],
                final_lane="PASS_PARTIAL",
                calibrated_confidence=_calibrate_confidence(
                    mode="PASS_PARTIAL",
                    support_level="partial",
                    confidence=confidence,
                ),
            )

        # Mismatch or weak support without bounded wording should not pass as exact.
        mismatch_reason = "Answer type mismatch for exact task."
        if candidate_type == expected and not exact_support:
            mismatch_reason = "Exact answer support is insufficient."
        if overclaim:
            mismatch_reason = "Answer overclaims exactness beyond available evidence."

        # If expectation is already best-effort, allow related but still bounded only.
        expectation = str(answer_expectation or "").strip().lower()
        if expectation != "exact" and related_type and has_partial:
            return ReviewerResult(
                status=ReviewerStatus.DOWNGRADE_LANE,
                reasons=["Best-effort related answer accepted with explicit disclaimer."],
                suggested_queries=[],
                missing_fields=[],
                final_lane="PASS_PARTIAL",
                calibrated_confidence=_calibrate_confidence(
                    mode="PASS_PARTIAL",
                    support_level="partial",
                    confidence=confidence,
                ),
            )

        retry_reason = "type_mismatch"
        if overclaim:
            retry_reason = "overclaim"
        elif candidate_type == expected and not exact_support:
            retry_reason = "unsupported_exact"
        retry_queries = plan_targeted_retry_queries(
            expected_answer_type=expected,
            target_entity=target_entity,
            query=query,
            max_queries=3,
        )

        return ReviewerResult(
            status=ReviewerStatus.ASK_USER,
            reasons=[mismatch_reason],
            suggested_queries=retry_queries,
            missing_fields=["exact_answer_type"],
            final_lane="ASK_USER",
            calibrated_confidence=_calibrate_confidence(
                mode="ASK_USER",
                support_level="weak",
                confidence=confidence,
            ),
            retry_reason=retry_reason,
        )


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
    normalized_lane = _normalize_answer_mode(lane, default="")
    if answer_policy == "bounded" or normalized_lane == "PASS_PARTIAL":
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
        self._settings = get_settings()
        self.require_citations_on_pass = require_citations_on_pass
        self.require_policy_for_high_risk = require_policy_for_high_risk
        self.min_citation_coverage = min_citation_coverage
        self._answer_calibrator = AnswerCalibrator()

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
        expected_answer_type: str | None = None,
        acceptable_related_types: list[str] | None = None,
        answer_expectation: str = "best_effort",
        target_entity: str | None = None,
        answer_candidate: dict[str, Any] | None = None,
    ) -> ReviewerResult:
        """Run reviewer checks. Returns status and reasons."""
        _ = (retrieval_attempt, max_attempts)
        reasons: list[str] = []
        missing_fields: list[str] = []
        is_bounded = _is_bounded_answer(answer, answer_policy, lane)
        candidate_payload = answer_candidate or {}
        candidate_mode = _normalize_answer_mode(
            candidate_payload.get("answer_mode"),
            default="PASS_EXACT",
        )
        candidate_support_level = _normalize_support_level(
            candidate_payload.get("support_level"),
            default="partial" if candidate_mode == "PASS_PARTIAL" else "strong",
        )
        candidate_disclaimers = _to_str_list(candidate_payload.get("disclaimers"), limit=3)
        lane_mode = _normalize_answer_mode(lane, default="")
        partial_requested = bool(
            answer_policy == "bounded"
            or lane_mode == "PASS_PARTIAL"
            or candidate_mode == "PASS_PARTIAL"
        )
        has_partial_disclaimer = _has_partial_disclaimer(answer, candidate_disclaimers)

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

            if bool(getattr(self._settings, "soft_contract_enabled", True)):
                if bool(getattr(self._settings, "answer_candidate_enabled", True)):
                    calibrated_exact = self._answer_calibrator.calibrate(
                        expected_answer_type=expected_answer_type,
                        acceptable_related_types=acceptable_related_types,
                        answer_expectation=answer_expectation,
                        target_entity=target_entity,
                        query=query,
                        answer=answer,
                        citations=citations,
                        evidence=evidence,
                        confidence=confidence,
                        answer_candidate=answer_candidate,
                    )
                    if calibrated_exact is not None:
                        return calibrated_exact

            if partial_requested and not has_partial_disclaimer:
                reasons.append("PASS_PARTIAL requires an explicit disclaimer.")
                return ReviewerResult(
                    status=ReviewerStatus.DOWNGRADE_LANE,
                    reasons=reasons,
                    suggested_queries=[],
                    missing_fields=[],
                    trimmed_answer=_ensure_partial_disclaimer(answer, candidate_disclaimers),
                    final_lane="PASS_PARTIAL",
                    calibrated_confidence=_calibrate_confidence(
                        mode="PASS_PARTIAL",
                        support_level=candidate_support_level,
                        confidence=confidence,
                    ),
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
                        final_lane="PASS_PARTIAL" if alt_status == ReviewerStatus.DOWNGRADE_LANE else None,
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
                        final_lane="PASS_PARTIAL" if alt_status == ReviewerStatus.DOWNGRADE_LANE else None,
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
                        final_lane="PASS_PARTIAL" if alt_status == ReviewerStatus.DOWNGRADE_LANE else None,
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
                final_lane="PASS_PARTIAL" if partial_requested else "PASS_EXACT",
                calibrated_confidence=(
                    _calibrate_confidence(
                        mode="PASS_PARTIAL",
                        support_level=candidate_support_level,
                        confidence=confidence,
                    )
                    if partial_requested
                    else _calibrate_confidence(
                        mode="PASS_EXACT",
                        support_level=candidate_support_level,
                        confidence=confidence,
                    )
                ),
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
