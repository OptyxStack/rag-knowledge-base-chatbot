"""Load sample conversations from source JSON files (WHMCS crawl format).

Supported format:
- conversations: {"source": "whmcs", "conversations": [{"external_id", "subject", "description", ...}]}
- tickets: legacy key for backward compatibility
"""

import json
from pathlib import Path
from typing import Any


def load_sample_conversations_json(path: Path) -> list[dict[str, Any]]:
    """Load JSON with conversations array. Used by sample_conversations.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("conversations", data.get("tickets", []))
    tickets = []
    for t in entries:
        external_id = t.get("external_id")
        if not external_id:
            continue
        tickets.append({
            "external_id": str(external_id),
            "subject": t.get("subject", "Untitled"),
            "description": t.get("description", ""),
            "status": t.get("status", "Open"),
            "priority": t.get("priority"),
            "client_id": str(t["client_id"]) if t.get("client_id") is not None else None,
            "email": t.get("email"),
            "name": t.get("name"),
            "metadata": {
                "source": data.get("source", ""),
                **{k: v for k, v in t.items() if k not in (
                    "external_id", "subject", "description", "status", "priority",
                    "client_id", "email", "name"
                )},
            },
            "source_file": path.name,
        })
    return tickets


LOADERS: dict[str, Any] = {
    "sample_conversations.json": load_sample_conversations_json,
    "tickets.json": load_sample_conversations_json,  # backward compat
}


def load_all_tickets(source_dir: Path, files: list[str] | None = None) -> list[dict[str, Any]]:
    """Load tickets from all JSON files in source_dir."""
    all_tickets = []
    seen_ids: set[str] = set()
    for fname, loader in LOADERS.items():
        if files and fname not in files:
            continue
        path = source_dir / fname
        if not path.exists():
            continue
        try:
            items = loader(path)
            for t in items:
                eid = t.get("external_id")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    all_tickets.append(t)
        except Exception:
            pass
    return all_tickets
