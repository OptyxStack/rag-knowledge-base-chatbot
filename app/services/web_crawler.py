"""Crawl entire website from a seed URL for document ingestion."""

from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.services.url_fetcher import fetch_content_from_url

logger = get_logger(__name__)

# Default limits
DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_DEPTH = 3
DEFAULT_TIMEOUT = 15.0


def _normalize_url(url: str, base: str) -> str | None:
    """Resolve relative URL and return absolute URL, or None if invalid."""
    try:
        full = urljoin(base, url)
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            return None
        # Remove fragment
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized.rstrip("/") or f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        return None


def _same_domain(url: str, base_domain: str) -> bool:
    """Check if URL belongs to same domain (including subdomains)."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        base = base_domain.lower()
        return netloc == base or netloc.endswith("." + base)
    except Exception:
        return False


def _extract_links(html: str, base_url: str) -> set[str]:
    """Extract all internal links from HTML."""
    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = _normalize_url(href, base_url)
        if full and _same_domain(full, base_domain):
            links.add(full)
    return links


def crawl_website(
    seed_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """
    Crawl website starting from seed_url. Returns list of doc dicts for ingestion.
    Each doc: {url, title, content, raw_text, doc_type, ...}
    """
    if not seed_url or not seed_url.strip():
        raise ValueError("Seed URL is required")
    seed_url = seed_url.strip()
    if not seed_url.startswith(("http://", "https://")):
        seed_url = "https://" + seed_url

    base_domain = urlparse(seed_url).netloc
    seen: set[str] = set()
    docs: list[dict] = []
    queue: deque[tuple[str, int]] = deque([(seed_url, 0)])  # (url, depth)

    while queue and len(docs) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        if depth > max_depth:
            continue

        try:
            result = fetch_content_from_url(url, timeout=timeout)
        except Exception as e:
            logger.warning("web_crawler_fetch_failed", url=url, error=str(e))
            continue

        content = result.get("content", "").strip()
        if len(content) < 50:
            logger.debug("web_crawler_skip_minimal", url=url, len=len(content))
            continue

        title = result.get("title", "Untitled")
        doc_type = _doc_type_from_url(url)
        docs.append({
            "url": url,
            "source_url": url,
            "title": title,
            "content": content,
            "raw_text": content,
            "doc_type": doc_type,
            "metadata": {"crawl_depth": depth, "source": "web_crawl"},
            "source_file": "web_crawl",
        })

        # Discover new links only if within depth limit
        if depth < max_depth and len(docs) + len(seen) < max_pages * 2:
            raw_html = result.get("raw_html", "")
            if raw_html:
                for link in _extract_links(raw_html, url):
                    if link not in seen:
                        queue.append((link, depth + 1))

    logger.info("web_crawler_done", seed=seed_url, pages=len(docs), seen=len(seen))
    return docs


def _doc_type_from_url(url: str) -> str:
    """Infer doc_type from URL path."""
    url_lower = url.lower()
    if "terms" in url_lower or "tos" in url_lower:
        return "tos"
    if "privacy" in url_lower or "policy" in url_lower:
        return "policy"
    if "faq" in url_lower or "faqs" in url_lower:
        return "faq"
    if "docs" in url_lower or "documentation" in url_lower or "help" in url_lower:
        return "howto"
    if "vps" in url_lower or "billing" in url_lower or "store" in url_lower or "pricing" in url_lower:
        return "pricing"
    return "other"
