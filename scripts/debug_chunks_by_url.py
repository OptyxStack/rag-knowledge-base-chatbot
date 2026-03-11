#!/usr/bin/env python3
"""Debug: List all chunks for a given source URL. Check if page is fully ingested.

Usage:
    python scripts/debug_chunks_by_url.py
    python scripts/debug_chunks_by_url.py --url "https://greencloudvps.com/terms-of-service.php"
"""

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from app.core.config import get_settings
from app.search.opensearch_client import OpenSearchClient


def _safe(s: str, n: int = 200) -> str:
    """ASCII-safe truncate for console."""
    s = (s or "").strip()
    s = s.encode("ascii", errors="replace").decode("ascii")
    return (s[:n] + "...") if len(s) > n else s


async def get_chunks_by_url(opensearch: OpenSearchClient, url_pattern: str) -> list[dict]:
    """Fetch chunks where source_url matches pattern (wildcard or exact)."""
    client = await opensearch._get_client()
    index_name = opensearch._settings.opensearch_index

    # Use wildcard to match URL (e.g. *terms-of-service*)
    body = {
        "size": 100,
        "query": {
            "wildcard": {"source_url": {"value": f"*{url_pattern}*", "case_insensitive": True}}
        },
        "_source": ["chunk_id", "document_id", "chunk_text", "source_url", "doc_type", "title"],
        "sort": [{"chunk_id": "asc"}],
    }

    try:
        if opensearch._is_async():
            resp = await client.search(index=index_name, body=body)
        else:
            resp = await asyncio.to_thread(client.search, index=index_name, body=body)
    except Exception as e:
        print(f"Search error: {e}")
        return []

    hits = resp.get("hits", {}).get("hits", [])
    return [hit.get("_source", {}) for hit in hits]


async def main_async(args) -> int:
    settings = get_settings()
    opensearch = OpenSearchClient()

    url = args.url or "terms-of-service"
    print("=" * 70)
    print(f"DEBUG CHUNKS BY URL: {url!r}")
    print("=" * 70)

    chunks = await get_chunks_by_url(opensearch, url)
    print(f"\nTotal chunks found: {len(chunks)}")

    if not chunks:
        print("\nNo chunks found. The page may not be ingested.")
        print("  Check: 1) Crawl the URL  2) Ingest from source  3) Verify index name")
        return 0

    # Key phrases from ToS page (from web content)
    key_phrases = [
        "Additional IPs for KVM VPS",
        "$2/IP/month",
        "IP Address Change",
        "$2 one-time fee",
        "Additional services",
        "Money Back Guarantee",
        "Billing terms",
    ]

    print("\n--- Chunk list ---")
    found_phrases: set[str] = set()
    for i, c in enumerate(chunks, 1):
        text = (c.get("chunk_text") or "")
        url_val = c.get("source_url") or "?"
        doc_type = c.get("doc_type") or "?"
        chunk_id = (c.get("chunk_id") or "?")[:12]
        print(f"\n  [{i}] chunk_id={chunk_id}... doc_type={doc_type}")
        print(f"      source_url={url_val}")
        print(f"      text_len={len(text)} chars")
        print(f"      preview={_safe(text, 150)}")
        for phrase in key_phrases:
            if phrase in text:
                found_phrases.add(phrase)
                print(f"      *** CONTAINS: {phrase!r} ***")

    print("\n--- Coverage check ---")
    print("Key phrases from ToS page:")
    for p in key_phrases:
        status = "FOUND" if p in found_phrases else "MISSING"
        print(f"  {status}: {p!r}")

    if "Additional IPs for KVM VPS" not in found_phrases:
        print("\n*** WARNING: 'Additional IPs for KVM VPS' NOT in any chunk! ***")
        print("   The IP add-on pricing may be in a chunk that was not retrieved,")
        print("   or the page was chunked in a way that split/omitted that section.")

    print("\n" + "=" * 70)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug chunks for a source URL")
    parser.add_argument(
        "--url",
        type=str,
        default="terms-of-service",
        help="URL pattern to match (e.g. terms-of-service, budget-kvm-vps)",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
