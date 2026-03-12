"""Crawl entire website from a seed URL for document ingestion."""

from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.url_fetcher import fetch_content_from_url

logger = get_logger(__name__)

# Default limits
DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_DEPTH = 3
DEFAULT_TIMEOUT = 15.0
_PRODUCT_FAMILY_KEYS = ("windows_vps", "kvm_vps", "macos_vps", "dedicated")


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


def _url_matches_exclude(url: str, exclude_prefixes: list[str]) -> bool:
    """Return True if url starts with any exclude prefix."""
    if not exclude_prefixes:
        return False
    url_lower = url.lower()
    for prefix in exclude_prefixes:
        p = (prefix or "").strip().lower()
        if p and url_lower.startswith(p):
            return True
    return False


def _infer_page_kind(*, url: str, doc_type: str, title: str = "", text: str = "") -> str:
    dt = (doc_type or "").strip().lower()
    if dt == "conversation" or url.startswith("ticket://"):
        return "conversation"
    if dt == "faq":
        return "faq"
    if dt in {"howto", "docs"}:
        return "howto"
    if dt in {"policy", "tos"}:
        return "policy"
    if dt == "blog":
        return "blog"

    blob = f"{url} {title} {text}".lower()
    if any(token in blob for token in ("/order", "checkout", "cart", "buy now", "purchase")):
        return "order_page"
    if dt == "pricing" or any(token in blob for token in ("pricing", "plans", "price", "/mo")):
        return "pricing_table"
    if any(token in blob for token in ("vps", "server", "dedicated", "product")):
        return "product_page"
    return "blog"


def _normalize_product_family(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    aliases = {
        "windows": "windows_vps",
        "windows_vps": "windows_vps",
        "windows-rdp": "windows_vps",
        "rdp": "windows_vps",
        "kvm": "kvm_vps",
        "kvm_vps": "kvm_vps",
        "linux_vps": "kvm_vps",
        "linux": "kvm_vps",
        "macos": "macos_vps",
        "mac": "macos_vps",
        "macos_vps": "macos_vps",
        "dedicated": "dedicated",
        "dedicated_server": "dedicated",
        "dedicated_servers": "dedicated",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in _PRODUCT_FAMILY_KEYS else None


def _infer_product_family(*, url: str, title: str = "", text: str = "") -> str | None:
    blob = f"{url} {title} {text}".lower()
    if ("windows" in blob or "rdp" in blob) and "vps" in blob:
        return "windows_vps"
    if "kvm" in blob and "vps" in blob:
        return "kvm_vps"
    if ("macos" in blob or "mac os" in blob or "apple" in blob) and "vps" in blob:
        return "macos_vps"
    if "dedicated" in blob:
        return "dedicated"
    return None


def _build_taxonomy_metadata(*, url: str, title: str, text: str, doc_type: str) -> dict:
    page_kind = _infer_page_kind(url=url, doc_type=doc_type, title=title, text=text)
    product_family = _normalize_product_family(_infer_product_family(url=url, title=title, text=text))
    metadata = {"page_kind": page_kind}
    if product_family:
        metadata["product_family"] = product_family
    return metadata


def crawl_website(
    seed_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    timeout: float = DEFAULT_TIMEOUT,
    exclude_prefixes: list[str] | None = None,
) -> list[dict]:
    """
    Crawl website starting from seed_url. Returns list of doc dicts for ingestion.
    Each doc: {url, title, content, raw_text, doc_type, ...}
    exclude_prefixes: URLs starting with any of these prefixes will not be crawled.
    """
    if not seed_url or not seed_url.strip():
        raise ValueError("Seed URL is required")
    seed_url = seed_url.strip()
    if not seed_url.startswith(("http://", "https://")):
        seed_url = "https://" + seed_url

    exclude = [p.strip() for p in (exclude_prefixes or []) if p and p.strip()]

    base_domain = urlparse(seed_url).netloc
    seen: set[str] = set()
    docs: list[dict] = []
    queue: deque[tuple[str, int]] = deque([(seed_url, 0)])  # (url, depth)

    while queue and len(docs) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        if _url_matches_exclude(url, exclude):
            logger.debug("web_crawler_skip_excluded", url=url[:80])
            continue

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
        taxonomy_meta = _build_taxonomy_metadata(
            url=url,
            title=title,
            text=content,
            doc_type=doc_type,
        )
        docs.append({
            "url": url,
            "source_url": url,
            "title": title,
            "content": content,
            "raw_text": content,
            "doc_type": doc_type,
            "metadata": {
                "crawl_depth": depth,
                "source": "web_crawl",
                **taxonomy_meta,
            },
            "source_file": "web_crawl",
        })

        # Discover new links only if within depth limit
        if depth < max_depth and len(docs) + len(seen) < max_pages * 2:
            raw_html = result.get("raw_html", "")
            if raw_html:
                for link in _extract_links(raw_html, url):
                    if link not in seen and not _url_matches_exclude(link, exclude):
                        queue.append((link, depth + 1))

    logger.info("web_crawler_done", seed=seed_url, pages=len(docs), seen=len(seen))
    return docs


def _doc_type_from_url(url: str) -> str:
    """Infer doc_type from URL path using config-driven keyword mapping."""
    url_lower = url.lower()
    mapping = get_settings().doc_type_url_keywords or {}
    for doc_type, keywords in mapping.items():
        dt = str(doc_type).strip().lower()
        if not dt:
            continue
        for kw in (keywords or []):
            token = str(kw).strip().lower()
            if token and token in url_lower:
                return dt
    return "other"
