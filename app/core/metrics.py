"""Prometheus metrics: token cost, retrieval hit-rate, escalation rate, latency."""

from prometheus_client import Counter, Histogram, Gauge

# LLM
llm_requests_total = Counter(
    "support_ai_llm_requests_total",
    "Total LLM requests",
    ["model", "status"],
)
llm_tokens_total = Counter(
    "support_ai_llm_tokens_total",
    "Total tokens (input + output)",
    ["model", "type"],  # type: input | output
)
llm_cost_usd = Counter(
    "support_ai_llm_cost_usd_total",
    "Estimated cost in USD",
    ["model"],
)
llm_latency_seconds = Histogram(
    "support_ai_llm_latency_seconds",
    "LLM request latency",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)

# Retrieval
retrieval_requests_total = Counter(
    "support_ai_retrieval_requests_total",
    "Total retrieval requests",
)
retrieval_chunks_returned = Histogram(
    "support_ai_retrieval_chunks_returned",
    "Number of chunks returned per retrieval",
    buckets=[0, 1, 5, 10, 20, 50, 100],
)
retrieval_hit_rate = Counter(
    "support_ai_retrieval_hits_total",
    "Retrievals that returned at least one chunk",
)
retrieval_miss_rate = Counter(
    "support_ai_retrieval_misses_total",
    "Retrievals that returned zero chunks",
)

# Evidence Quality (Phase 1)
evidence_quality_score = Histogram(
    "support_ai_evidence_quality_score",
    "Evidence quality score (0-1) when gate triggers retry",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# archi_v3: Language detection
language_detect_total = Counter(
    "support_ai_language_detect_total",
    "Language detection calls",
    ["source_lang"],
)
language_detect_translated = Counter(
    "support_ai_language_detect_translated_total",
    "Queries translated from non-English",
)

# archi_v3: Evidence Evaluator
evidence_evaluator_total = Counter(
    "support_ai_evidence_evaluator_total",
    "Evidence evaluator LLM calls",
)
evidence_evaluator_retry_needed = Counter(
    "support_ai_evidence_evaluator_retry_needed_total",
    "Evidence evaluator suggested retry",
)
evidence_evaluator_relevance_score = Histogram(
    "support_ai_evidence_evaluator_relevance_score",
    "Evidence relevance score from LLM (0-1)",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# archi_v3: Self-Critic
self_critic_total = Counter(
    "support_ai_self_critic_total",
    "Self-critic LLM calls",
)
self_critic_fail_total = Counter(
    "support_ai_self_critic_fail_total",
    "Self-critic failed (regenerate triggered)",
)
self_critic_regenerate_total = Counter(
    "support_ai_self_critic_regenerate_total",
    "Answer regenerations after self-critic fail",
)

# archi_v3: Hybrid Decision Router
decision_router_llm_total = Counter(
    "support_ai_decision_router_llm_total",
    "Decision router LLM calls (gray zone)",
)
decision_router_llm_override = Counter(
    "support_ai_decision_router_llm_override_total",
    "LLM overrode ASK_USER to PASS",
)

# archi_v3: Final Polish
final_polish_total = Counter(
    "support_ai_final_polish_total",
    "Final polish LLM calls",
)
final_polish_applied = Counter(
    "support_ai_final_polish_applied_total",
    "Final polish successfully applied",
)

# Decisions
decision_total = Counter(
    "support_ai_decision_total",
    "Total decisions by type",
    ["decision"],  # PASS | ASK_USER | ESCALATE
)
escalation_rate = Counter(
    "support_ai_escalations_total",
    "Total escalations",
)

# API
api_requests_total = Counter(
    "support_ai_api_requests_total",
    "Total API requests",
    ["method", "path", "status"],
)
api_latency_seconds = Histogram(
    "support_ai_api_latency_seconds",
    "API request latency",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)

# Token pricing (approximate USD per 1K tokens)
TOKEN_PRICES = {
    "gpt-4o": (5.0, 15.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.5, 1.5),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD."""
    for prefix, (in_p, out_p) in TOKEN_PRICES.items():
        if model.startswith(prefix):
            return (input_tokens * in_p / 1_000_000) + (output_tokens * out_p / 1_000_000)
    return (input_tokens * 0.5 / 1_000_000) + (output_tokens * 1.5 / 1_000_000)
