"""Load documents from source JSON files for ingestion.

Supported formats:
- pages: {"pages": [{"url", "title", "text"}]}
- articles: {"articles": [{"url", "title", "snippet"}]}
- plans: {"plans": [{"plan_name", "price_raw", ...}]}
- sales_kb: {"datasets": {"sales_knowledge": {"product_categories": [...]}}}
"""

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings

def _doc_type_from_url(url: str) -> str:
    """Infer doc_type from URL using config-driven keyword mapping."""
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


def load_pages_json(path: Path, doc_type_override: str | None = None) -> list[dict[str, Any]]:
    """Load JSON with pages array: [{url, title, text}]. Used by sample_docs.json and {doc_type}.json."""
    with open(path) as f:
        data = json.load(f)
    docs = []
    for p in data.get("pages", []):
        url = p.get("url")
        text = p.get("text", "").strip()
        if not url or len(text) < 50:
            continue
        doc_type = doc_type_override or data.get("doc_type") or _doc_type_from_url(url)
        docs.append({
            "url": url,
            "title": p.get("title", "Untitled"),
            "raw_text": text,
            "doc_type": doc_type,
            "metadata": {"source": data.get("source", "")},
            "source_file": path.name,
        })
    return docs


def load_articles_json(path: Path) -> list[dict[str, Any]]:
    """Load JSON with articles array: [{url, title, snippet}]."""
    with open(path) as f:
        data = json.load(f)
    docs = []
    for a in data.get("articles", []):
        url = a.get("url")
        snippet = a.get("snippet", "").strip()
        if not url or len(snippet) < 50:
            continue
        docs.append({
            "url": url,
            "title": a.get("title", "Untitled"),
            "raw_text": snippet,
            "doc_type": _doc_type_from_url(url),
            "metadata": {"key_points": a.get("key_points", [])},
            "source_file": path.name,
        })
    return docs


def load_plans_json(path: Path) -> list[dict[str, Any]]:
    """Load JSON with plans array: [{plan_name, price_raw, ram, cpu, ...}]."""
    with open(path) as f:
        data = json.load(f)
    docs = []
    default_category = str(data.get("category") or "Plans").strip() or "Plans"
    for plan in data.get("plans", []):
        source_url = plan.get("source_url") or plan.get("order_link", "")
        plan_name = plan.get("plan_name", "unknown")
        url = f"{source_url}#plan-{plan_name}" if source_url else f"plan://{plan_name}"
        text_parts = [f"Plan: {plan_name}"]
        if plan.get("price_raw"):
            text_parts.append(f"Price: {plan['price_raw']}")
        if plan.get("billing_cycle"):
            text_parts.append(f"Billing: {plan['billing_cycle']}")
        for k in ("ram", "cpu", "storage", "bandwidth", "port", "os", "location"):
            if plan.get(k):
                text_parts.append(f"{k}: {plan[k]}")
        if plan.get("order_link"):
            text_parts.append(f"Order: {plan['order_link']}")
        text = "\n".join(text_parts)
        docs.append({
            "url": url,
            "title": f"Plan {plan_name}",
            "raw_text": text,
            "doc_type": "pricing",
            "metadata": {
                "product": plan_name,
                "category": str(plan.get("category") or default_category),
                "plan_name": plan_name,
                "price_raw": plan.get("price_raw"),
                "order_link": plan.get("order_link"),
            },
            "source_file": path.name,
        })
    return docs


