"""Claim-level segmentation for post-generation verification (Workstream 5).

Segments answer into claims for claim-to-citation mapping and trim decisions.
"""

import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Claim:
    """Single claim (sentence or bullet) from answer."""

    text: str
    start: int
    end: int
    index: int


def segment_claims(answer: str) -> list[Claim]:
    """Segment answer into claims. Splits by sentence boundaries and bullet points."""
    if not answer or not answer.strip():
        return []

    text = answer.strip()
    # Split by sentence boundaries (. ! ?) and newlines
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    claims: list[Claim] = []
    pos = 0
    for idx, raw in enumerate(parts):
        p = raw.strip()
        if not p or len(p) < 3:
            pos += len(raw) + 1
            continue
        bullet = re.match(r"^[\-\*•]\s*", p)
        if bullet:
            p = p[bullet.end() :].strip()
        if not p:
            pos += len(raw) + 1
            continue
        start = text.find(p, max(0, pos - 1))
        if start < 0:
            start = pos
        end = start + len(p)
        claims.append(Claim(text=p, start=start, end=end, index=len(claims)))
        pos = end + 1
    if not claims and text:
        claims = [Claim(text=text, start=0, end=len(text), index=0)]
    return claims


def is_risky_claim(claim_text: str) -> bool:
    """True if claim contains patterns that typically need citation (numbers, policy)."""
    return is_number_claim(claim_text) or is_policy_claim(claim_text)


def is_number_claim(claim_text: str) -> bool:
    """True if claim has price/number patterns."""
    t = claim_text.lower()
    return bool(re.search(r"\$[\d,]+\.?\d*|[\d]+%|\d{1,2}/\d{1,2}/\d{2,4}", t))


def is_policy_claim(claim_text: str) -> bool:
    """True if claim matches configured policy-claim patterns."""
    t = claim_text.lower()
    patterns = get_settings().claim_parser_policy_patterns or []
    for pattern in patterns:
        try:
            if re.search(pattern, t):
                return True
        except re.error:
            logger.warning("claim_parser_invalid_policy_pattern", pattern=pattern)
    return False


def trim_unsupported_claims(
    answer: str,
    unsupported_indices: list[int],
) -> str:
    """Remove claims at given indices, return trimmed answer."""
    claims = segment_claims(answer)
    if not claims or not unsupported_indices:
        return answer
    drop = set(unsupported_indices)
    kept = [c for c in claims if c.index not in drop]
    if not kept:
        return answer
    return " ".join(c.text for c in kept).strip()
