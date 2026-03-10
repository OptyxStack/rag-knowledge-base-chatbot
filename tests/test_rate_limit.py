"""Tests for rate limit middleware."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.rate_limit import _is_admin_bearer_request, rate_limit_middleware


def _make_request(headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/conversations/refresh-cache",
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_is_admin_bearer_request_true_for_admin_jwt():
    request = _make_request({"Authorization": "Bearer admin-token"})
    with patch(
        "app.core.rate_limit.decode_access_token",
        return_value={"sub": "user-1", "role": "admin"},
    ):
        assert _is_admin_bearer_request(request) is True


def test_is_admin_bearer_request_false_for_non_admin_or_non_bearer():
    request = _make_request({"Authorization": "Bearer user-token"})
    with patch(
        "app.core.rate_limit.decode_access_token",
        return_value={"sub": "user-2", "role": "user"},
    ):
        assert _is_admin_bearer_request(request) is False

    api_key_request = _make_request({"X-Admin-API-Key": "secret"})
    assert _is_admin_bearer_request(api_key_request) is False


@pytest.mark.asyncio
async def test_rate_limit_middleware_skips_admin_bearer():
    request = _make_request({"Authorization": "Bearer admin-token"})

    async def call_next(req: Request):
        return SimpleNamespace(status_code=200, request=req)

    with patch(
        "app.core.rate_limit.decode_access_token",
        return_value={"sub": "user-1", "role": "admin"},
    ):
        response = await rate_limit_middleware(request, call_next)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_middleware_still_limits_non_admin(monkeypatch):
    request = _make_request({"Authorization": "Bearer user-token"})

    async def call_next(req: Request):
        return SimpleNamespace(status_code=200, request=req)

    class FakePipeline:
        def zadd(self, *args, **kwargs):
            return self

        def zremrangebyscore(self, *args, **kwargs):
            return self

        def zcard(self, *args, **kwargs):
            return self

        def expire(self, *args, **kwargs):
            return self

        async def execute(self):
            return [None, None, 999, True]

    class FakeRedis:
        def pipeline(self):
            return FakePipeline()

        async def close(self):
            return None

    monkeypatch.setattr(
        "app.core.rate_limit.get_settings",
        lambda: type(
            "S",
            (),
            {"redis_url": "redis://localhost:6379/0", "rate_limit_requests": 5, "rate_limit_window_seconds": 60},
        )(),
    )

    with patch("app.core.rate_limit.redis.from_url", return_value=FakeRedis()), patch(
        "app.core.rate_limit.decode_access_token",
        return_value={"sub": "user-2", "role": "user"},
    ):
        with pytest.raises(HTTPException) as exc:
            await rate_limit_middleware(request, call_next)

    assert exc.value.status_code == 429
