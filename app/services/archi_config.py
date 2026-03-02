"""Archi v3 config from DB (app_config) with env fallback.

Keys: language_detect_enabled, decision_router_use_llm, evidence_evaluator_enabled,
      self_critic_enabled, final_polish_enabled.
Cache refreshed on startup and when admin updates config.
"""

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import AppConfig

logger = get_logger(__name__)

CONFIG_KEYS = (
    "language_detect_enabled",
    "decision_router_use_llm",
    "evidence_evaluator_enabled",
    "self_critic_enabled",
    "final_polish_enabled",
)
_cache: dict[str, Any] = {}
CACHE_TTL_SECONDS = 60


def _parse_bool(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    return value.strip().lower() in ("true", "1", "yes")


async def _load_from_db(session: AsyncSession) -> dict[str, str]:
    """Load archi config from app_config."""
    result: dict[str, str] = {}
    try:
        rows = await session.execute(
            select(AppConfig.key, AppConfig.value).where(AppConfig.key.in_(CONFIG_KEYS))
        )
        for key, value in rows.all():
            if value is not None:
                result[key] = value
    except Exception as e:
        logger.warning("archi_config_load_failed", error=str(e))
    return result


async def refresh_cache(session: AsyncSession) -> None:
    """Load archi config from DB and update in-memory cache."""
    db_values = await _load_from_db(session)
    settings = get_settings()
    for key in CONFIG_KEYS:
        if key in db_values:
            _cache[key] = _parse_bool(db_values[key])
        else:
            _cache[key] = getattr(settings, key, False)
    _cache["updated_at"] = time.monotonic()
    logger.info("archi_config_cache_refreshed", **{k: _cache.get(k) for k in CONFIG_KEYS})


def get_language_detect_enabled() -> bool:
    if "language_detect_enabled" in _cache:
        return _cache["language_detect_enabled"]
    return getattr(get_settings(), "language_detect_enabled", True)


def get_decision_router_use_llm() -> bool:
    if "decision_router_use_llm" in _cache:
        return _cache["decision_router_use_llm"]
    return getattr(get_settings(), "decision_router_use_llm", False)


def get_evidence_evaluator_enabled() -> bool:
    if "evidence_evaluator_enabled" in _cache:
        return _cache["evidence_evaluator_enabled"]
    return getattr(get_settings(), "evidence_evaluator_enabled", False)


def get_self_critic_enabled() -> bool:
    if "self_critic_enabled" in _cache:
        return _cache["self_critic_enabled"]
    return getattr(get_settings(), "self_critic_enabled", False)


def get_final_polish_enabled() -> bool:
    if "final_polish_enabled" in _cache:
        return _cache["final_polish_enabled"]
    return getattr(get_settings(), "final_polish_enabled", False)
