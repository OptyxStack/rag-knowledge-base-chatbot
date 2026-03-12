"""
Request Normalizer — flexible, LLM-led.

Goals:
- All queries go through LLM.
- Minimal invariants: JSON shape + light type coercion.
- No rule-based keyword matching / regex intent detection / slot heuristics.
- Fallback only when LLM fails (keeps pipeline alive).

Notes:
- QuerySpec remains the retrieval contract, but retrieval_profile/doc_type_prior
  from LLM are treated as soft hints (especially for exact answer tasks).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.conversation_context import truncate_for_prompt
from app.services.llm_gateway import get_llm_gateway
from app.services.retrieval_planner import (
    derive_hard_requirements,
    infer_retrieval_profile,
    sanitize_retrieval_profile,
)
from app.services.schemas import HypothesisSpec, QuerySpec

logger = get_logger(__name__)


_ALLOWED_INTENTS = {
    "transactional",
    "comparison",
    "policy",
    "troubleshooting",
    "account",
    "informational",
    "ambiguous",
    "social",
}
_ALLOWED_RISK = {"low", "medium", "high"}
_ALLOWED_ANSWER_SHAPES = {
    "direct_lookup",
    "yes_no",
    "recommendation",
    "comparison",
    "procedural",
    "bounded_summary",
}
_ALLOWED_ANSWER_TYPES = {
    "direct_link",
    "pricing",
    "policy",
    "troubleshooting",
    "general",
    "clarification",
    "account",
}
_ANSWER_TYPE_ALIASES = {
    "link": "direct_link",
    "order_link": "direct_link",
    "buy_link": "direct_link",
    "price": "pricing",
    "price_lookup": "pricing",
    "general_info": "general",
    "ask_user": "clarification",
    "ambiguous": "clarification",
}
_EXACT_ANSWER_TYPES = {"direct_link", "pricing", "policy"}
_DEFAULT_ACCEPTABLE_RELATED_TYPES = {
    "direct_link": ["pricing", "general"],
    "pricing": ["direct_link", "general"],
    "policy": ["general"],
    "troubleshooting": ["general"],
    "account": ["general"],
    "general": [],
    "clarification": [],
}
_ANSWER_TYPE_PROFILE_MAP = {
    "direct_link": "pricing_profile",
    "pricing": "pricing_profile",
    "policy": "policy_profile",
    "troubleshooting": "troubleshooting_profile",
    "account": "account_profile",
    "general": "generic_profile",
    "clarification": "generic_profile",
}
_ALLOWED_EVIDENCE_FAMILIES = {
    "pricing_limits",
    "policy_terms",
    "capability_availability",
    "transactional_link",
    "troubleshooting_steps",
    "comparison_analysis",
    "account_access",
    "general_info",
}
_STANDARD_EVIDENCE_TYPES = {
    "policy_language",
    "numbers_units",
    "transaction_link",
    "steps_structure",
    "has_any_url",
}
_VALID_DOC_TYPE_HINTS = {
    "pricing",
    "policy",
    "faq",
    "howto",
    "docs",
    "tos",
    "conversation",
    "blog",
}
_EVIDENCE_LABEL_ALIASES = {
    "policy": "policy_language",
    "policy_clause": "policy_language",
    "policy_text": "policy_language",
    "pricing": "numbers_units",
    "price": "numbers_units",
    "numbers": "numbers_units",
    "specs": "numbers_units",
    "order_link": "transaction_link",
    "buy_link": "transaction_link",
    "checkout_link": "transaction_link",
    "steps": "steps_structure",
    "howto": "steps_structure",
    "how_to": "steps_structure",
    "link": "has_any_url",
    "links": "has_any_url",
    "url": "has_any_url",
    "urls": "has_any_url",
}
_AVAILABILITY_QUERY_PHRASES = {
    "do you offer",
    "do u offer",
    "do you have",
    "do u have",
    "is there",
    "available in",
    "availability",
}
_AVAILABILITY_LOCATION_TERMS = {
    " singapore",
    " sg",
    " region",
    " location",
    " datacenter",
    " data center",
    " dc1",
    " dc2",
    " asia",
    " apac",
}
_AVAILABILITY_PRODUCT_TERMS = {
    "vps",
    "server",
    "hosting",
    "proxy",
}
_POLICY_QUERY_TERMS = {
    "refund",
    "policy",
    "terms",
    "tos",
    "allowed",
    "allow",
    "prohibited",
    "banned",
    "restriction",
    "restrictions",
    "eligible",
}

NORMALIZER_SYSTEM_PROMPT = """You normalize a user's query for a support chatbot.

Return JSON ONLY (no markdown, no extra text). If unsure, use empty lists or null.

Schema:
{
  "canonical_query_en": "English translation or original if already English",
  "entities": ["..."],

  "required_evidence": ["..."],
  "hard_requirements": ["..."],
  "soft_requirements": ["..."],
  "risk_level": "low|medium|high",
  "retrieval_profile": "pricing_profile|policy_profile|troubleshooting_profile|comparison_profile|account_profile|generic_profile",
  "doc_type_prior": ["pricing", "policy", "faq", "howto", "docs", "tos", "conversation"],

  "is_ambiguous": false,
  "clarifying_questions": [],
  "answerable_without_clarification": true,
  "missing_info_blocking": [],
  "missing_info_for_refinement": [],
  "blocking_clarifying_questions": [],
  "refinement_questions": [],
  "assistant_should_lead": false,
  "evidence_families": [],
  "answer_shape": "direct_lookup|yes_no|recommendation|comparison|procedural|bounded_summary",
  "answer_type": "direct_link|pricing|policy|troubleshooting|general|clarification|account",
  "target_entity": "optional entity/page family name",
  "answer_expectation": "exact|best_effort|clarify_first",
  "acceptable_related_types": [],
  "answer_mode": "PASS_EXACT|PASS_PARTIAL|ASK_USER",
  "support_level": "strong|partial|weak",
  "blocking_missing_slots": [],
  "primary_hypothesis": {},
  "fallback_hypotheses": [],

  "keyword_queries": ["..."],
  "semantic_queries": ["..."],
  "retrieval_rewrites": ["..."],

  "skip_retrieval": false,
  "canned_response": "optional when skip_retrieval is true",
  "out_of_scope": false,

  "product_type": null,
  "os": null,
  "comparison_targets": [],
  "billing_cycle": null
}

