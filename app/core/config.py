"""Application configuration via environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API
    app_name: str = Field(default="GreenCloud", description="Company/app name for branding and cache keys")
    debug: bool = False
    api_prefix: str = "/v1"

    # Auth
    api_key: str = Field(default="", description="API key for standard access")
    admin_api_key: str = Field(default="", description="Admin API key for ingest/admin")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/support_ai",
        description="PostgreSQL connection URL (asyncpg driver)",
    )
    database_url_sync: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/support_ai",
        description="PostgreSQL connection URL (sync for Celery)",
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis URL")

    # Celery
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL",
    )

    # OpenSearch
    opensearch_host: str = Field(default="http://localhost:9200", description="OpenSearch host")
    opensearch_index: str = Field(default="support_docs", description="OpenSearch index name")
    opensearch_user: str = Field(default="", description="OpenSearch username")
    opensearch_password: str = Field(default="", description="OpenSearch password")

    # Qdrant
    qdrant_host: str = Field(default="localhost", description="Qdrant host")
    qdrant_port: int = Field(default=6333, description="Qdrant port")
    qdrant_collection: str = Field(default="support_chunks", description="Qdrant collection")
    qdrant_api_key: str = Field(default="", description="Qdrant API key (optional)")

    # Embeddings
    embedding_provider: Literal["openai", "custom"] = Field(default="openai")
    embedding_model: str = Field(default="text-embedding-3-small", description="OpenAI embedding model")
    embedding_dimensions: int = Field(default=1536, description="Embedding dimensions")
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_base_url: str = Field(default="", description="OpenAI-compatible API base URL (empty = default)")

    # LLM
    llm_provider: Literal["openai", "custom"] = Field(default="openai")
    llm_model: str = Field(default="gpt-5.2", description="LLM model name")
    llm_temperature: float = Field(default=0.0, ge=0, le=2, description="0 = deterministic, better for accuracy")

    # Reranker
    reranker_provider: Literal["local", "cohere", "custom"] = Field(default="local")
    reranker_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    reranker_url: str = Field(default="http://localhost:8001/rerank", description="Local reranker service URL")
    cohere_api_key: str = Field(default="", description="Cohere API key for reranker")

    # Retrieval
    retrieval_top_n: int = Field(default=50, description="Top N from each source (OpenSearch + Qdrant)")
    retrieval_top_k: int = Field(default=8, description="Top K after reranking (higher = more context)")
    retrieval_plans_extra_chunks: int = Field(default=4, description="Extra chunks for plans/pricing queries")
    # Intent-aware fetch: when query matches plans/price, also fetch from these doc_types (comma-separated)
    retrieval_plans_fetch_doc_types: str = Field(
        default="pricing",
        description="Doc types to additionally fetch for plans/price queries. Empty to disable.",
    )
    retrieval_ensure_doc_type_min: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Ensure at least N chunks from plans_fetch_doc_types in final evidence (diversity). 0=disabled.",
    )
    retrieval_fusion: Literal["rrf", "simple"] = Field(
        default="rrf",
        description="Merge strategy: rrf=Reciprocal Rank Fusion (strong), simple=dedupe by chunk_id",
    )
    retrieval_rrf_k: int = Field(default=60, ge=1, le=200, description="RRF constant k (higher = less rank sensitivity)")
    max_retrieval_attempts: int = Field(default=2, description="Max retrieval attempts before ASK_USER/ESCALATE")

    # Evidence Quality Gate (Phase 1)
    evidence_quality_enabled: bool = Field(default=True, description="Enable evidence quality gate before LLM")
    evidence_quality_threshold: float = Field(default=0.6, ge=0, le=1, description="Aggregate quality threshold when no required_evidence")
    evidence_feature_thresholds: dict[str, float] = Field(
        default={
            "numbers_units": 0.3,
            "has_any_url": 0.2,
            "has_transaction_link": 0.2,
            "policy_language": 0.3,
            "steps_structure": 0.2,
            "content_density": 0.3,
            "boilerplate_ratio": 0.4,
        },
        description="Per-feature min thresholds for required evidence",
    )

    # Chunking
    chunk_min_tokens: int = Field(default=300, ge=100, le=1000)
    chunk_max_tokens: int = Field(default=700, ge=200, le=1500)

    # Rate limiting
    rate_limit_requests: int = Field(default=60, description="Requests per window")
    rate_limit_window_seconds: int = Field(default=60, description="Rate limit window")

    # PII redaction
    pii_redact_emails: bool = Field(default=True)
    pii_redact_phones: bool = Field(default=True)

    # Gateway
    max_request_body_bytes: int = Field(default=1_000_000, description="Max request body size (1MB)")
    ip_blocklist: str = Field(default="", description="Comma-separated IPs to block")
    ip_allowlist: str = Field(default="", description="Comma-separated IPs to allow (empty=all)")

    # LLM fallback & caching
    llm_fallback_model: str = Field(default="gpt-3.5-turbo", description="Fallback model on primary failure")
    llm_cache_ttl_seconds: int = Field(default=3600, description="Response cache TTL")
    llm_prompt_cache_key: str = Field(default="", description="OpenAI prompt_cache_key for better cache hits")
    llm_prompt_cache_retention: str = Field(default="in_memory", description="OpenAI cache: in_memory or 24h")

    # Language (archi_v3)
    language_detect_enabled: bool = Field(default=True, description="Detect input language (non-LLM)")

    # Phase 2: Normalizer
    normalizer_enabled: bool = Field(default=True, description="Enable request normalizer (QuerySpec) before retrieval")
    normalizer_use_llm: bool = Field(
        default=False,
        description="Use LLM for intent/entities/evidence inference (fallback to rule-based on error)",
    )
    normalizer_llm_model: str = Field(
        default="gpt-4o-mini",
        description="Model for normalizer LLM (lightweight for cost; e.g. gpt-4o-mini)",
    )

    # Phase 3: Decision Router
    decision_router_enabled: bool = Field(default=True, description="Enable decision router before LLM (ASK_USER/ESCALATE without LLM call)")
    decision_router_use_llm: bool = Field(
        default=False,
        description="Use LLM for gray zone decisions (hybrid: deterministic rules first)",
    )
    decision_router_llm_model: str = Field(default="gpt-4o-mini", description="Model for decision router LLM")

    # Evidence Evaluator (archi_v3)
    evidence_evaluator_enabled: bool = Field(default=False, description="LLM evaluates evidence relevance, advises Retry Planner")
    evidence_evaluator_llm_model: str = Field(default="gpt-4o-mini", description="Model for evidence evaluator")

    # Self-Critic (archi_v3)
    self_critic_enabled: bool = Field(default=False, description="LLM self-critic after answer generation; regenerate on fail")
    self_critic_regenerate_max: int = Field(default=1, ge=0, le=2, description="Max regenerate attempts on self-critic fail")

    # Final Polish (archi_v3)
    final_polish_enabled: bool = Field(default=False, description="LLM final polish for clarity, structure, tone")

    # Phase 3: Budget controls
    retrieval_latency_budget_ms: int = Field(default=5000, description="Total retrieval latency budget across attempts (0=disabled)")
    retrieval_token_budget: int = Field(default=0, description="Token budget for normalizer LLM (0=unlimited, rule-based only)")

    # Intent cache (who am i, what can you do - skip LLM)
    intent_cache_enabled: bool = Field(default=True, description="Return predefined answers for common intents")
    llm_max_tokens: int = Field(default=2048, description="Max output tokens (keep under model context)")
    llm_max_evidence_chars: int = Field(default=1200, description="Max chars per evidence chunk in prompt")
    llm_timeout_seconds: float = Field(default=60.0)
    llm_retry_attempts: int = Field(default=2)

    # Object storage (MinIO/S3)
    object_storage_url: str = Field(default="", description="S3/MinIO endpoint")
    object_storage_access_key: str = Field(default="")
    object_storage_secret_key: str = Field(default="")
    object_storage_bucket: str = Field(default="support-ai-docs")


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
