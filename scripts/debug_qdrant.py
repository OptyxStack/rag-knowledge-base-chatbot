#!/usr/bin/env python3
"""Debug Qdrant: connection, collection stats, doc_type distribution, sample search.

Usage:
    python scripts/debug_qdrant.py
    python scripts/debug_qdrant.py --search "refund policy"
    python scripts/debug_qdrant.py --search "refund" --filter-doc-types policy,tos
"""

import argparse
import asyncio
import sys
from collections import Counter

# Add project root to path
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.search.embeddings import get_embedding_provider
from app.search.qdrant_client import QdrantSearchClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug Qdrant connection and data")
    parser.add_argument("--search", type=str, help="Run vector search with this query")
    parser.add_argument(
        "--filter-doc-types",
        type=str,
        help="Comma-separated doc_types to filter (e.g. policy,tos)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Search result limit")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Qdrant: {settings.qdrant_host}:{settings.qdrant_port}")
    print(f"Collection: {settings.qdrant_collection}")
    print("-" * 50)

    qdrant = QdrantSearchClient()
    client = qdrant._get_client()

    # 1. List collections
    try:
        # Check server version (query_points requires Qdrant 1.9+)
        try:
            version = client.get_version()
            print(f"Qdrant server version: {getattr(version, 'version', version)}")
        except Exception:
            pass
        collections = client.get_collections().collections
        print(f"Collections: {[c.name for c in collections]}")
        if not any(c.name == settings.qdrant_collection for c in collections):
            print(f"ERROR: Collection '{settings.qdrant_collection}' not found!")
            return 1
    except Exception as e:
        print(f"ERROR connecting to Qdrant: {e}")
        return 1

    # 2. Collection info
    try:
        info = client.get_collection(settings.qdrant_collection)
        print(f"Collection status: {getattr(info, 'status', '?')}")
        for attr in ("vectors_count", "points_count", "indexed_vector_count"):
            if hasattr(info, attr):
                print(f"{attr}: {getattr(info, attr)}")
        try:
            cnt = client.count(settings.qdrant_collection, exact=True)
            print(f"Points (count API): {getattr(cnt, 'count', cnt)}")
        except Exception:
            pass
    except Exception as e:
        print(f"ERROR getting collection info: {e}")
        return 1

    # 3. Doc_type distribution (scroll sample)
    try:
        doc_types: list[str] = []
        offset = None
        for _ in range(20):  # Max 20 pages
            result, offset = client.scroll(
                collection_name=settings.qdrant_collection,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in result:
                dt = (p.payload or {}).get("doc_type", "?")
                doc_types.append(dt)
            if offset is None or len(result) < 100:
                break

        dist = Counter(doc_types)
        print(f"\nDoc_type distribution (sampled {len(doc_types)} points):")
        for dt, cnt in dist.most_common():
            print(f"  {dt}: {cnt}")
    except Exception as e:
        print(f"WARNING: Could not sample doc_types: {e}")

    # 4. Sample search (optional) - use QdrantSearchClient.search() (same as app)
    if args.search:
        print(f"\n--- Vector search: '{args.search}' ---")
        filter_doc_types = None
        if args.filter_doc_types:
            filter_doc_types = [t.strip() for t in args.filter_doc_types.split(",") if t.strip()]
            print(f"Filter doc_types: {filter_doc_types}")

        async def run_search():
            embedder = get_embedding_provider()
            vectors = await embedder.embed([args.search])
            return vectors[0]

        vector = asyncio.run(run_search())
        chunks = qdrant.search(
            vector=vector,
            top_n=args.limit,
            doc_types=filter_doc_types,
        )

        print(f"Results: {len(chunks)}")
        for i, c in enumerate(chunks[:5], 1):
            print(f"  {i}. doc_type={c.doc_type} score={c.score:.3f} id={c.chunk_id}")
            text = (c.chunk_text or "")[:80]
            print(f"     {text}...")
    else:
        print("\nTip: Run with --search 'refund policy' to test vector search")
        print("     Add --filter-doc-types policy,tos to simulate Attempt 2 retry")

    print("\n--- Summary ---")
    print("If Vector search returns 0 with 404: Qdrant server may be too old.")
    print("  query_points API requires Qdrant 1.10+.")
    print("  Fix: docker compose pull qdrant && docker compose up -d qdrant")

    return 0


if __name__ == "__main__":
    sys.exit(main())
