#!/usr/bin/env python3
"""Debug normalizer LLM failure – trace why queries like "hello dumb" trigger llm_fallback.

Usage:
    python scripts/debug_normalizer.py
    python scripts/debug_normalizer.py --query "hello dumb"
"""

import argparse
import asyncio
import json
import sys
import traceback

sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task
from app.services.normalizer import (
    NORMALIZER_SYSTEM_PROMPT,
    _extract_probable_json,
    normalize,
    _normalize_llm,
)

logger = get_logger(__name__)


async def debug_llm_raw(query: str) -> None:
    """Call LLM directly and print raw response + parse attempt."""
    model = get_model_for_task("normalizer")
    llm = get_llm_gateway()

    user_content = f"Query: {query.strip()}"
    messages = [
        {"role": "system", "content": NORMALIZER_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    print(f"\n--- LLM call ---")
    print(f"Model: {model}")
    print(f"Query: {query!r}")

    try:
        resp = await llm.chat(
            messages=messages,
            temperature=0.0,
            model=model,
            max_tokens=512,
        )
        raw = (resp.content or "").strip()
        print(f"\n--- Raw response (len={len(raw)}) ---")
        print(raw[:2000] + ("..." if len(raw) > 2000 else ""))

        extracted = _extract_probable_json(raw)
        print(f"\n--- Extracted JSON (len={len(extracted)}) ---")
        print(extracted[:1500] + ("..." if len(extracted) > 1500 else ""))

        payload = json.loads(extracted)
        if not isinstance(payload, dict):
            print(f"\n!!! Parse OK but payload is not dict: type={type(payload)}")
        else:
            print(f"\n--- Parse OK ---")
            print(f"skip_retrieval: {payload.get('skip_retrieval')}")
            print(f"canonical_query_en: {payload.get('canonical_query_en')}")
    except json.JSONDecodeError as e:
        print(f"\n!!! JSONDecodeError: {e}")
        traceback.print_exc()
    except Exception as e:
        print(f"\n!!! Exception: {type(e).__name__}: {e}")
        traceback.print_exc()


async def debug_normalize(query: str) -> None:
    """Call normalize() and report result + any internal failure."""
    print(f"\n--- normalize({query!r}) ---")
    try:
        spec = await normalize(query)
        print(f"extraction_mode: {spec.extraction_mode}")
        print(f"skip_retrieval: {spec.skip_retrieval}")
        print(f"canned_response: {spec.canned_response}")
        print(f"intent: {spec.intent}")
    except Exception as e:
        print(f"!!! Exception: {type(e).__name__}: {e}")
        traceback.print_exc()


async def debug_normalize_llm_direct(query: str) -> None:
    """Call _normalize_llm directly (bypasses fallback) to see if it returns None."""
    print(f"\n--- _normalize_llm({query!r}) direct ---")
    try:
        spec = await _normalize_llm(query, None, source_lang=None, locale=None)
        if spec is None:
            print("!!! _normalize_llm returned None (LLM failed internally)")
        else:
            print(f"OK: extraction_mode={spec.extraction_mode}, skip_retrieval={spec.skip_retrieval}")
    except Exception as e:
        print(f"!!! Exception: {type(e).__name__}: {e}")
        traceback.print_exc()


async def main():
    parser = argparse.ArgumentParser(description="Debug normalizer failure")
    parser.add_argument("--query", default="hello dumb", help="Query to test")
    parser.add_argument("--raw-only", action="store_true", help="Only run raw LLM call")
    args = parser.parse_args()

    print("=" * 60)
    print("Normalizer debug")
    print("=" * 60)
    print(f"Query: {args.query!r}")

    if args.raw_only:
        await debug_llm_raw(args.query)
    else:
        await debug_llm_raw(args.query)
        await debug_normalize_llm_direct(args.query)
        await debug_normalize(args.query)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
