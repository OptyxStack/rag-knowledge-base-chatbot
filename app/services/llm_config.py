"""LLM config from DB (app_config) with env fallback.

Keys: llm_model, llm_fallback_model, llm_api_key, llm_base_url.
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

CONFIG_KEYS = ("llm_model", "llm_fallback_model", "llm_api_key", "llm_base_url")
_cache: dict[str, Any] = {}
CACHE_TTL_SECONDS = 60


async def _load_from_db(session: AsyncSession) -> dict[str, str]:
    """Load llm_model and llm_fallback_model from app_config."""
    result: dict[str, str] = {}
    try:
        rows = await session.execute(
            select(AppConfig.key, AppConfig.value).where(AppConfig.key.in_(CONFIG_KEYS))
        )
        for key, value in rows.all():
            if value and value.strip():
                result[key] = value.strip()
    except Exception as e:
        logger.warning("llm_config_load_failed", error=str(e))
    return result


async def refresh_cache(session: AsyncSession) -> None:
    """Load LLM config from DB and update in-memory cache."""
    db_values = await _load_from_db(session)
    settings = get_settings()
    _cache["llm_model"] = db_values.get("llm_model") or settings.llm_model
    _cache["llm_fallback_model"] = db_values.get("llm_fallback_model") or settings.llm_fallback_model
    _cache["llm_api_key"] = db_values.get("llm_api_key") or settings.openai_api_key
    _cache["llm_base_url"] = db_values.get("llm_base_url") or settings.openai_base_url or ""
    _cache["updated_at"] = time.monotonic()
    logger.info(
        "llm_config_cache_refreshed",
        llm_model=_cache["llm_model"],
        llm_fallback_model=_cache["llm_fallback_model"],
    )


def get_llm_model() -> str:
    """Return primary LLM model. From DB cache or env fallback."""
    if "llm_model" in _cache:
        return _cache["llm_model"]
    return get_settings().llm_model


def get_llm_fallback_model() -> str:
    """Return fallback LLM model. From DB cache or env fallback."""
    if "llm_fallback_model" in _cache:
        return _cache["llm_fallback_model"]
    return get_settings().llm_fallback_model


def get_llm_api_key() -> str:
    """Return LLM API key (token). From DB cache or env fallback."""
    if "llm_api_key" in _cache:
        return _cache["llm_api_key"]
    return get_settings().openai_api_key


def get_llm_base_url() -> str:
    """Return LLM API base URL. From DB cache or env fallback. Empty = default OpenAI URL."""
    if "llm_base_url" in _cache:
        return _cache["llm_base_url"]
    return get_settings().openai_base_url or ""
