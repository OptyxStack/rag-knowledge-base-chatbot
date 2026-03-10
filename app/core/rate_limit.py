"""Redis-based token bucket rate limiting."""

import time
from typing import Callable

import redis.asyncio as redis
from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.auth_service import decode_access_token

logger = get_logger(__name__)

RATE_LIMIT_PREFIX = "rl:"
RATE_LIMIT_WINDOW = 60  # seconds


def _is_admin_bearer_request(request: Request) -> bool:
    """Bypass rate limit only for authenticated admin users via Bearer JWT."""
    auth_header = (request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return False
    token = auth_header[7:].strip()
    if not token:
        return False
    payload = decode_access_token(token)
    return bool(payload and payload.get("sub") and payload.get("role") == "admin")


async def rate_limit_middleware(request: Request, call_next: Callable):
    """Rate limit by external_user_id or IP."""
    if _is_admin_bearer_request(request):
        return await call_next(request)

    settings = get_settings()
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.warning("rate_limit_redis_unavailable", error=str(e))
        return await call_next(request)

    # Use external_user_id from body/query if available, else IP
    identifier = request.client.host if request.client else "unknown"
    # Try to get from header (set by routes that have user context)
    external_user_id = request.headers.get("X-External-User-Id")
    if external_user_id:
        identifier = f"user:{external_user_id}"

    key = f"{RATE_LIMIT_PREFIX}{identifier}"
    limit = settings.rate_limit_requests
    window = settings.rate_limit_window_seconds

    try:
        pipe = r.pipeline()
        now = time.time()
        pipe.zadd(key, {str(now): now})
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.zcard(key)
        pipe.expire(key, window + 1)
        results = await pipe.execute()
        count = results[2]

        if count > limit:
            await r.close()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {window} seconds.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("rate_limit_error", error=str(e))
    finally:
        try:
            await r.close()
        except Exception:
            pass

    return await call_next(request)