Guidance (non-binding, principle-based—infer from user goal, not from keywords):
- canonical_query_en: English; use conversation context to resolve referents.
- is_ambiguous: false when context provides the referent; true only when it remains unclear.
- answerable_without_clarification: true when a useful preliminary answer is possible; false only when missing info prevents a safe answer.
- evidence_families: 1-3 broad evidence needs. Infer from the user's goal: what do they need to act or decide? (pricing_limits, policy_terms, transactional_link, troubleshooting_steps, comparison_analysis, capability_availability, account_access, general_info). Do not infer from surface keywords.
- answer_type: the class of information the user needs. Infer from goal: factual (plans, prices, links) → pricing or direct_link; rules/eligibility → policy; steps/how-to → troubleshooting; comparison → general with comparison; unclear → general. Do not infer from keywords.
- retrieval_profile: the evidence profile that would contain that answer type. Match to answer_type; when evidence_families is set, it drives retrieval. Use generic_profile when the goal is broad or unclear.
- doc_type_prior: soft hints for where to look; derive from evidence_families and answer_type. Do not prescribe narrow doc types.
- answer_shape: the expected answer form (yes_no, recommendation, comparison, procedural, direct_lookup, bounded_summary). Infer from goal.
- answer_expectation: exact for concrete lookup; best_effort for broad asks; clarify_first when blocked.
- required_evidence: standard types only (policy_language, numbers_units, transaction_link, steps_structure, has_any_url). Infer from what would satisfy the user's goal.
- retrieval_rewrites: 0-8 variations; order by effectiveness. Cover full scope of the query. Match how docs are phrased.
- For follow-up queries like "page link", "that link", "link please", "the link": if conversation context clearly identifies the referent (e.g. prior discussion was about Windows VPS, a product, or a page), resolve it. Set keyword_queries and semantic_queries to include the resolved topic (e.g. "Windows VPS page link", "greencloudvps windows vps"). Do not leave them as the raw user phrase when context resolves the referent.
- retrieval_profile and doc_type_prior are hints; retrieval will broaden when needed.
- skip_retrieval: true only for routine chitchat (greeting, thanks, bye). canned_response: friendly reply when skip_retrieval.
- out_of_scope: true when query is not about the support domain (AI self, personal, unrelated topics). Redirect politely.
- primary_hypothesis / fallback_hypotheses: main and alternative retrieval hypotheses when evidence may be in different families.
"""
NORMALIZER_SYSTEM_PROMPT = (
    NORMALIZER_SYSTEM_PROMPT
    + "\n"
    + "Additional evidence guidance:\n"
    + "- required_evidence / hard_requirements / soft_requirements: use only standard evidence types: policy_language, numbers_units, transaction_link, steps_structure, has_any_url.\n"
    + "- Do not invent ad-hoc evidence types such as 'promo plan details' or product-specific labels.\n"
)


def _get_greeting_response() -> str:
    app_name = (get_settings().app_name or "").strip()
    if app_name:
        return f"Hello! Welcome to {app_name} support. How can I help you today?"
    return "Hello! Welcome. How can I help you today?"


def _get_out_of_scope_response() -> str:
    """Redirect when query is not about the support domain (AI self, personal, etc.)."""
    app_name = (get_settings().app_name or "").strip()
    if app_name:
        return f"I'm here to help with {app_name} questions—pricing, policies, troubleshooting, and more. How can I assist you today?"
    return "I'm here to help with product and support questions. How can I assist you today?"


def _extract_probable_json(text: str) -> str:
    """
    Robust-ish JSON extraction without content rules:
    - Accept raw JSON.
    - If code-fenced, strip fences.
    - Else try to isolate the first {...} block.
    """
    s = (text or "").strip()

    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        last = s.rfind("```")
        if last != -1:
            s = s[:last].strip()

    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return s

    start = s.find("{")
    end = s.rfind("}")
    if 0 <= start < end:
        return s[start : end + 1].strip()

    return s


def _as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    try:
        return str(v).strip()
    except Exception:
        return default


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    return default


def _as_str_list(v: Any, limit: int | None = None) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        out = [str(x).strip() for x in v if x is not None and str(x).strip()]
    else:
        out = [str(v).strip()] if str(v).strip() else []
    if limit is not None:
        out = out[:limit]
    # stable de-dup
    seen: set[str] = set()
    dedup: list[str] = []
    for x in out:
        xl = x.lower()
        if xl in seen:
            continue
        seen.add(xl)
        dedup.append(x)
    return dedup


def _sanitize_evidence_labels(v: Any, limit: int | None = None) -> list[str]:
    labels = _as_str_list(v, limit=limit)
    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        normalized = _EVIDENCE_LABEL_ALIASES.get(label.strip().lower(), label.strip().lower())
        if normalized not in _STANDARD_EVIDENCE_TYPES or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _sanitize_intent(v: Any) -> str:
    intent = _as_str(v, "informational").lower()
    return intent if intent in _ALLOWED_INTENTS else "informational"


def _sanitize_risk(v: Any) -> str:
    risk = _as_str(v, "low").lower()
    return risk if risk in _ALLOWED_RISK else "low"


def _sanitize_answer_shape(v: Any) -> str:
    shape = _as_str(v, "direct_lookup").lower()
    return shape if shape in _ALLOWED_ANSWER_SHAPES else "direct_lookup"


def _sanitize_answer_type(v: Any) -> str:
    raw = _as_str(v, "").lower()
    normalized = _ANSWER_TYPE_ALIASES.get(raw, raw)
    if normalized in _ALLOWED_ANSWER_TYPES:
        return normalized
    return "general"


def _sanitize_answer_expectation(v: Any) -> str:
    value = _as_str(v, "").lower()
    if value in {"exact", "best_effort", "clarify_first"}:
        return value
    return "best_effort"


def _sanitize_answer_mode(v: Any) -> str:
    value = _as_str(v, "").upper()
    if value in {"PASS_EXACT", "PASS_PARTIAL", "ASK_USER"}:
        return value
    aliases = {
        "EXACT": "PASS_EXACT",
        "PARTIAL": "PASS_PARTIAL",
        "CLARIFY": "ASK_USER",
    }
    return aliases.get(value, "PASS_EXACT")


def _sanitize_support_level(v: Any) -> str:
    value = _as_str(v, "").lower()
    if value in {"strong", "partial", "weak"}:
        return value
    return "strong"


def _sanitize_doc_type_prior(v: Any) -> list[str]:
    out: list[str] = []
    for item in _as_str_list(v, limit=8):
        normalized = item.lower().strip()
        if normalized in _VALID_DOC_TYPE_HINTS and normalized not in out:
            out.append(normalized)
    return out


def _sanitize_acceptable_related_types(v: Any, answer_type: str) -> list[str]:
    out: list[str] = []
    for item in _as_str_list(v, limit=6):
        normalized = _sanitize_answer_type(item)
        if normalized != answer_type and normalized not in out:
            out.append(normalized)
    if not out:
        out = list(_DEFAULT_ACCEPTABLE_RELATED_TYPES.get(answer_type, []))
    return out[:4]


def _derive_answer_type(*, intent: str, required_evidence: list[str], is_ambiguous: bool) -> str:
    if is_ambiguous:
        return "clarification"
    req = {x.lower() for x in required_evidence}
    if "transaction_link" in req:
        return "direct_link"
    if "policy_language" in req or intent == "policy":
        return "policy"
    if "steps_structure" in req or intent == "troubleshooting":
        return "troubleshooting"
    if "numbers_units" in req or intent == "transactional":
        return "pricing"
    if intent == "account":
        return "account"
    return "general"


def _derive_answer_expectation(
    *,
    answer_type: str,
    answerable_without_clarification: bool,
    missing_info_blocking: list[str],
) -> str:
    if missing_info_blocking or not answerable_without_clarification:
        return "clarify_first"
    if answer_type in _EXACT_ANSWER_TYPES:
        return "exact"
    return "best_effort"


def _looks_like_availability_query(
    *,
    query: str,
    canonical_query_en: str,
    entities: list[str],
    slots: dict[str, Any],
    intent: str,
    answer_shape: str,
) -> bool:
    text = f" {(canonical_query_en or query or '').strip().lower()} "
    if not text.strip():
        return False
    if intent == "policy" or any(term in text for term in _POLICY_QUERY_TERMS):
        return False

    entity_blob = " ".join(str(item or "").strip().lower() for item in entities if str(item or "").strip())
    slot_blob = " ".join(
        str(slots.get(key, "") or "").strip().lower()
        for key in ("product_type", "os")
    ).strip()
    combined_blob = f"{entity_blob} {slot_blob}".strip()
    has_product_signal = any(term in combined_blob for term in _AVAILABILITY_PRODUCT_TERMS)
    has_offer_signal = any(phrase in text for phrase in _AVAILABILITY_QUERY_PHRASES) or " available " in text
    has_location_signal = any(term in text for term in _AVAILABILITY_LOCATION_TERMS) or " in " in text

    if has_offer_signal and has_product_signal and has_location_signal:
        return True
    if answer_shape == "yes_no" and has_product_signal and has_location_signal and ("offer" in text or "have" in text or "available" in text):
        return True
    return False


def _reprioritize_doc_types_for_availability(doc_type_prior: list[str]) -> list[str]:
    prioritized = ["pricing", "docs", "faq"]
    out: list[str] = []
    for item in [*prioritized, *doc_type_prior]:
        normalized = str(item or "").strip().lower()
        if normalized and normalized not in out:
            out.append(normalized)
    return out[:8]


def _adjust_availability_contract(
    *,
    required_evidence: list[str],
    hard_requirements: list[str],
    soft_requirements: list[str],
    answer_shape: str,
    doc_type_prior: list[str],
) -> tuple[list[str], list[str], list[str], str, list[str]]:
    required = [item for item in required_evidence if item != "policy_language"]
    hard = [item for item in hard_requirements if item != "policy_language"]
    soft = [item for item in soft_requirements if item != "policy_language"]
    if "has_any_url" not in soft:
        soft.append("has_any_url")
    return (
        required,
        hard,
        soft,
        "yes_no" if answer_shape != "yes_no" else answer_shape,
        _reprioritize_doc_types_for_availability(doc_type_prior),
    )


def _derive_answer_mode(
    *,
    answerable_without_clarification: bool,
    answer_mode_hint: str,
    answer_expectation: str,
) -> str:
    if not answerable_without_clarification or answer_mode_hint == "ask_user" or answer_expectation == "clarify_first":
        return "ASK_USER"
    if answer_mode_hint == "weak":
        return "PASS_PARTIAL"
    return "PASS_EXACT"


def _derive_support_level(*, answer_mode: str, hard_requirements: list[str], required_evidence: list[str]) -> str:
    if answer_mode == "ASK_USER":
        return "weak"
    if answer_mode == "PASS_PARTIAL":
        return "partial"
    if hard_requirements and len(hard_requirements) >= len(required_evidence or []):
        return "strong"
    return "partial" if required_evidence else "strong"


def _derive_target_entity(*, payload: dict[str, Any], entities: list[str]) -> str | None:
    explicit = _as_str(payload.get("target_entity"))
    if explicit:
        return explicit
    if entities:
        return entities[0]
    return None


def _enforce_exact_task_hints(
    *,
    answer_type: str,
    retrieval_profile: str,
    doc_type_prior: list[str],
) -> tuple[str, list[str]]:
    if answer_type not in _EXACT_ANSWER_TYPES:
        return retrieval_profile, doc_type_prior

    desired_profile = _ANSWER_TYPE_PROFILE_MAP.get(answer_type, "generic_profile")
    profile = retrieval_profile or desired_profile
    if profile != desired_profile:
        profile = desired_profile

    required_doc_types: list[str] = []
    if answer_type == "policy":
        required_doc_types = ["policy", "tos"]
    elif answer_type == "pricing":
        required_doc_types = ["pricing", "tos"]
    elif answer_type == "direct_link":
        required_doc_types = ["pricing", "docs"]

    merged = list(doc_type_prior)
    for doc_type in reversed(required_doc_types):
        if doc_type in merged:
            merged.remove(doc_type)
        merged.insert(0, doc_type)
    return profile, merged[:8]


def _sanitize_evidence_families(v: Any, limit: int | None = None) -> list[str]:
    families = _as_str_list(v, limit=limit)
    out: list[str] = []
    seen: set[str] = set()
    for family in families:
        normalized = family.strip().lower()
        if normalized not in _ALLOWED_EVIDENCE_FAMILIES or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _coerce_answerable_without_clarification(
    payload: dict[str, Any],
    *,
    raw_is_ambiguous: bool,
    missing_info_blocking: list[str],
    missing_info_for_refinement: list[str],
    blocking_questions: list[str],
    refinement_questions: list[str],
    assistant_should_lead: bool,
) -> bool:
    explicit = payload.get("answerable_without_clarification")
    if isinstance(explicit, bool):
        return explicit
    if missing_info_blocking or blocking_questions:
        return False
    if assistant_should_lead or missing_info_for_refinement or refinement_questions:
        return True
    return not raw_is_ambiguous


def _infer_evidence_families(
    *,
    intent: str,
    required_evidence: list[str],
    hard_requirements: list[str],
    answer_shape: str,
) -> list[str]:
    req = {r.lower() for r in required_evidence}
    hard = {r.lower() for r in hard_requirements}
    families: list[str] = []
    if "policy_language" in req or "policy_language" in hard or intent == "policy":
        families.append("policy_terms")
    if {"numbers_units", "transaction_link", "has_any_url"} & req or intent == "transactional":
        families.append("pricing_limits")
    if "transaction_link" in req:
        families.append("transactional_link")
    if "steps_structure" in req or intent == "troubleshooting":
        families.append("troubleshooting_steps")
    if answer_shape == "comparison" or intent == "comparison":
        families.append("comparison_analysis")
    if intent == "account":
        families.append("account_access")
    if answer_shape == "yes_no":
        families.append("capability_availability")
    if not families:
        families.append("general_info")
    return families[:3]


def _infer_answer_shape(
    *,
    intent: str,
    assistant_should_lead: bool,
    required_evidence: list[str],
) -> str:
    if assistant_should_lead:
        return "recommendation"
    req = {r.lower() for r in required_evidence}
    if intent == "comparison":
        return "comparison"
    if intent == "troubleshooting" or "steps_structure" in req:
        return "procedural"
    if intent in {"policy", "transactional"}:
        return "yes_no"
    return "direct_lookup"


def _derive_hypothesis_doc_types(families: list[str], base_doc_types: list[str]) -> list[str]:
    doc_types = list(base_doc_types)
    if "policy_terms" in families and "tos" not in doc_types:
        doc_types.insert(0, "tos")
    if "pricing_limits" in families and "pricing" not in doc_types:
        doc_types.insert(0, "pricing")
    if "troubleshooting_steps" in families and "docs" not in doc_types:
        doc_types.append("docs")
    if "capability_availability" in families and "conversation" not in doc_types:
        doc_types.append("conversation")
    return list(dict.fromkeys(doc_types))


def _build_hypothesis(
    *,
    name: str,
    evidence_families: list[str],
    answer_shape: str,
    retrieval_profile: str,
    required_evidence: list[str],
    hard_requirements: list[str],
    soft_requirements: list[str],
    doc_type_prior: list[str],
    rewrite_candidates: list[str],
    query_hint: str | None = None,
) -> HypothesisSpec:
    preferred_sources = [d for d in doc_type_prior if d in {"conversation", "faq", "blog"}]
    return HypothesisSpec(
        name=name,
        evidence_families=evidence_families,
        answer_shape=answer_shape,
        retrieval_profile=retrieval_profile,
        required_evidence=list(required_evidence),
        hard_requirements=list(hard_requirements),
        soft_requirements=list(soft_requirements),
        doc_type_prior=list(doc_type_prior),
        preferred_sources=list(dict.fromkeys(preferred_sources)),
        rewrite_candidates=list(rewrite_candidates[:5]),
        query_hint=query_hint,
    )


def _build_default_hypotheses(
    *,
    canonical_query_en: str,
    evidence_families: list[str],
    answer_shape: str,
    retrieval_profile: str,
    required_evidence: list[str],
    hard_requirements: list[str],
    soft_requirements: list[str],
    doc_type_prior: list[str],
    rewrite_candidates: list[str],
) -> tuple[HypothesisSpec, list[HypothesisSpec]]:
    base_doc_types = _derive_hypothesis_doc_types(evidence_families, doc_type_prior)
    primary = _build_hypothesis(
        name="primary",
        evidence_families=evidence_families,
        answer_shape=answer_shape,
        retrieval_profile=retrieval_profile,
        required_evidence=required_evidence,
        hard_requirements=hard_requirements,
        soft_requirements=soft_requirements,
        doc_type_prior=base_doc_types,
        rewrite_candidates=rewrite_candidates,
        query_hint=canonical_query_en,
    )
    fallback_specs: list[HypothesisSpec] = []
    fallback_1_families = list(dict.fromkeys(["capability_availability", "pricing_limits", *evidence_families]))[:3]
    fallback_1_doc_types = _derive_hypothesis_doc_types(
        fallback_1_families,
        ["tos", "pricing", "policy", "docs", "conversation"],
    )
    fallback_specs.append(
        _build_hypothesis(
            name="fallback_capability",
            evidence_families=fallback_1_families,
            answer_shape="yes_no" if answer_shape == "direct_lookup" else answer_shape,
            retrieval_profile="pricing_profile" if retrieval_profile != "troubleshooting_profile" else "generic_profile",
            required_evidence=list(dict.fromkeys(required_evidence or ["numbers_units", "has_any_url"])),
            hard_requirements=[],
            soft_requirements=list(dict.fromkeys([*soft_requirements, "policy_language", "numbers_units"])),
            doc_type_prior=fallback_1_doc_types,
            rewrite_candidates=rewrite_candidates,
            query_hint=f"{canonical_query_en} terms availability pricing",
        )
    )
    fallback_2_families = list(dict.fromkeys(["general_info", "policy_terms", *evidence_families]))[:3]
    fallback_2_doc_types = _derive_hypothesis_doc_types(
        fallback_2_families,
        ["policy", "tos", "faq", "conversation", "pricing"],
    )
    fallback_specs.append(
        _build_hypothesis(
            name="fallback_policy",
            evidence_families=fallback_2_families,
            answer_shape="bounded_summary",
            retrieval_profile="policy_profile" if retrieval_profile != "troubleshooting_profile" else "generic_profile",
            required_evidence=list(dict.fromkeys(required_evidence or ["policy_language"])),
            hard_requirements=[],
            soft_requirements=list(dict.fromkeys([*soft_requirements, "policy_language"])),
            doc_type_prior=fallback_2_doc_types,
            rewrite_candidates=rewrite_candidates,
            query_hint=f"{canonical_query_en} terms of service policy",
        )
    )
    return primary, fallback_specs[:2]


def _parse_hypothesis_list(
    value: Any,
    *,
    default_families: list[str],
    default_shape: str,
    default_profile: str,
    default_required: list[str],
    default_hard: list[str],
    default_soft: list[str],
    default_doc_types: list[str],
    default_rewrites: list[str],
) -> list[HypothesisSpec]:
    if not isinstance(value, list):
        return []
    out: list[HypothesisSpec] = []
    for idx, item in enumerate(value[:3]):
        if not isinstance(item, dict):
            continue
        families = _sanitize_evidence_families(item.get("evidence_families"), limit=3) or list(default_families)
        shape = _sanitize_answer_shape(item.get("answer_shape") or default_shape)
        profile = sanitize_retrieval_profile(item.get("retrieval_profile")) or default_profile
        required = _sanitize_evidence_labels(item.get("required_evidence"), limit=10) or list(default_required)
        hard = _sanitize_evidence_labels(item.get("hard_requirements"), limit=10) or list(default_hard)
        soft = _sanitize_evidence_labels(item.get("soft_requirements"), limit=10) or list(default_soft)
        doc_types = _sanitize_doc_type_prior(item.get("doc_type_prior")) or list(default_doc_types)
        rewrites = _as_str_list(item.get("rewrite_candidates"), limit=5) or list(default_rewrites)
        out.append(
            _build_hypothesis(
                name=_as_str(item.get("name")) or f"hypothesis_{idx + 1}",
                evidence_families=families,
                answer_shape=shape,
                retrieval_profile=profile,
                required_evidence=required,
                hard_requirements=hard,
                soft_requirements=soft,
                doc_type_prior=_derive_hypothesis_doc_types(families, doc_types),
                rewrite_candidates=rewrites,
                query_hint=_as_str(item.get("query_hint")) or None,
            )
        )
    return out


def _parse_llm_slots(data: dict[str, Any]) -> dict[str, Any]:
    """
    Only take what the LLM explicitly provides.
    No config-driven / rule-based extraction.
    """
    slots: dict[str, Any] = {}

    pt = _as_str(data.get("product_type"))
    if pt:
        slots["product_type"] = pt.lower()

    os_val = _as_str(data.get("os"))
    if os_val:
        slots["os"] = os_val.lower()

    bc = _as_str(data.get("billing_cycle")).lower()
    if bc in ("monthly", "yearly"):
        slots["billing_cycle"] = bc

    ct = data.get("comparison_targets")
    if isinstance(ct, list):
        targets = [str(t).strip().lower() for t in ct if t and str(t).strip()]
        targets = targets[:3]
        if len(targets) >= 2:
            slots["comparison_targets"] = targets

    return slots


def _build_rewrite_candidates(
    query: str,
    canonical_query_en: str,
    keyword_queries: list[str],
    semantic_queries: list[str],
    retrieval_rewrites: list[str],
) -> list[str]:
    candidates: list[str] = []
    for s in [query, canonical_query_en, *retrieval_rewrites, *keyword_queries, *semantic_queries]:
        s2 = (s or "").strip()
        if s2:
            candidates.append(s2)
    # stable de-dup
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        cl = c.lower()
        if cl in seen:
            continue
        seen.add(cl)
        out.append(c)
    return out[:16]


def _apply_config_overrides(
    *,
    query: str,
    llm_entities: list[str],
    llm_slots: dict[str, Any],
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Apply compatibility overrides from settings for legacy deployments."""
    settings = get_settings()
    ql = (query or "").lower()
    entities = list(llm_entities)
    slots = dict(llm_slots or {})
    overrides: list[str] = []

    domain_terms = [t.strip().lower() for t in (settings.normalizer_domain_terms or "").split(",") if t.strip()]
    if domain_terms:
        overrides.append("normalizer_domain_terms")
        seen = {e.lower() for e in entities}
        for t in domain_terms:
            if t in ql and t not in seen:
                entities.append(t)
                seen.add(t)

    if settings.normalizer_query_expansion:
        overrides.append("normalizer_query_expansion")

    if settings.normalizer_slots_enabled:
        overrides.append("normalizer_slots_enabled")
        product_types = [
            t.strip().lower()
            for t in (settings.normalizer_slot_product_types or "").split(",")
            if t.strip()
        ]
        os_types = [
            t.strip().lower()
            for t in (settings.normalizer_slot_os_types or "").split(",")
            if t.strip()
        ]
        if product_types:
            overrides.append("normalizer_slot_product_types")
            if "product_type" not in slots:
                for p in product_types:
                    if p in ql:
                        slots["product_type"] = p
                        break
        if os_types:
            overrides.append("normalizer_slot_os_types")
            if "os" not in slots:
                for os_name in os_types:
                    if os_name in ql:
                        slots["os"] = os_name
                        break

    return entities, slots, list(dict.fromkeys(overrides))


