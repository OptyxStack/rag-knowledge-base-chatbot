"""Reverse-sync sample conversation changes back to source JSON files.

When conversations are created / updated / deleted via API, the corresponding
entry in the source JSON is kept in sync (like docs source_sync).

Supported format
----------------
- conversations: {"source": "whmcs", "conversations": [{"external_id", "subject", "description", ...}]}
- tickets: legacy key for backward compatibility
"""

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SAMPLE_CONVERSATIONS_FILE = "sample_conversations.json"

_SYNC_FIELDS = ("subject", "description", "status", "priority", "client_id", "email", "name")


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


def _find_entry_index(entries: list[dict], external_id: str) -> int:
    """Return list index of the entry whose external_id matches, or -1."""
    for i, entry in enumerate(entries):
        if str(entry.get("external_id", "")) == str(external_id):
            return i
    return -1


def sync_ticket_update(
    external_id: str,
    source_file: str | None,
    *,
    subject: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    client_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> bool:
    """Write changed fields back to the source JSON entry. Returns True on success."""
    source_file = source_file or SAMPLE_CONVERSATIONS_FILE
    # Map legacy tickets.json to sample_conversations.json
    if source_file == "tickets.json":
        source_file = SAMPLE_CONVERSATIONS_FILE
    if source_file != SAMPLE_CONVERSATIONS_FILE:
        logger.debug("ticket_sync_skip_unknown_format", source_file=source_file)
        return False

    path = _resolve_source_dir() / source_file
    if not path.exists():
        logger.debug("ticket_sync_file_missing", path=str(path))
        return False

    try:
        data = _read_json(path)
        entries = data.get("conversations", data.get("tickets", []))
        idx = _find_entry_index(entries, external_id)
        if idx < 0:
            logger.debug("ticket_sync_entry_not_found", external_id=external_id)
            return False

        changed = False
        for field in _SYNC_FIELDS:
            val = locals().get(field)
            if val is not None:
                entries[idx][field] = val
                changed = True

        if changed:
            data["conversations"] = entries
            _write_json(path, data)
            logger.info("ticket_sync_updated", source_file=source_file, external_id=external_id)
        return changed
    except Exception as e:
        logger.warning("ticket_sync_update_failed", source_file=source_file, error=str(e))
        return False


def sync_ticket_create(
    external_id: str,
    subject: str,
    description: str = "",
    status: str = "Open",
    priority: str | None = None,
    client_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> bool:
    """Append a new entry to sample_conversations.json. Returns True on success."""
    source_dir = _resolve_source_dir()
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / SAMPLE_CONVERSATIONS_FILE

    try:
        if path.exists():
            data = _read_json(path)
        else:
            data = {"source": "whmcs", "conversations": []}

        # Normalize: use "conversations" key (support legacy "tickets")
        if "conversations" not in data and "tickets" in data:
            data["conversations"] = data.pop("tickets")
        tickets = data.setdefault("conversations", [])

        # Upsert by external_id
        for t in tickets:
            if str(t.get("external_id", "")) == str(external_id):
                t["subject"] = subject
                t["description"] = description
                t["status"] = status
                if priority is not None:
                    t["priority"] = priority
                if client_id is not None:
                    t["client_id"] = client_id
                if email is not None:
                    t["email"] = email
                if name is not None:
                    t["name"] = name
                _write_json(path, data)
                logger.info("ticket_sync_upserted", source_file=SAMPLE_CONVERSATIONS_FILE, external_id=external_id)
                return True

        tickets.append({
            "external_id": external_id,
            "subject": subject,
            "description": description,
            "status": status,
            "priority": priority,
            "client_id": client_id,
            "email": email,
            "name": name,
        })
        _write_json(path, data)
        logger.info("ticket_sync_created", source_file=SAMPLE_CONVERSATIONS_FILE, external_id=external_id)
        return True
    except Exception as e:
        logger.warning("ticket_sync_create_failed", error=str(e))
        return False


def sync_ticket_delete(external_id: str, source_file: str | None) -> bool:
    """Remove the matching entry from the source JSON. Returns True on success."""
    source_file = source_file or SAMPLE_CONVERSATIONS_FILE
    if source_file == "tickets.json":
        source_file = SAMPLE_CONVERSATIONS_FILE
    if source_file != SAMPLE_CONVERSATIONS_FILE:
        return False

    path = _resolve_source_dir() / source_file
    if not path.exists():
        return False

    try:
        data = _read_json(path)
        entries = data.get("conversations", data.get("tickets", []))
        idx = _find_entry_index(entries, external_id)
        if idx < 0:
            return False

        removed = entries.pop(idx)
        data["conversations"] = entries
        _write_json(path, data)
        logger.info(
            "ticket_sync_deleted",
            source_file=source_file,
            external_id=removed.get("external_id", external_id),
        )
        return True
    except Exception as e:
        logger.warning("ticket_sync_delete_failed", source_file=source_file, error=str(e))
        return False
