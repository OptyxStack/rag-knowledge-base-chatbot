"""Pluggable LLM gateway - OpenAI with fallback, cache, token budget."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import hashlib
import json

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm_config import get_llm_api_key, get_llm_base_url, get_llm_fallback_model, get_llm_model

logger = get_logger(__name__)


@dataclass
class LLMResponse:
    """Structured LLM response."""

    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None


class LLMGateway(ABC):
    """Abstract LLM gateway interface."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send chat completion request."""
        pass


def _cache_key(messages: list, model: str, temperature: float) -> str:
    """Generate cache key for request."""
    payload = json.dumps({"messages": messages, "model": model, "temperature": temperature}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class OpenAIGateway(LLMGateway):
    """OpenAI Chat Completions with fallback, cache, retry, token budget."""

    def __init__(self) -> None:
        self._settings = get_settings()
        api_key = get_llm_api_key()
        base_url = get_llm_base_url()
        kwargs: dict = {
            "api_key": api_key,
            "timeout": self._settings.llm_timeout_seconds,
        }
        if base_url and base_url.strip():
            kwargs["base_url"] = base_url.strip()
        self._client = AsyncOpenAI(**kwargs)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> LLMResponse:
        """Call OpenAI with fallback, cache, retry."""
        model = kwargs.pop("model", None) or get_llm_model()
        models_to_try = [model, get_llm_fallback_model()]
        max_tokens = kwargs.pop("max_tokens", None) or self._settings.llm_max_tokens

        # Cache lookup (Redis)
        request_cache_key = _cache_key(messages, model, temperature)
        cached = await self._get_cached(request_cache_key)
        if cached:
            logger.debug("llm_cache_hit", model=model, cache_key=request_cache_key[:12])
            return cached

        # OpenAI prompt caching: improves cache hit rates for similar prompts
        extra_params: dict[str, Any] = {k: v for k, v in kwargs.items() if k not in ("model", "timeout")}
        prompt_cache_key = self._settings.llm_prompt_cache_key or f"support_ai:{self._settings.app_name}"
        extra_params["prompt_cache_key"] = prompt_cache_key
        if self._settings.llm_prompt_cache_retention in ("24h", "in_memory"):
            extra_params["prompt_cache_retention"] = self._settings.llm_prompt_cache_retention

        def _token_param(m: str) -> dict[str, Any]:
            """Use max_completion_tokens for o1/gpt-5; max_tokens for older models."""
            if m.startswith("o1") or m.startswith("gpt-5"):
                return {"max_completion_tokens": max_tokens}
            return {"max_tokens": max_tokens}

        last_error = None
        for m in models_to_try:
            try:
                response = await self._client.chat.completions.create(
                    model=m,
                    messages=messages,
                    temperature=temperature,
                    **_token_param(m),
                    **extra_params,
                )
                choice = response.choices[0] if response.choices else None
                if not choice:
                    raise ValueError("Empty LLM response")

                usage = response.usage or type("Usage", (), {"prompt_tokens": 0, "completion_tokens": 0})()
                inp = getattr(usage, "prompt_tokens", 0)
                out = getattr(usage, "completion_tokens", 0)
                result = LLMResponse(
                    content=choice.message.content or "",
                    model=response.model,
                    provider="openai",
                    input_tokens=inp,
                    output_tokens=out,
                    finish_reason=choice.finish_reason,
                    raw={"id": response.id, "model": response.model},
                )
                await self._set_cached(request_cache_key, result)
                logger.debug("llm_cache_store", model=response.model, cache_key=request_cache_key[:12])
                # Metrics
                try:
                    from app.core.metrics import (
                        llm_requests_total,
                        llm_tokens_total,
                        llm_cost_usd,
                        estimate_cost,
                    )
                    llm_requests_total.labels(model=response.model, status="success").inc()
                    llm_tokens_total.labels(model=response.model, type="input").inc(inp)
                    llm_tokens_total.labels(model=response.model, type="output").inc(out)
                    llm_cost_usd.labels(model=response.model).inc(estimate_cost(response.model, inp, out))
                except Exception:
                    pass
                try:
                    from app.core.tracing import llm_usage_var
                    acc = llm_usage_var.get()
                    if acc is not None:
                        acc.append({"model": response.model, "input_tokens": inp, "output_tokens": out})
                except Exception:
                    pass
                try:
                    from app.services.archi_config import get_debug_llm_calls
                    if get_debug_llm_calls():
                        from app.core.tracing import llm_call_log_var, current_llm_task_var
                        from app.core.metrics import estimate_cost
                        log_list = llm_call_log_var.get()
                        if log_list is not None:
                            task = current_llm_task_var.get() or "unknown"
                            msgs = [{"role": m.get("role", ""), "content": (m.get("content") or "")[:4000]} for m in messages]
                            cost = estimate_cost(response.model, inp, out)
                            log_list.append({
                                "task": task,
                                "messages": msgs,
                                "response_content": (result.content or "")[:4000],
                                "model": response.model,
                                "input_tokens": inp,
                                "output_tokens": out,
                                "cost_usd": round(cost, 6),
                            })
                except Exception:
                    pass
                return result
            except Exception as e:
                last_error = e
                logger.warning("llm_model_failed", model=m, error=str(e))
                if m == models_to_try[-1]:
                    raise last_error

        raise last_error or ValueError("LLM failed")

    async def _get_cached(self, key: str) -> LLMResponse | None:
        """Get from Redis cache."""
        try:
            import redis.asyncio as redis
            r = redis.from_url(self._settings.redis_url, decode_responses=False)
            data = await r.get(f"llm_cache:{key}")
            await r.close()
            if data:
                import pickle
                return pickle.loads(data)
        except Exception as e:
            logger.debug("llm_cache_get_failed", error=str(e))
        return None

    async def _set_cached(self, key: str, response: LLMResponse) -> None:
        """Set in Redis cache."""
        try:
            import redis.asyncio as redis
            import pickle
            r = redis.from_url(self._settings.redis_url, decode_responses=False)
            await r.setex(
                f"llm_cache:{key}",
                self._settings.llm_cache_ttl_seconds,
                pickle.dumps(response),
            )
            await r.close()
        except Exception as e:
            logger.debug("llm_cache_set_failed", error=str(e))


def get_llm_gateway() -> LLMGateway:
    """Factory for LLM gateway."""
    settings = get_settings()
    if settings.llm_provider == "openai":
        return OpenAIGateway()
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