async def _normalize_llm(
    query: str,
    conversation_history: list[dict[str, str]] | None,
    source_lang: str | None = None,
    locale: str | None = None,
) -> QuerySpec | None:
    from app.services.model_router import get_model_for_task

    model = get_model_for_task("normalizer")

    user_parts = [f"Query: {query.strip()}"]
    if source_lang:
        user_parts.append(f"source_lang: {source_lang}")
    if locale:
        user_parts.append(f"locale: {locale}")

    # Provide lightweight context. No rewriting/expansion logic in code.
    if conversation_history:
        truncated = truncate_for_prompt(conversation_history)
        content_limit = get_settings().conversation_message_content_max_chars
        ctx = "\n".join(
            f"{m.get('role', 'user')}: {(m.get('content') or '')[:content_limit]}"
            for m in truncated
        ).strip()
        if ctx:
            user_parts.append(f"Conversation context (last {len(truncated)}):\n{ctx}")

    user_content = "\n\n".join(user_parts).strip()

    try:
        from app.core.tracing import current_llm_task_var

        current_llm_task_var.set("normalizer")
        llm = get_llm_gateway()
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": NORMALIZER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=512,
        )

        raw = (resp.content or "").strip()
        payload = json.loads(_extract_probable_json(raw))
        if not isinstance(payload, dict):
            raise ValueError("LLM output is not a JSON object")

        risk_level = _sanitize_risk(payload.get("risk_level"))

        canonical_query_en = _as_str(payload.get("canonical_query_en")) or query.strip()
        src_lang = (_as_str(source_lang) or "en").lower() or "en"
        translation_needed = bool(src_lang != "en" and canonical_query_en.strip() and canonical_query_en.strip() != query.strip())

        entities = _as_str_list(payload.get("entities"), limit=12)
        required_evidence = _sanitize_evidence_labels(payload.get("required_evidence"), limit=10)
        explicit_hard_requirements = _sanitize_evidence_labels(payload.get("hard_requirements"), limit=10)
        explicit_soft_requirements = _sanitize_evidence_labels(payload.get("soft_requirements"), limit=10)
        doc_type_prior = _sanitize_doc_type_prior(payload.get("doc_type_prior"))

        raw_is_ambiguous = _as_bool(payload.get("is_ambiguous"), False)
        legacy_questions = _as_str_list(payload.get("clarifying_questions"), limit=3)
        missing_info_blocking = _as_str_list(payload.get("missing_info_blocking"), limit=5)
        missing_info_for_refinement = _as_str_list(payload.get("missing_info_for_refinement"), limit=5)
        blocking_missing_slots = _as_str_list(payload.get("blocking_missing_slots"), limit=8)
        blocking_questions = _as_str_list(payload.get("blocking_clarifying_questions"), limit=3)
        refinement_questions = _as_str_list(payload.get("refinement_questions"), limit=3)
        assistant_should_lead = _as_bool(payload.get("assistant_should_lead"), False)
        answerable_without_clarification = _coerce_answerable_without_clarification(
            payload,
            raw_is_ambiguous=raw_is_ambiguous,
            missing_info_blocking=missing_info_blocking,
            missing_info_for_refinement=missing_info_for_refinement,
            blocking_questions=blocking_questions,
            refinement_questions=refinement_questions,
            assistant_should_lead=assistant_should_lead,
        )
        is_ambiguous = bool(raw_is_ambiguous and not answerable_without_clarification)
        if not blocking_questions and is_ambiguous:
            blocking_questions = legacy_questions
        if not refinement_questions and answerable_without_clarification:
            refinement_questions = legacy_questions
        clarifying_questions = blocking_questions if not answerable_without_clarification else refinement_questions
        if not blocking_missing_slots:
            blocking_missing_slots = list(missing_info_blocking)

        keyword_queries = _as_str_list(payload.get("keyword_queries"), limit=2)
        semantic_queries = _as_str_list(payload.get("semantic_queries"), limit=2)
        retrieval_rewrites = _as_str_list(payload.get("retrieval_rewrites"), limit=8)

        out_of_scope = _as_bool(payload.get("out_of_scope"), False)
        skip_retrieval = _as_bool(payload.get("skip_retrieval"), False) or out_of_scope
        canned_response = _as_str(payload.get("canned_response"))
        if skip_retrieval and not canned_response:
            canned_response = _get_out_of_scope_response() if out_of_scope else _get_greeting_response()
        elif out_of_scope:
            canned_response = _get_out_of_scope_response()

        # Minimal defaults if LLM omits retrieval phrases
        if not keyword_queries and not skip_retrieval:
            keyword_queries = [canonical_query_en.strip() or query.strip()]
        if not semantic_queries and not skip_retrieval:
            semantic_queries = [canonical_query_en.strip() or query.strip()]

        slots = _parse_llm_slots(payload)
        entities, slots, config_overrides_applied = _apply_config_overrides(
            query=query.strip(),
            llm_entities=entities,
            llm_slots=slots,
        )
        constraints = dict(slots) if slots else {}

        rewrite_candidates = _build_rewrite_candidates(
            query=query.strip(),
            canonical_query_en=canonical_query_en.strip(),
            keyword_queries=keyword_queries,
            semantic_queries=semantic_queries,
            retrieval_rewrites=retrieval_rewrites,
        )

        intent = "social" if skip_retrieval else _sanitize_intent(payload.get("intent"))
        answer_mode_hint = "strong"
        if not answerable_without_clarification:
            answer_mode_hint = "ask_user"
        elif assistant_should_lead or missing_info_for_refinement or refinement_questions:
            answer_mode_hint = "weak"
        hard_requirements = derive_hard_requirements(
            explicit_hard_requirements,
            required_evidence,
            risk_level,
        )
        soft_requirements: list[str] = (
            explicit_soft_requirements if explicit_soft_requirements else list(required_evidence)
        )
        answer_shape = _sanitize_answer_shape(
            payload.get("answer_shape")
            or _infer_answer_shape(
                intent=intent,
                assistant_should_lead=assistant_should_lead,
                required_evidence=required_evidence,
            )
        )
        availability_query = _looks_like_availability_query(
            query=query.strip(),
            canonical_query_en=canonical_query_en.strip(),
            entities=entities,
            slots=slots,
            intent=intent,
            answer_shape=answer_shape,
        )
        if availability_query:
            required_evidence, hard_requirements, soft_requirements, answer_shape, doc_type_prior = _adjust_availability_contract(
                required_evidence=required_evidence,
                hard_requirements=hard_requirements,
                soft_requirements=soft_requirements,
                answer_shape=answer_shape,
                doc_type_prior=doc_type_prior,
            )
        evidence_families = _sanitize_evidence_families(payload.get("evidence_families"), limit=3)
        if not evidence_families:
            evidence_families = _infer_evidence_families(
                intent=intent,
                required_evidence=required_evidence,
                hard_requirements=hard_requirements,
                answer_shape=answer_shape,
            )
        answer_type = _sanitize_answer_type(payload.get("answer_type"))
        if answer_type == "general":
            answer_type = _derive_answer_type(
                intent=intent,
                required_evidence=required_evidence,
                is_ambiguous=is_ambiguous,
            )
        if availability_query and answer_type == "policy":
            answer_type = "general"
        target_entity = _derive_target_entity(payload=payload, entities=entities)
        answer_expectation = _sanitize_answer_expectation(payload.get("answer_expectation"))
        if not _as_str(payload.get("answer_expectation")):
            answer_expectation = _derive_answer_expectation(
                answer_type=answer_type,
                answerable_without_clarification=answerable_without_clarification,
                missing_info_blocking=blocking_missing_slots,
            )
        if availability_query and answer_expectation == "exact":
            answer_expectation = "best_effort"
        acceptable_related_types = _sanitize_acceptable_related_types(
            payload.get("acceptable_related_types"),
            answer_type,
        )
        answer_mode = _sanitize_answer_mode(payload.get("answer_mode"))
        if not _as_str(payload.get("answer_mode")):
            answer_mode = _derive_answer_mode(
                answerable_without_clarification=answerable_without_clarification,
                answer_mode_hint=answer_mode_hint,
                answer_expectation=answer_expectation,
            )
        support_level = _sanitize_support_level(payload.get("support_level"))
        if not _as_str(payload.get("support_level")):
            support_level = _derive_support_level(
                answer_mode=answer_mode,
                hard_requirements=hard_requirements,
                required_evidence=required_evidence,
            )

        retrieval_profile = sanitize_retrieval_profile(payload.get("retrieval_profile"))
        if not retrieval_profile:
            retrieval_profile = infer_retrieval_profile(
                intent=intent,
                required_evidence=required_evidence,
                hard_requirements=hard_requirements,
                evidence_families=evidence_families,
            )
        if availability_query and retrieval_profile == "policy_profile":
            retrieval_profile = "generic_profile"
        retrieval_profile, doc_type_prior = _enforce_exact_task_hints(
            answer_type=answer_type,
            retrieval_profile=retrieval_profile,
            doc_type_prior=doc_type_prior,
        )

        if doc_type_prior:
            constraints = dict(constraints)
            constraints["doc_type_prior"] = doc_type_prior

        default_primary, default_fallbacks = _build_default_hypotheses(
            canonical_query_en=canonical_query_en.strip(),
            evidence_families=evidence_families,
            answer_shape=answer_shape,
            retrieval_profile=retrieval_profile,
            required_evidence=required_evidence,
            hard_requirements=hard_requirements,
            soft_requirements=soft_requirements,
            doc_type_prior=doc_type_prior or [],
            rewrite_candidates=rewrite_candidates,
        )
        primary_list = _parse_hypothesis_list(
            [payload.get("primary_hypothesis")] if isinstance(payload.get("primary_hypothesis"), dict) else [],
            default_families=evidence_families,
            default_shape=answer_shape,
            default_profile=retrieval_profile,
            default_required=required_evidence,
            default_hard=hard_requirements,
            default_soft=soft_requirements,
            default_doc_types=doc_type_prior or [],
            default_rewrites=rewrite_candidates,
        )
        primary_hypothesis = primary_list[0] if primary_list else default_primary
        fallback_hypotheses = _parse_hypothesis_list(
            payload.get("fallback_hypotheses"),
            default_families=evidence_families,
            default_shape=answer_shape,
            default_profile=retrieval_profile,
            default_required=required_evidence,
            default_hard=hard_requirements,
            default_soft=soft_requirements,
            default_doc_types=doc_type_prior or [],
            default_rewrites=rewrite_candidates,
        ) or default_fallbacks

        spec = QuerySpec(
            intent=intent,
            entities=entities,
            constraints=constraints,
            required_evidence=required_evidence,
            risk_level=risk_level,
            keyword_queries=([] if skip_retrieval else keyword_queries),
            semantic_queries=([] if skip_retrieval else semantic_queries),
            clarifying_questions=clarifying_questions,
            is_ambiguous=is_ambiguous,
            skip_retrieval=skip_retrieval,
            canned_response=(canned_response if skip_retrieval else None),
            out_of_scope=out_of_scope,
            original_query=query.strip(),
            source_lang=src_lang,
            translation_needed=translation_needed,
            canonical_query_en=(canonical_query_en.strip() or None),
            user_goal="general_info" if skip_retrieval else intent,
            resolved_slots=slots,
            missing_slots=missing_info_for_refinement,
            ambiguity_type=("referential" if is_ambiguous else None),
            answerable_without_clarification=answerable_without_clarification,
            missing_info_blocking=missing_info_blocking,
            missing_info_for_refinement=missing_info_for_refinement,
            blocking_clarifying_questions=blocking_questions,
            refinement_questions=refinement_questions,
            assistant_should_lead=assistant_should_lead,
            hard_requirements=hard_requirements,
            soft_requirements=soft_requirements,
            evidence_families=evidence_families,
            answer_shape=answer_shape,
            answer_type=answer_type,
            target_entity=target_entity,
            answer_expectation=answer_expectation,
            acceptable_related_types=acceptable_related_types,
            answer_mode=answer_mode,
            support_level=support_level,
            blocking_missing_slots=blocking_missing_slots,
            primary_hypothesis=primary_hypothesis,
            fallback_hypotheses=fallback_hypotheses,
            doc_type_prior=doc_type_prior,
            retrieval_profile=retrieval_profile,
            rewrite_candidates=([] if skip_retrieval else rewrite_candidates),
            answer_mode_hint=answer_mode_hint,
            extraction_mode="llm_primary",
            config_overrides_applied=config_overrides_applied,
        )

        logger.info(
            "normalizer_llm",
            intent=spec.intent,
            risk_level=spec.risk_level,
            is_ambiguous=spec.is_ambiguous,
            skip_retrieval=spec.skip_retrieval,
            required_evidence=spec.required_evidence,
            hard_requirements=spec.hard_requirements,
            retrieval_profile=spec.retrieval_profile,
            answer_type=spec.answer_type,
            answer_mode=spec.answer_mode,
            support_level=spec.support_level,
            translated=spec.translation_needed,
            canonical_query_preview=(canonical_query_en[:120] if canonical_query_en else None),
        )
        return spec

    except Exception as e:
        logger.warning("normalizer_llm_failed", error=str(e), query_preview=(query or "")[:80])
        return None