def load_sales_kb_json(path: Path) -> list[dict[str, Any]]:
    """Load JSON with datasets.sales_knowledge.product_categories."""
    with open(path) as f:
        data = json.load(f)
    docs = []
    sales = data.get("datasets", {}).get("sales_knowledge", {})
    if not sales:
        return docs
    global_highlights = sales.get("global_highlights", [])
    for cat in sales.get("product_categories", []):
        url = cat.get("url", "")
        summary = cat.get("summary", "").strip()
        if not url or len(summary) < 30:
            continue
        plans_text = []
        for p in cat.get("plans", [])[:20]:
            plans_text.append(
                f"{p.get('plan_name', '')}: ${p.get('price_usd_month', 0)}/mo - "
                f"{p.get('memory', '')} RAM, {p.get('storage', '')} storage"
            )
        full_text = summary + "\n\nPlans:\n" + "\n".join(plans_text) if plans_text else summary
        docs.append({
            "url": url,
            "title": cat.get("title", cat.get("category", "Untitled")),
            "raw_text": full_text,
            "doc_type": "pricing",
            "metadata": {
                "product": cat.get("category"),
                "category": cat.get("category"),
                "global_highlights": global_highlights[:5],
            },
            "source_file": path.name,
        })
    return docs


def load_sample_conversations_json(path: Path) -> list[dict[str, Any]]:
    """Load sample_conversations.json and convert to document format for vector/RAG ingestion."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Support both "conversations" and "tickets" for backward compatibility
    entries = data.get("conversations", data.get("tickets", []))
    docs = []
    for t in entries:
        ticket_id = t.get("id") or t.get("external_id")
        if not ticket_id:
            continue
        url = f"ticket://{ticket_id}"
        subject = (t.get("subject") or "").strip().split("\n")[0][:200]
        parts = [f"Subject: {subject}"]
        if t.get("description"):
            parts.append(f"Content:\n{t['description']}")
        metadata = t.get("metadata") or {}
        replies = metadata.get("replies", [])
        staff_replies = [r for r in replies if r.get("role") == "staff" and r.get("content")]
        if staff_replies:
            parts.append("Staff replies:")
            for r in staff_replies[:5]:
                parts.append((r.get("content") or "").strip())
        text = "\n\n".join(parts)
        if len(text) < 50:
            continue
        docs.append({
            "url": url,
            "source_url": url,
            "title": subject or f"Sample conversation {ticket_id}",
            "raw_text": text,
            "content": text,
            "doc_type": "conversation",
            "metadata": {"conversation_id": str(ticket_id), "source": data.get("source", "")},
            "source_file": path.name,
        })
    return docs


LOADERS: dict[str, Any] = {
    "sample_docs.json": load_pages_json,
    "docs_full.json": load_pages_json,
    "docs_knowledge.json": load_articles_json,
    "plans_advanced.json": load_plans_json,
    "plans_additional.json": load_plans_json,
    "sales_kb.json": load_sales_kb_json,
    "custom_docs.json": load_pages_json,
    "pricing.json": load_pages_json,
    "other.json": load_pages_json,
    "howto.json": load_pages_json,
    "sample_conversations.json": load_sample_conversations_json,
    "tickets.json": load_sample_conversations_json,  # backward compat
}


def load_all_docs(source_dir: Path, files: list[str] | None = None) -> list[dict[str, Any]]:
    """Load docs from all JSON files in source_dir. Includes {doc_type}.json (policy.json, faq.json, etc.)."""
    all_docs = []
    seen_urls: set[str] = set()
    known = set(LOADERS.keys())

    for fname, loader in LOADERS.items():
        if files and fname not in files:
            continue
        path = source_dir / fname
        if not path.exists():
            continue
        try:
            docs = loader(path)
            for d in docs:
                url = d.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_docs.append(d)
        except Exception:
            pass

    # Load doc_type files (policy.json, faq.json, howto.json, etc.) not in LOADERS
    if source_dir.exists():
        for path in source_dir.glob("*.json"):
            if path.name in known:
                continue
            if files and path.name not in files:
                continue
            try:
                doc_type = path.stem.lower().replace(" ", "_")
                docs = load_pages_json(path, doc_type_override=doc_type)
                for d in docs:
                    url = d.get("url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_docs.append(d)
            except Exception:
                pass

    return all_docs
