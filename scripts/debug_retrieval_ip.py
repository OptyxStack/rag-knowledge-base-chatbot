#!/usr/bin/env python3
"""Debug retrieval for "can i buy more ips for my vps" – why chunk with IP pricing not found.

Usage:
    python scripts/debug_retrieval_ip.py
    python scripts/debug_retrieval_ip.py --query "additional IPs VPS"
"""

import argparse
import asyncio
import sys
import io

# Avoid UnicodeEncodeError on Windows console
if sys.stdout.encoding and "utf" not in sys.stdout.encoding.lower():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from app.core.config import get_settings
from app.search.embeddings import get_embedding_provider
from app.search.opensearch_client import OpenSearchClient
from app.search.qdrant_client import QdrantSearchClient
from app.search.reranker import get_reranker_provider
from app.search.base import SearchChunk
from app.services.retrieval_planner import build_retrieval_plan_for_attempt
from app.services.normalizer import normalize


def _trunc(s: str, n: int = 120) -> str:
    s = (s or "").strip()
    # ASCII-safe for Windows console
    s = s.encode("ascii", errors="replace").decode("ascii")
    return (s[:n] + "...") if len(s) > n else s


async def check_chunk_exists_in_opensearch(opensearch: OpenSearchClient, phrase: str) -> list:
    """Search OpenSearch for phrase (no doc_type filter) to see if chunk exists."""
    chunks = await opensearch.search(
        query=phrase,
        top_n=20,
        doc_types=None,  # No filter – search all
        boost_pricing=False,
        use_highlight=False,
    )
    return chunks


async def run_bm25(opensearch: OpenSearchClient, query: str, doc_types: list[str] | None, top_n: int = 30) -> list:
    """Run BM25 search."""
    return await opensearch.search(
        query=query,
        top_n=top_n,
        doc_types=doc_types,
        boost_pricing=True,
        use_highlight=False,
    )


async def run_vector(qdrant, embedder, query: str, doc_types: list[str] | None, top_n: int = 30) -> list:
    """Run vector search."""
    vectors = await embedder.embed([query])
    return qdrant.search(vector=vectors[0], top_n=top_n, doc_types=doc_types)


