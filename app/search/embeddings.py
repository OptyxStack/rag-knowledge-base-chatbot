"""Embedding providers: OpenAI (default) or custom."""

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EmbeddingProvider

logger = get_logger(__name__)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embeddings."""

    def __init__(self) -> None:
        self._settings = get_settings()
        kwargs: dict = {"api_key": self._settings.openai_api_key}
        if self._settings.openai_base_url:
            kwargs["base_url"] = self._settings.openai_base_url.rstrip("/")
        self._client = AsyncOpenAI(**kwargs)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using OpenAI."""
        if not texts:
            return []

        try:
            response = await self._client.embeddings.create(
                model=self._settings.embedding_model,
                input=texts,
            )
            return [d.embedding for d in response.data]
        except Exception as e:
            logger.error("embedding_failed", error=str(e))
            raise

    def dimensions(self) -> int:
        return self._settings.embedding_dimensions


def get_embedding_provider() -> EmbeddingProvider:
    """Factory for embedding provider."""
    settings = get_settings()
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")
