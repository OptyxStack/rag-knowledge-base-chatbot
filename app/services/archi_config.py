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
    "evidence_quality_use_llm",
    "evidence_quality_llm_v2",
    "debug_llm_calls",
    "self_critic_enabled",
    "final_polish_enabled",
    "doc_type_classifier_enabled",
    "retrieval_doc_type_use_llm",
    "page_kind_filter_enabled",
    "llm_model_economy",
    "llm_task_aware_routing_enabled",
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


def _parse_value(key: str, value: str | None, settings: Any) -> Any:
    """Parse config value: bool for flags, str for llm_model_economy."""
    if key == "llm_model_economy":
        if value is not None and value.strip():
            return value.strip()
        return getattr(settings, "llm_model_economy", None) or "gpt-4o-mini"
    if key == "llm_task_aware_routing_enabled":
        return _parse_bool(value) if value is not None else getattr(settings, "llm_task_aware_routing_enabled", True)
    if value is not None:
        return _parse_bool(value)
    return getattr(settings, key, False)


async def refresh_cache(session: AsyncSession) -> None:
    """Load archi config from DB and update in-memory cache."""
    db_values = await _load_from_db(session)
    settings = get_settings()
    for key in CONFIG_KEYS:
        if key in db_values:
            _cache[key] = _parse_value(key, db_values[key], settings)
        else:
            _cache[key] = _parse_value(key, None, settings)
    _cache["updated_at"] = time.monotonic()
    log_data = {k: _cache.get(k) for k in CONFIG_KEYS if k != "llm_model_economy"}
    logger.info("archi_config_cache_refreshed", **log_data)


def get_language_detect_enabled() -> bool:
    if "language_detect_enabled" in _cache:
        return _cache["language_detect_enabled"]
    return getattr(get_settings(), "language_detect_enabled", True)


def get_decision_router_use_llm() -> bool:
    if "decision_router_use_llm" in _cache:
        return _cache["decision_router_use_llm"]
    return getattr(get_settings(), "decision_router_use_llm", True)


def get_evidence_evaluator_enabled() -> bool:
    if "evidence_evaluator_enabled" in _cache:
        return _cache["evidence_evaluator_enabled"]
    return getattr(get_settings(), "evidence_evaluator_enabled", False)


def get_evidence_quality_use_llm() -> bool:
    """Use LLM for evidence quality gate (flexible, query-aware). From DB or env."""
    if "evidence_quality_use_llm" in _cache:
        return _cache["evidence_quality_use_llm"]
    return getattr(get_settings(), "evidence_quality_use_llm", True)


def get_evidence_quality_llm_v2() -> bool:
    """Use LLM v2 (single pass/fail). When True, assess phase uses evaluate_quality_llm_v2."""
    if "evidence_quality_llm_v2" in _cache:
        return _cache["evidence_quality_llm_v2"]
    return getattr(get_settings(), "evidence_quality_llm_v2", False)


def get_debug_llm_calls() -> bool:
    """Capture full LLM prompts and responses in flow debug. From DB or env."""
    if "debug_llm_calls" in _cache:
        return _cache["debug_llm_calls"]
    return getattr(get_settings(), "debug_llm_calls", False)


def get_self_critic_enabled() -> bool:
    if "self_critic_enabled" in _cache:
        return _cache["self_critic_enabled"]
    return getattr(get_settings(), "self_critic_enabled", False)


def get_final_polish_enabled() -> bool:
    if "final_polish_enabled" in _cache:
        return _cache["final_polish_enabled"]
    return getattr(get_settings(), "final_polish_enabled", False)


def get_doc_type_classifier_enabled() -> bool:
    """Use LLM to classify doc_type from content when crawling. From DB or env."""
    if "doc_type_classifier_enabled" in _cache:
        return _cache["doc_type_classifier_enabled"]
    return getattr(get_settings(), "doc_type_classifier_enabled", False)


def get_retrieval_doc_type_use_llm() -> bool:
    """Use LLM to select doc types for retrieval (semantic routing). From DB or env."""
    if "retrieval_doc_type_use_llm" in _cache:
        return _cache["retrieval_doc_type_use_llm"]
    return getattr(get_settings(), "retrieval_doc_type_use_llm", False)


def get_page_kind_filter_enabled() -> bool:
    """Filter retrieval by page_kind (howto, faq, etc.). Disable when chunks lack page_kind. From DB or env."""
    if "page_kind_filter_enabled" in _cache:
        return _cache["page_kind_filter_enabled"]
    return getattr(get_settings(), "page_kind_filter_enabled", False)


def get_llm_model_economy() -> str:
    """Economy model for non-critical tasks. From DB or env."""
    if "llm_model_economy" in _cache:
        return _cache["llm_model_economy"]
    return getattr(get_settings(), "llm_model_economy", None) or "gpt-4o-mini"


def get_llm_task_aware_routing_enabled() -> bool:
    """Task-aware model routing: primary for generate/self_critic, economy for rest."""
    if "llm_task_aware_routing_enabled" in _cache:
        return _cache["llm_task_aware_routing_enabled"]
    return getattr(get_settings(), "llm_task_aware_routing_enabled", True)
