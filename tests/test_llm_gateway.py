"""Tests for OpenAI gateway cache-key behavior."""

from types import SimpleNamespace

import pytest

from app.services.llm_gateway import LLMResponse, OpenAIGateway, _cache_key, clear_llm_cache


class _Settings:
    llm_max_tokens = 256
    llm_timeout_seconds = 30.0
    llm_prompt_cache_key = "shared-prompt-cache-key"
    llm_prompt_cache_retention = "in_memory"
    llm_cache_ttl_seconds = 3600
    redis_url = "redis://localhost:6379/0"
    app_name = "SupportAI"


def _build_gateway(fake_create):
    gateway = OpenAIGateway.__new__(OpenAIGateway)
    gateway._settings = _Settings()
    gateway._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    return gateway


@pytest.mark.asyncio
async def test_chat_uses_request_hash_for_redis_cache(monkeypatch):
    calls: dict[str, object] = {}

    async def fake_create(**kwargs):
        calls["create_kwargs"] = kwargs
        return SimpleNamespace(
            id="resp-1",
            model=kwargs["model"],
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )

    gateway = _build_gateway(fake_create)

    async def fake_get_cached(key):
        calls["cache_get_key"] = key
        return None

    async def fake_set_cached(key, response):
        calls["cache_set_key"] = key
        calls["cached_response"] = response

    gateway._get_cached = fake_get_cached
    gateway._set_cached = fake_set_cached

    monkeypatch.setattr("app.services.llm_gateway.get_llm_model", lambda: "gpt-5.2")
    monkeypatch.setattr("app.services.llm_gateway.get_llm_fallback_model", lambda: "gpt-4o-mini")

    messages = [{"role": "user", "content": "What is VPS pricing?"}]
    result = await gateway.chat(messages=messages, temperature=0.0, model="gpt-5.2")

    expected_key = _cache_key(messages, "gpt-5.2", 0.0)
    assert calls["cache_get_key"] == expected_key
    assert calls["cache_set_key"] == expected_key
    assert calls["create_kwargs"]["prompt_cache_key"] == "shared-prompt-cache-key"
    assert calls["create_kwargs"]["prompt_cache_retention"] == "in_memory"
    assert "max_completion_tokens" in calls["create_kwargs"]
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_chat_returns_cached_response_without_calling_provider(monkeypatch):
    async def fake_create(**kwargs):
        raise AssertionError("provider should not be called on cache hit")

    gateway = _build_gateway(fake_create)
    cached = LLMResponse(
        content="cached-value",
        model="gpt-5.2",
        provider="openai",
        input_tokens=1,
        output_tokens=1,
    )

    async def fake_get_cached(key):
        return cached

    async def fake_set_cached(key, response):
        raise AssertionError("cache set should not run on cache hit")

    gateway._get_cached = fake_get_cached
    gateway._set_cached = fake_set_cached

    monkeypatch.setattr("app.services.llm_gateway.get_llm_model", lambda: "gpt-5.2")
    monkeypatch.setattr("app.services.llm_gateway.get_llm_fallback_model", lambda: "gpt-4o-mini")

    result = await gateway.chat(
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.1,
        model="gpt-5.2",
    )
    assert result is cached


@pytest.mark.asyncio
async def test_clear_llm_cache_deletes_llm_namespace(monkeypatch):
    class FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def keys(self, pattern):
            assert pattern == "llm_cache:*"
            return [b"llm_cache:a", b"llm_cache:b", b"llm_cache:c"]

        async def delete(self, *keys):
            assert keys == (b"llm_cache:a", b"llm_cache:b", b"llm_cache:c")
            return 3

        async def close(self):
            self.closed = True

    fake_redis = FakeRedis()
    monkeypatch.setattr("app.services.llm_gateway.get_settings", lambda: _Settings())

    from unittest.mock import patch

    with patch("redis.asyncio.from_url", return_value=fake_redis):
        result = await clear_llm_cache()

    assert result == {"deleted_keys": 3}
    assert fake_redis.closed is True
