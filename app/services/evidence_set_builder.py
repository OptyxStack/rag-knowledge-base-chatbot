"""Evidence Set Builder – Workstream 3: Select answer-ready evidence from CandidatePool.

Per UPGRADE_RAG_DESIGN:
- EvidenceSet optimized for answerability, not raw rank
- coverage_map from Evidence Selector (LLM) is the only source for covered_requirements
- Primary vs supporting chunks; coverage metadata for explainability
"""

from __future__ import annotations

from app.core.config import get_settings
from app.search.base import EvidenceChunk, SearchChunk
from app.services.schemas import CandidatePool, EvidenceSet, QuerySpec, RetrievalPlan


def _coverage_mapping_allowed(chunk: SearchChunk, req: str) -> bool:
    req_key = (req or "").strip().lower()
    doc_type = (chunk.doc_type or "").strip().lower()
    settings = get_settings()
    if req_key == "policy_language":
        configured_policy_types = {
            str(t).strip().lower()
            for t in (settings.reviewer_policy_doc_types or [])
            if str(t).strip()
        }
        if configured_policy_types:
            return doc_type in configured_policy_types
        return doc_type in {"policy", "tos"}
    if req_key == "steps_structure":
        return doc_type in {"howto", "docs", "faq", "conversation"}
    return req_key in {"numbers_units", "transaction_link", "has_any_url"}


def build_evidence_set(
    reranked: list[tuple[SearchChunk, float]],
    query_spec: QuerySpec | None,
    plan: RetrievalPlan | None,
    pool: CandidatePool | None = None,
    coverage_map: dict[str, str] | None = None,
) -> EvidenceSet:
    """Build EvidenceSet from reranked chunks.

    - coverage_map: from Evidence Selector (LLM) – requirement -> chunk_id. When present,
      used for covered_requirements and primary chunks; avoids regex heuristic.
    - Primary chunks: from coverage_map when available, else first 3 by rank
    - Supporting chunks: remaining selected chunks
    """
    hard = set()
    soft = set()
    if query_spec:
        hard = {
            str(x)
            for x in (getattr(query_spec, "hard_requirements", None) or (query_spec.required_evidence or []))
            if isinstance(x, str)
        }
        soft = {
            str(x)
            for x in (getattr(query_spec, "soft_requirements", None) or [])
            if isinstance(x, str)
        }
        resolved = query_spec.resolved_slots or {}
    else:
        resolved = {}
    slot_names = set(resolved.keys())

    evidence_chunks: list[EvidenceChunk] = []
    primary_ids: list[str] = []
    supporting_ids: list[str] = []
    covered_req: set[str] = set()
    covered_slots: set[str] = set()
    chunk_ids_in_reranked = {c.chunk_id for c, _ in reranked}
    chunk_by_id = {c.chunk_id: c for c, _ in reranked}
    validated_coverage_map: dict[str, str] = {}
    if coverage_map:
        for req, cid in coverage_map.items():
            chunk = chunk_by_id.get(cid)
            if not chunk:
                continue
            if req not in (hard | soft):
                continue
            if not _coverage_mapping_allowed(chunk, req):
                continue
            validated_coverage_map[req] = cid

    for chunk, score in reranked:
        snippet = (chunk.chunk_text or "")[:500]
        if len(chunk.chunk_text or "") > 500:
            snippet += "..."
        ec = EvidenceChunk(
            chunk_id=chunk.chunk_id,
            snippet=snippet,
            source_url=chunk.source_url or "",
            doc_type=chunk.doc_type or "",
            score=score,
            full_text=chunk.chunk_text,
        )
        evidence_chunks.append(ec)

        if validated_coverage_map:
            for req, cid in validated_coverage_map.items():
                if cid == chunk.chunk_id and req in (hard | soft):
                    covered_req.add(req)
        text_lower = (chunk.chunk_text or "").lower()
        for slot_name, slot_val in resolved.items():
            if slot_val and str(slot_val).lower() in text_lower:
                covered_slots.add(slot_name)

    uncovered_req = (hard | soft) - covered_req
    uncovered_slots = slot_names - covered_slots

    # Primary: from coverage_map when available, else first 3 by rank
    if validated_coverage_map:
        coverage_primary = [
            cid for req, cid in validated_coverage_map.items()
            if cid in chunk_ids_in_reranked
        ]
        primary_ids = list(dict.fromkeys(coverage_primary))
        for chunk, _ in reranked:
            if chunk.chunk_id not in primary_ids and len(primary_ids) < 3:
                primary_ids.append(chunk.chunk_id)
        for chunk, _ in reranked:
            if chunk.chunk_id not in primary_ids:
                supporting_ids.append(chunk.chunk_id)
    else:
        for i, (chunk, _) in enumerate(reranked):
            cid = chunk.chunk_id
            if i < 3:
                primary_ids.append(cid)
            else:
                supporting_ids.append(cid)

    # Trust mix: doc_type distribution
    trust_mix: dict[str, float] = {}
    for ec in evidence_chunks:
        dt = ec.doc_type or "unknown"
        trust_mix[dt] = trust_mix.get(dt, 0.0) + (ec.score or 0.0)
    total = sum(trust_mix.values()) or 1.0
    trust_mix = {k: round(v / total, 3) for k, v in trust_mix.items()}

    diversity = len(trust_mix) / max(len(evidence_chunks), 1) if evidence_chunks else 0.0
    concentration = max(trust_mix.values(), default=0.0)

    build_reason = "rerank_top_k"
    if plan:
        build_reason = f"profile={plan.profile}, attempt={plan.attempt_index}, reason={plan.reason}"

    return EvidenceSet(
        chunks=evidence_chunks,
        primary_chunks=primary_ids,
        supporting_chunks=supporting_ids,
        covered_requirements=sorted(covered_req),
        uncovered_requirements=sorted(uncovered_req),
        covered_slots=sorted(covered_slots),
        uncovered_slots=sorted(uncovered_slots),
        trust_mix=trust_mix,
        diversity_score=round(diversity, 3),
        concentration_score=round(concentration, 3),
        evidence_summary=f"{len(evidence_chunks)} chunks, {len(covered_req)}/{len(hard|soft)} requirements",
        build_reason=build_reason,
    )
