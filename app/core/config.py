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
    retrieval_policy_doc_types: str = Field(
        default="",
        description="Doc types for policy/refund/terms queries. Empty = search all (no filter). E.g. 'policy,tos' to restrict.",
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
    max_retrieval_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Max retrieval attempts before ASK_USER/ESCALATE. Enterprise default 3 for policy/pricing.",
    )
    retrieval_profile_engine_enabled: bool = Field(
        default=True,
        description="Workstream 3: Use RetrievalPlan from QuerySpec. Disable to fall back to legacy heuristics.",
    )
    evidence_set_builder_enabled: bool = Field(
        default=True,
        description="Workstream 3: Build EvidenceSet from CandidatePool. Disable for legacy top-k only.",
    )
    evidence_requirement_keywords: dict[str, list[str]] = Field(
        default={
            "transaction_link": ["http", "www.", "order", "store"],
            "has_any_url": ["http", "www.", "order", "store"],
            "policy_language": ["policy", "terms", "refund", "eligible"],
            "steps_structure": ["step", "1.", "2.", "first", "second", "then"],
        },
        description="Fallback keyword hints for requirement->chunk matching in EvidenceSet builder when LLM coverage_map is absent.",
    )
    evidence_requirement_regex_patterns: dict[str, str] = Field(
        default={
            "numbers_units": r"\d+(\s*%|\s*usd|\s*vnd|\s*\$|/mo)",
        },
        description="Fallback regex hints for requirement->chunk matching in EvidenceSet builder when LLM coverage_map is absent.",
    )

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
    chunk_semantic_min_tokens: int = Field(
        default=120,
        ge=40,
        le=800,
        description="Target minimum tokens for semantic retrieval chunks.",
    )
    chunk_semantic_max_tokens: int = Field(
        default=260,
        ge=60,
        le=1000,
        description="Target maximum tokens for semantic retrieval chunks.",
    )
    chunk_parent_refs_enabled: bool = Field(
        default=True,
        description="Attach parent section references in chunk metadata for optional parent expansion.",
    )

    # Reviewer / claim parser policy patterns
    reviewer_high_risk_patterns: list[str] = Field(
        default=[
            r"\b(refund|reimburse|money back)\b",
            r"\b(billing|invoice|payment dispute)\b",
            r"\b(legal|lawsuit|attorney)\b",
            r"\b(abuse|fraud|violation)\b",
            r"\b(cancel.*subscription|terminate)\b",
        ],
        description="Regex patterns for high-risk query detection in reviewer gate.",
    )
    reviewer_policy_doc_types: list[str] = Field(
        default=["policy", "tos"],
        description="Doc types accepted as policy citations in reviewer gate.",
    )
    reviewer_policy_claim_patterns: list[str] = Field(
        default=[
            r"according to (?:our |the )?policy",
            r"(?:we |the company )?(?:shall|must|may not)",
            r"within \d+ (?:days|hours)",
            r"(?:eligible|entitled) (?:for|to)",
        ],
        description="Regex patterns for policy-like claims requiring citation in reviewer gate.",
    )
    claim_parser_policy_patterns: list[str] = Field(
        default=[
            r"according to (?:our |the )?policy",
            r"(?:we |the company )?(?:shall|must|may not)",
            r"within \d+ (?:days|hours)",
            r"(?:eligible|entitled) (?:for|to)",
            r"refund|reimburse|money back",
        ],
        description="Regex patterns to classify policy claims in claim parser.",
    )

    # Doc type classifier (crawl/ingestion)
    doc_type_classifier_enabled: bool = Field(
        default=False,
        description="Use LLM to classify doc_type from content (policy, tos, faq, howto, pricing, other). When disabled, uses URL-based inference.",
    )
    retrieval_doc_type_use_llm: bool = Field(
        default=False,
        description="Use LLM to select doc types for retrieval (semantic routing). When disabled, uses keyword heuristics.",
    )
    doc_type_url_keywords: dict[str, list[str]] = Field(
        default={
            "tos": ["terms", "tos"],
            "policy": ["privacy", "policy"],
            "faq": ["faq", "faqs"],
            "howto": ["docs", "documentation", "help"],
            "pricing": ["vps", "billing", "store", "pricing"],
        },
        description="URL keyword mapping for fallback doc_type inference (doc_type -> keyword list).",
    )

    # Rate limiting
    rate_limit_requests: int = Field(default=60, description="Requests per window")
    rate_limit_window_seconds: int = Field(default=60, description="Rate limit window")

    # Debug: capture full LLM prompts and responses for flow inspection (normalizer, evidence_quality, generate, etc.)
    debug_llm_calls: bool = Field(
        default=False,
        description="Capture full prompts and responses for each LLM call in flow debug. Set DEBUG_LLM_CALLS=true when debugging.",
    )

    # Pipeline logging (trace all RAG stages for debugging)
    pipeline_logging_enabled: bool = Field(
        default=True,
        description="Log each pipeline stage (answer_service, retrieve, assess, decide, generate, verify)",
    )

    # PII redaction
    pii_redact_emails: bool = Field(default=True)
    pii_redact_phones: bool = Field(default=True)

    # Evidence hygiene patterns (logging signals only)
    hygiene_boilerplate_patterns: list[str] = Field(
        default=[
            r"\bcontact\s+(?:us|support)\b",
            r"\bcopyright\s+(?:\u00A9)?\s*\d{4}",
            r"\b(?:privacy|terms)\s+(?:of\s+)?(?:service|policy)\b",
            r"\bmenu\b",
            r"\ball\s+rights\s+reserved\b",
            r"\bsign\s+(?:in|up)\b",
            r"\blogin\b",
            r"\bcart\b",
            r"\bcheckout\b",
            r"\bnav(?:igation)?\b",
            r"\bfooter\b",
            r"\bcookie\s+policy\b",
        ],
        description="Regex patterns for boilerplate detection in evidence hygiene telemetry.",
    )
    hygiene_transaction_path_patterns: list[str] = Field(
        default=[
            r"/(?:order|store|checkout|cart|buy|purchase|subscribe|billing)/?",
            r"/(?:dedicated-servers|proxies|semi-dedicated|vps)/?",
            r"(?:dedicated-servers|proxies|semi-dedicated|-vps|vps|billing)\.(?:php|html?)",
            r"order_link",
            r"order\s*link",
        ],
        description="Regex patterns used to detect transactional URLs/paths in evidence hygiene telemetry.",
    )

    # Gateway
    max_request_body_bytes: int = Field(default=1_000_000, description="Max request body size (1MB)")
    ip_blocklist: str = Field(default="", description="Comma-separated IPs to block")
    ip_allowlist: str = Field(default="", description="Comma-separated IPs to allow (empty=all)")

    # LLM fallback & caching
    llm_fallback_model: str = Field(default="gpt-3.5-turbo", description="Fallback model on primary failure")
    llm_model_economy: str = Field(
        default="gpt-4o-mini",
        description="Economy model for non-critical tasks (normalizer, decision_router, evidence_evaluator, final_polish)",
    )
    llm_task_aware_routing_enabled: bool = Field(
        default=True,
        description="Task-aware routing: primary (gpt-5.2) for generate/self_critic, economy for others",
    )
    llm_cache_ttl_seconds: int = Field(default=3600, description="Response cache TTL")
    llm_prompt_cache_key: str = Field(default="", description="OpenAI prompt_cache_key for better cache hits")
    llm_prompt_cache_retention: str = Field(default="in_memory", description="OpenAI cache: in_memory or 24h")

    # Language (archi_v3)
    language_detect_enabled: bool = Field(default=True, description="Detect input language (non-LLM)")

    # Phase 2: Normalizer
    normalizer_enabled: bool = Field(default=True, description="Enable request normalizer (QuerySpec) before retrieval")
    normalizer_use_llm: bool = Field(
        default=True,
        description="[Deprecated] Normalizer is LLM-only. Kept for config compatibility.",
    )
    normalizer_llm_model: str = Field(
        default="gpt-4o-mini",
        description="Model for normalizer LLM (lightweight for cost; e.g. gpt-4o-mini)",
    )
    normalizer_domain_terms: str = Field(
        default="",
        description="Config-driven domain override (UPGRADE_RAG_DESIGN). Comma-separated terms for rule-based entity extraction, e.g. vps,windows,linux,pricing. Empty = generic path.",
    )
    normalizer_query_expansion: bool = Field(
        default=False,
        description="Config-driven domain override. Enable intent-based BM25 query expansion (adds pricing,order,policy,etc. to keyword queries).",
    )
    normalizer_slots_enabled: bool = Field(
        default=False,
        description="Config-driven domain override. Enable rule-based slot extraction (product_type, os, billing_cycle, region) for deployment-specific compatibility.",
    )
    normalizer_slot_product_types: str = Field(
        default="",
        description="Comma-separated product types for slot extraction (e.g. vps,dedicated,plan_a). Empty = disabled.",
    )
    normalizer_slot_os_types: str = Field(
        default="",
        description="Comma-separated OS types for os slot (e.g. windows,linux,macos). Empty = disabled.",
    )

    # Query Rewriter (Phase 2: LLM-based when QuerySpec absent)
    query_rewriter_use_llm: bool = Field(
        default=True,
        description="Use LLM for query rewriting when QuerySpec is absent. When False, uses rule-based heuristic.",
    )
    query_rewriter_cache_enabled: bool = Field(
        default=True,
        description="Cache query rewrite results in Redis to reduce LLM calls.",
    )
    query_rewriter_cache_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400 * 7,
        description="Cache TTL for query rewrites (1h default, max 7 days).",
    )

    # Evidence Selector (Phase 1: coverage-aware selection)
    evidence_selector_use_llm: bool = Field(
        default=True,
        description="Use LLM for coverage-aware evidence selection. When False, uses top-k.",
    )
    evidence_selector_fallback_top_k: int = Field(
        default=8,
        ge=4,
        le=20,
        description="Fallback top-k when LLM disabled or fails.",
    )

    # Chunk filter – before generate: LLM selects relevant chunks
    chunk_filter_enabled: bool = Field(
        default=True,
        description="[Deprecated] Chunk filter is removed in Phase 3. Kept for config compatibility only.",
    )

    # Phase 3: Decision Router
    decision_router_enabled: bool = Field(default=True, description="Enable decision router before LLM (ASK_USER/ESCALATE without LLM call)")
    decision_router_use_llm: bool = Field(
        default=True,
        description="[Deprecated] Decision router is deterministic-only in Phase 3. Kept for config compatibility.",
    )
    decision_router_llm_model: str = Field(default="gpt-4o-mini", description="Model for decision router LLM")

    # Evidence Evaluator (archi_v3)
    evidence_evaluator_enabled: bool = Field(default=False, description="LLM evaluates evidence relevance, advises Retry Planner")
    evidence_evaluator_llm_model: str = Field(default="gpt-4o-mini", description="Model for evidence evaluator")

    # Evidence Quality Gate – LLM vs regex
    evidence_quality_use_llm: bool = Field(
        default=True,
        description="Use LLM for evidence quality gate (flexible, query-aware). When disabled, uses rule-based regex scoring.",
    )
    evidence_quality_llm_v2: bool = Field(
        default=True,
        description="Use LLM v2 (single pass/fail, partial-evidence aware). When True, overrides evidence_quality_use_llm path.",
    )

    # Self-Critic (archi_v3)
    self_critic_enabled: bool = Field(default=False, description="LLM self-critic after answer generation; regenerate on fail")
    self_critic_regenerate_max: int = Field(default=1, ge=0, le=2, description="Max regenerate attempts on self-critic fail")

    # Workstream 5: Claim-level review
    claim_level_review_enabled: bool = Field(
        default=True,
        description="Enable claim-level trim and lane downgrade instead of full rejection",
    )

    # Final Polish (archi_v3)
    final_polish_enabled: bool = Field(default=False, description="LLM final polish for clarity, structure, tone")

    # Phase 3: Budget controls
    retrieval_latency_budget_ms: int = Field(default=5000, description="Total retrieval latency budget across attempts (0=disabled)")
    retrieval_token_budget: int = Field(default=0, description="Token budget for normalizer LLM (0=unlimited, rule-based only)")

    # Prompt layering: Core + Domain + Custom (internal bot, not multi-tenant)
    prompt_domain: Literal["support", "legal", "generic"] = Field(
        default="support",
        description="Domain preset for system prompt: support (plans/pricing/escalation), legal (policy/high-risk), generic (minimal)",
    )

    # Intent cache (who am i, what can you do - skip LLM)
    intent_cache_enabled: bool = Field(default=True, description="Return predefined answers for common intents")
    intent_cache_disabled_keys: list[str] = Field(
        default=["refund_policy"],
        description="Intent keys that should never bypass retrieval/generation (e.g. refund_policy).",
    )
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