def _build_minimal_fallback(query: str, source_lang: str | None = None) -> QuerySpec:
    """Minimal QuerySpec when LLM fails. Keeps the pipeline running."""
    q = (query or "").strip()
    lang = (_as_str(source_lang) or "en").lower() or "en"
    return QuerySpec(
        intent="informational",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=[q] if q else [],
        semantic_queries=[q] if q else [],
        clarifying_questions=[],
        is_ambiguous=False,
        skip_retrieval=False,
        canned_response=None,
        original_query=q,
        source_lang=lang,
        translation_needed=False,
        canonical_query_en=q or None,
        user_goal="general_info",
        resolved_slots={},
        missing_slots=[],
        ambiguity_type=None,
        answerable_without_clarification=True,
        missing_info_blocking=[],
        missing_info_for_refinement=[],
        blocking_clarifying_questions=[],
        refinement_questions=[],
        assistant_should_lead=False,
        hard_requirements=[],
        soft_requirements=[],
        doc_type_prior=[],
        retrieval_profile="generic_profile",
        rewrite_candidates=[q] if q else [],
        answer_mode_hint="strong",
        extraction_mode="llm_fallback",
        config_overrides_applied=[],
        evidence_families=["general_info"],
        answer_shape="direct_lookup",
        answer_type="general",
        target_entity=None,
        answer_expectation="best_effort",
        acceptable_related_types=[],
        answer_mode="PASS_EXACT",
        support_level="partial",
        blocking_missing_slots=[],
        primary_hypothesis=_build_hypothesis(
            name="primary",
            evidence_families=["general_info"],
            answer_shape="direct_lookup",
            retrieval_profile="generic_profile",
            required_evidence=[],
            hard_requirements=[],
            soft_requirements=[],
            doc_type_prior=[],
            rewrite_candidates=[q] if q else [],
            query_hint=q or None,
        ),
        fallback_hypotheses=[],
    )


