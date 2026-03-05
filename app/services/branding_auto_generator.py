"""Auto-generate branding config (persona, domain, custom rules) from website URL using AI."""

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

from app.core.logging import get_logger
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task
from app.services.url_fetcher import fetch_content_from_url

logger = get_logger(__name__)

MAX_CONTENT_CHARS = 12_000  # Truncate to avoid token limits
EXTRA_PATHS = ("/about", "/pricing", "/terms", "/support", "/help")


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("URL is required")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError("Invalid URL")
    return url


def _fetch_page(base_url: str, path: str) -> dict | None:
    """Fetch a single page. Sync, run in thread."""
    try:
        full_url = urljoin(base_url, path)
        return fetch_content_from_url(full_url, timeout=25.0)
    except Exception as e:
        logger.debug("branding_fetch_extra_failed", url=path, error=str(e))
        return None


async def _fetch_website_content(url: str) -> str:
    """Fetch homepage + optional extra pages, return combined text."""
    url = _normalize_url(url)
    results: list[dict] = []

    # Homepage (required) - use longer timeout for slow sites (e.g. greencloud.vn)
    try:
        home = await asyncio.to_thread(fetch_content_from_url, url, 45.0)
        results.append({"url": url, "title": home.get("title", ""), "content": home.get("content", "")})
    except Exception as e:
        logger.warning("branding_fetch_home_failed", url=url, error=str(e))
        raise ValueError(f"Cannot fetch URL: {e}") from e

    # Extra pages (optional)
    base = url.rstrip("/")
    for path in EXTRA_PATHS:
        try:
            extra = await asyncio.to_thread(_fetch_page, base, path)
            if extra and extra.get("content"):
                results.append({
                    "url": urljoin(base, path),
                    "title": extra.get("title", ""),
                    "content": extra.get("content", ""),
                })
        except Exception:
            pass

    # Combine and truncate
    parts: list[str] = []
    total = 0
    for r in results:
        title = r.get("title", "")
        content = (r.get("content") or "").strip()
        if not content:
            continue
        block = f"--- Page: {title} ---\n{content}"
        if total + len(block) > MAX_CONTENT_CHARS:
            remain = MAX_CONTENT_CHARS - total - 100
            if remain > 0:
                block = block[:remain] + "\n[...truncated]"
            parts.append(block)
            break
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts) if parts else ""


SYSTEM_PROMPT = """You are an analyst. Given website content, extract branding info for a RAG support chatbot.

Output valid JSON only, no markdown. Keys:
- persona: 1-2 sentence intro for the system prompt (e.g. "You are a friendly support assistant for X company, helping with products and policies.")
- prompt_domain: one of "support" | "legal" | "generic" (support=plans/pricing/escalation, legal=policy/terms, generic=minimal)
- custom_prompt_rules: optional 2-5 bullet points for domain-specific rules. Empty string if none needed.
- app_name: company/product name for branding (short, e.g. "GreenCloud")"""


async def generate_branding_from_domain(url: str) -> dict:
    """
    Fetch website content, analyze with LLM, return generated branding config.
    Returns: {persona, prompt_domain, custom_prompt_rules, app_name}
    """
    content = await _fetch_website_content(url)
    if not content or len(content) < 50:
        raise ValueError("Website content too short or empty")

    gateway = get_llm_gateway()
    model = get_model_for_task("branding_auto_generator") or get_model_for_task("normalizer")

    user_msg = f"""Analyze this website content and extract branding config for a support chatbot.

URL: {_normalize_url(url)}

CONTENT:
{content}

Return JSON:
{{"persona": "...", "prompt_domain": "support|legal|generic", "custom_prompt_rules": "...", "app_name": "..."}}"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    response = await gateway.chat(messages, temperature=0.2, model=model)
    raw = (response.content or "").strip()

    # Extract JSON (handle markdown code blocks)
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        raise ValueError("LLM did not return valid JSON")

    data = json.loads(json_match.group())

    persona = (data.get("persona") or "").strip()
    if not persona:
        persona = "You are a support assistant."

    domain = (data.get("prompt_domain") or "support").strip().lower()
    if domain not in ("support", "legal", "generic"):
        domain = "support"

    custom = (data.get("custom_prompt_rules") or "").strip()
    app_name = (data.get("app_name") or "").strip()

    return {
        "persona": persona,
        "prompt_domain": domain,
        "custom_prompt_rules": custom,
        "app_name": app_name,
    }