async def main_async(args) -> int:
    settings = get_settings()
    opensearch = OpenSearchClient()
    qdrant = QdrantSearchClient()
    embedder = get_embedding_provider()
    reranker = get_reranker_provider()

    query = args.query or "can i buy more ips for my vps"
    print("=" * 70)
    print(f"DEBUG RETRIEVAL: {query!r}")
    print("=" * 70)

    # --- 1. Check if chunk with "Additional IPs" / "IP/month" exists in index ---
    print("\n--- 1. CHUNK EXISTENCE CHECK (OpenSearch, no doc_type filter) ---")
    for probe in ["Additional IPs", "Additional IPs for KVM", "IP/month", "$2/IP", "additional IPs KVM"]:
        chunks = await check_chunk_exists_in_opensearch(opensearch, probe)
        found = [c for c in chunks if "ip" in (c.chunk_text or "").lower() or "ip" in (c.source_url or "").lower()]
        print(f"\n  Probe: {probe!r} -> {len(chunks)} total, {len(found)} with 'ip' in text/url")
        for i, c in enumerate(chunks[:5], 1):
            has_ip = "IP" in (c.chunk_text or "") or "ip" in (c.source_url or "")
            print(f"    {i}. [{c.doc_type}] score={c.score:.3f} {c.chunk_id[:8]}...")
            print(f"       url={c.source_url or '?'}")
            print(f"       text={_trunc(c.chunk_text, 100)}")
            if "Additional IPs" in (c.chunk_text or "") or "$2" in (c.chunk_text or "") and "IP" in (c.chunk_text or ""):
                print(f"       *** POSSIBLE IP PRICING CHUNK ***")

    # --- 2. Normalizer output (query variants) ---
    print("\n--- 2. NORMALIZER OUTPUT ---")
    try:
        spec = await normalize(query)
        if spec:
            print(f"  keyword_queries: {getattr(spec, 'keyword_queries', [])}")
            print(f"  semantic_queries: {getattr(spec, 'semantic_queries', [])}")
            print(f"  retrieval_rewrites: {getattr(spec, 'rewrite_candidates', [])[:8]}")
            print(f"  doc_type_prior: {getattr(spec, 'doc_type_prior', [])}")
            print(f"  retrieval_profile: {getattr(spec, 'retrieval_profile', '?')}")
    except Exception as e:
        print(f"  Normalizer error: {e}")

    # --- 3. Retrieval plan ---
    print("\n--- 3. RETRIEVAL PLAN ---")
    try:
        plan, _ = await build_retrieval_plan_for_attempt(
            base_query=query,
            attempt=1,
            query_spec=await normalize(query) if args.normalize else None,
        )
        print(f"  query_keyword: {plan.query_keyword}")
        print(f"  query_semantic: {plan.query_semantic}")
        print(f"  authoritative_doc_types: {plan.authoritative_doc_types}")
        print(f"  supporting_doc_types: {plan.supporting_doc_types}")
        print(f"  fetch_n: {plan.fetch_n}, rerank_k: {plan.rerank_k}")
    except Exception as e:
        print(f"  Plan error: {e}")
        plan = None

    # --- 4. BM25 with different queries ---
    print("\n--- 4. BM25 SEARCH (authoritative doc_types: pricing, policy, howto, docs, tos) ---")
    auth_docs = ["pricing", "policy", "howto", "docs", "tos"]
    for q in [query, "additional IPs VPS", "extra IP VPS", "IP add-on KVM"]:
        chunks = await run_bm25(opensearch, q, auth_docs, top_n=15)
        ip_related = [c for c in chunks if "ip" in (c.chunk_text or "").lower() or "additional" in (c.chunk_text or "").lower()]
        print(f"\n  Query: {q!r}")
        print(f"    Results: {len(chunks)}, IP-related: {len(ip_related)}")
        for i, c in enumerate(chunks[:5], 1):
            print(f"    {i}. [{c.doc_type}] score={c.score:.3f} {_trunc(c.chunk_text, 80)}")
            if "Additional IPs" in (c.chunk_text or "") or ("$2" in (c.chunk_text or "") and "IP" in (c.chunk_text or "")):
                print(f"       *** IP PRICING CHUNK ***")

    # --- 5. Vector with same queries ---
    print("\n--- 5. VECTOR SEARCH (authoritative doc_types) ---")
    for q in [query, "additional IPs VPS"]:
        chunks = await run_vector(qdrant, embedder, q, auth_docs, top_n=15)
        print(f"\n  Query: {q!r} -> {len(chunks)} results")
        for i, c in enumerate(chunks[:5], 1):
            print(f"    {i}. [{c.doc_type}] score={c.score:.3f} {_trunc(c.chunk_text, 80)}")

    # --- 6. Full retrieval pipeline (if plan available) ---
    if plan:
        print("\n--- 6. FULL RETRIEVAL PIPELINE (RetrievalService.retrieve) ---")
        try:
            from app.services.retrieval import RetrievalService

            svc = RetrievalService()
            spec = await normalize(query) if args.normalize else None
            pack = await svc.retrieve(
                query=query,
                query_spec=spec,
                retrieval_plan=plan,
            )
            merged = pack.retrieval_stats.get("merged_count", 0)
            print(f"  Merged count: {merged}")
            print(f"  Reranked count: {len(pack.chunks)}")
            print(f"  Evidence chunks:")
            for i, c in enumerate(pack.chunks, 1):
                print(f"    {i}. [{c.doc_type}] score={c.score} {c.source_url or '?'}")
                print(f"       {_trunc(c.snippet or c.full_text or '', 100)}")
        except Exception as e:
            print(f"  Retrieval error: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    print("SUMMARY: If 'Additional IPs for KVM VPS: $2/IP/month' chunk is NOT in step 1,")
    print("  the page may not be ingested. If it appears in step 1 but not in 4/5/6,")
    print("  the query/doc_type filter or rerank is dropping it.")
    print("=" * 70)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug retrieval for IP add-on query")
    parser.add_argument("--query", type=str, default="can i buy more ips for my vps")
    parser.add_argument("--normalize", action="store_true", help="Use normalizer for plan (slower)")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