async def normalize(
    query: str,
    conversation_history: list[dict[str, str]] | None = None,
    locale: str | None = None,
    source_lang: str | None = None,
) -> QuerySpec:
    """Produce QuerySpec from raw query. LLM-led; minimal fallback on error."""
    q = (query or "").strip()
    settings = get_settings()
    try:
        configured_max_attempts = getattr(settings, "normalizer_llm_max_attempts", None)
        if configured_max_attempts in (None, ""):
            configured_max_attempts = os.getenv("NORMALIZER_LLM_MAX_ATTEMPTS", 1)
        max_attempts = max(1, int(configured_max_attempts))
    except Exception:
        max_attempts = 1
    try:
        configured_backoff = getattr(settings, "normalizer_llm_retry_backoff_ms", None)
        if configured_backoff in (None, ""):
            configured_backoff = os.getenv("NORMALIZER_LLM_RETRY_BACKOFF_MS", 0)
        retry_backoff_ms = max(0, int(configured_backoff))
    except Exception:
        retry_backoff_ms = 0

    spec: QuerySpec | None = None
    for attempt in range(1, max_attempts + 1):
        spec = await _normalize_llm(
            q,
            conversation_history,
            source_lang=source_lang,
            locale=locale,
        )
        if spec is not None:
            if attempt > 1:
                logger.info(
                    "normalizer_llm_retry_success",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    query_preview=q[:80],
                )
            return spec
        if attempt < max_attempts:
            logger.warning(
                "normalizer_llm_retrying",
                next_attempt=attempt + 1,
                max_attempts=max_attempts,
                query_preview=q[:80],
            )
            if retry_backoff_ms > 0:
                await asyncio.sleep((retry_backoff_ms * attempt) / 1000.0)
    if spec is not None:
        return spec
    logger.warning("normalizer_llm_fallback", reason="llm_failed", query_preview=q[:80])
    return _build_minimal_fallback(q, source_lang)
