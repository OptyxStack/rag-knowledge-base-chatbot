"""Reverse-sync document changes back to source JSON files.

When documents are created / updated / deleted via the admin API the
corresponding entry in the source JSON is kept in sync so that a future
``ingest-from-source`` does not overwrite manual edits.

Documents are organized by doc_type: each type goes to ``{doc_type}.json``
(e.g. policy.json, faq.json, howto.json). This enables retrieval to target
specific doc type files semantically.

Supported formats
-----------------
- **pages**   : ``{"pages": [{"url", "title", "text"}]}``
- **articles**: ``{"articles": [{"url", "title", "snippet"}]}``
- **plans**   : ``{"plans": [{"plan_name", …}]}``  (title-only sync)
- ``{doc_type}.json``: pages format for custom docs by type
"""

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

CUSTOM_DOCS_FILE = "custom_docs.json"


def doc_type_source_file(doc_type: str) -> str:
    """Return source file name for doc_type (e.g. policy.json, faq.json)."""
    key = (doc_type or "other").strip().lower().replace(" ", "_")
    return f"{key}.json" if key else "other.json"


_FILE_FORMATS: dict[str, dict[str, Any]] = {
    "sample_docs.json":                    {"list_key": "pages",    "url_field": "url", "title_field": "title", "text_field": "text"},
    "docs_full.json":                      {"list_key": "pages",    "url_field": "url", "title_field": "title", "text_field": "text"},
    "docs_knowledge.json":                 {"list_key": "articles", "url_field": "url", "title_field": "title", "text_field": "snippet"},
    "plans_advanced.json":                 {"list_key": "plans",    "url_field": None,  "title_field": "plan_name", "text_field": None},
    "plans_additional.json":               {"list_key": "plans",    "url_field": None,  "title_field": "plan_name", "text_field": None},
    "pricing.json":                        {"list_key": "pages",    "url_field": "url", "title_field": "title", "text_field": "text"},
    "other.json":                          {"list_key": "pages",    "url_field": "url", "title_field": "title", "text_field": "text"},
    "howto.json":                          {"list_key": "pages",    "url_field": "url", "title_field": "title", "text_field": "text"},
    CUSTOM_DOCS_FILE:                      {"list_key": "pages",    "url_field": "url", "title_field": "title", "text_field": "text"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_source_dir() -> Path:
    for base in (Path("/app"), Path.cwd()):
        candidate = base / "source"
        if candidate.exists():
            return candidate
    return Path.cwd() / "source"


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _find_entry_index(entries: list[dict], source_url: str, url_field: str | None) -> int:
    """Return list index of the entry whose URL matches, or -1."""
    if url_field:
        for i, entry in enumerate(entries):
            if entry.get(url_field) == source_url:
                return i
        return -1

    # Plans: URL is ``{source_url}#plan-{plan_name}`` or ``plan://{plan_name}``
    plan_name = ""
    if "#plan-" in source_url:
        plan_name = source_url.split("#plan-", 1)[1]
    elif source_url.startswith("plan://"):
        plan_name = source_url[len("plan://"):]
    if plan_name:
        for i, entry in enumerate(entries):
            if entry.get("plan_name", "").lower() == plan_name.lower():
                return i
    return -1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _get_format(source_file: str) -> dict[str, Any] | None:
    """Return format config for source file. Doc-type files use pages format."""
    fmt = _FILE_FORMATS.get(source_file)
    if fmt:
        return fmt
    if source_file.endswith(".json"):
        return {"list_key": "pages", "url_field": "url", "title_field": "title", "text_field": "text"}
    return None


def sync_document_update(
    source_url: str,
    source_file: str | None,
    *,
    title: str | None = None,
    cleaned_content: str | None = None,
) -> bool:
    """Write changed fields back to the source JSON entry.  Returns True on success."""
    source_file = source_file or CUSTOM_DOCS_FILE
    fmt = _get_format(source_file)
    if not fmt:
        logger.debug("source_sync_skip_unknown_format", source_file=source_file)
        return False

    path = _resolve_source_dir() / source_file
    if not path.exists():
        logger.debug("source_sync_file_missing", path=str(path))
        return False

    try:
        data = _read_json(path)
        entries = data.get(fmt["list_key"], [])
        idx = _find_entry_index(entries, source_url, fmt["url_field"])
        if idx < 0:
            logger.debug("source_sync_entry_not_found", source_url=source_url[:80])
            return False

        changed = False
        if title is not None and fmt["title_field"]:
            entries[idx][fmt["title_field"]] = title
            changed = True
        if cleaned_content is not None and fmt["text_field"]:
            entries[idx][fmt["text_field"]] = cleaned_content
            changed = True

        if changed:
            data[fmt["list_key"]] = entries
            _write_json(path, data)
            logger.info("source_sync_updated", source_file=source_file, url=source_url[:80])
        return changed
    except Exception as e:
        logger.warning("source_sync_update_failed", source_file=source_file, error=str(e))
        return False


def sync_document_create(
    source_url: str,
    title: str,
    content: str,
    doc_type: str = "other",
) -> bool:
    """Append a new entry to ``{doc_type}.json``. Returns True on success."""
    source_file = doc_type_source_file(doc_type)
    source_dir = _resolve_source_dir()
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / source_file

    try:
        if path.exists():
            data = _read_json(path)
        else:
            data = {"source": "admin_panel", "doc_type": doc_type, "pages": []}

        pages: list[dict] = data.setdefault("pages", [])

        # Upsert by URL
        for p in pages:
            if p.get("url") == source_url:
                p["title"] = title
                p["text"] = content
                _write_json(path, data)
                logger.info("source_sync_upserted", source_file=source_file, url=source_url[:80])
                return True

        pages.append({"url": source_url, "title": title, "text": content})
        _write_json(path, data)
        logger.info("source_sync_created", source_file=source_file, url=source_url[:80])
        return True
    except Exception as e:
        logger.warning("source_sync_create_failed", error=str(e))
        return False


def sync_document_delete(source_url: str, source_file: str | None) -> bool:
    """Remove the matching entry from the source JSON.  Returns True on success."""
    source_file = source_file or CUSTOM_DOCS_FILE
    fmt = _get_format(source_file)
    if not fmt:
        return False

    path = _resolve_source_dir() / source_file
    if not path.exists():
        return False

    try:
        data = _read_json(path)
        entries = data.get(fmt["list_key"], [])
        idx = _find_entry_index(entries, source_url, fmt["url_field"])
        if idx < 0:
            return False

        removed = entries.pop(idx)
        data[fmt["list_key"]] = entries
        _write_json(path, data)
        logger.info(
            "source_sync_deleted",
            source_file=source_file,
            url=(removed.get(fmt["url_field"] or "") or source_url)[:80],
        )
        return True
    except Exception as e:
        logger.warning("source_sync_delete_failed", source_file=source_file, error=str(e))
        return False
