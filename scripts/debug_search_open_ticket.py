#!/usr/bin/env python3
"""Search index for chunks about open ticket / submit ticket."""

import asyncio
import sys

sys.path.insert(0, ".")

from app.search.opensearch_client import OpenSearchClient


def _safe(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    s = s.encode("ascii", errors="replace").decode("ascii")
    return (s[:n] + "...") if len(s) > n else s


async def main():
    opensearch = OpenSearchClient()

    queries = [
        "open ticket",
        "submit ticket",
        "support ticket",
        "client area ticket",
        "billing panel submit ticket",
    ]

    print("=" * 70)
    print("SEARCH: chunks about open/submit ticket")
    print("=" * 70)

    for q in queries:
        chunks = await opensearch.search(
            query=q,
            top_n=15,
            doc_types=None,
            boost_pricing=False,
            use_highlight=False,
            prefer_snippet=False,
        )
        print(f"\n--- Query: {q!r} ({len(chunks)} results) ---")
        for i, c in enumerate(chunks[:10], 1):
            text = (c.chunk_text or "")[:250]
            print(f"  {i}. [{c.doc_type}] score={c.score:.3f} | {c.source_url or '?'}")
            print(f"     {_safe(text)}")
            if "ticket" in text.lower() or "client area" in text.lower() or "billing" in text.lower():
                print(f"     *** RELEVANT ***")


if __name__ == "__main__":
    asyncio.run(main())
