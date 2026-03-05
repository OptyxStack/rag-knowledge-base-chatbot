"""LLM-based doc type selection for retrieval. Chooses which doc types to search based on query semantics."""

import json
import re

from app.core.logging import get_logger
from app.services.doc_type_service import get_doc_types_for_prompt, get_valid_doc_type_keys
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task

logger = get_logger(__name__)


async def select_doc_types_for_query(query: str) -> list[str]:
    """
    Use LLM to select which doc types are relevant for the user query.
    Returns list of doc type keys to search (e.g. ["policy", "tos"], ["pricing"], ["faq", "howto"]).
    Empty list means search all (no filter).
    """
    types_list = get_doc_types_for_prompt()
    if not types_list:
        return []

    keys = [k for k, _ in types_list]
    types_desc = "\n".join(f"- {k}: {desc}" for k, desc in types_list)

    prompt = f"""Given the user's support query, select which document types are most relevant to search.

Document types:
{types_desc}

Output JSON only, no markdown:
{{"doc_types": ["type1", "type2"]}}

Rules:
- Return 1-3 most relevant types. Empty array [] means search all.
"""

    try:
        from app.core.tracing import current_llm_task_var
        current_llm_task_var.set("doc_type_router")
        llm = get_llm_gateway()
        model = get_model_for_task("doc_type_classifier")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query[:500]},
            ],
            temperature=0.0,
            model=model,
            max_tokens=64,
        )
        text = (resp.content or "").strip()
        if "```json" in text:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
            text = match.group(1) if match else text
        elif "```" in text:
            match = re.search(r"```\s*([\s\S]*?)\s*```", text)
            text = match.group(1) if match else text

        data = json.loads(text)
        raw = data.get("doc_types") or []
        valid = get_valid_doc_type_keys()
        selected = [str(x).strip().lower() for x in raw if str(x).strip().lower() in valid]
        if selected:
            logger.debug("doc_type_router", query=query[:50], doc_types=selected)
        return selected
    except Exception as e:
        logger.warning("doc_type_router_failed", query=query[:50], error=str(e))
        return []
