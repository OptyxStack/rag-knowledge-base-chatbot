"""LLM Final Polish – archi_v3. Improves clarity, structure, tone. Cannot modify factual content."""

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm_gateway import get_llm_gateway

logger = get_logger(__name__)

FINAL_POLISH_PROMPT = """Improve the support answer for clarity, structure, and helpful tone.

RULES:
- Do NOT change factual content, numbers, prices, or links
- Do NOT add information not in the original
- You may: fix grammar, improve flow, add brief transitions, format lists
- Output ONLY the polished answer, no JSON, no explanation"""


async def polish(answer: str) -> str | None:
    """LLM polishes answer. Returns None on error (return original)."""
    if not getattr(get_settings(), "final_polish_enabled", False):
        return None

    if not answer or len(answer.strip()) < 50:
        return None

    try:
        try:
            from app.core.metrics import final_polish_total
            final_polish_total.inc()
        except Exception:
            pass
        llm = get_llm_gateway()
        model = getattr(get_settings(), "llm_model", "gpt-4o-mini")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": FINAL_POLISH_PROMPT},
                {"role": "user", "content": f"Answer to polish:\n\n{answer}"},
            ],
            temperature=0.2,
            model=model,
            max_tokens=2048,
        )
        polished = (resp.content or "").strip()
        if polished:
            try:
                from app.core.metrics import final_polish_total, final_polish_applied
                final_polish_total.inc()
                final_polish_applied.inc()
            except Exception:
                pass
            logger.info(
                "final_polish",
                applied=True,
                original_len=len(answer),
                polished_len=len(polished),
            )
            return polished
        return None
    except Exception as e:
        logger.warning("final_polish_failed", error=str(e))
        return None
