"""Language detection – fast, non-LLM. Returns ISO 639-1 code (e.g. en, vi)."""

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def detect_language(text: str) -> str:
    """Detect language of text. Returns 'en' for English, or ISO 639-1 code (e.g. vi, zh, ja)."""
    if not text or not text.strip():
        return "en"

    if not getattr(get_settings(), "language_detect_enabled", True):
        return "en"

    try:
        from langdetect import detect, LangDetectException

        lang = detect(text.strip())
        lang = lang if lang else "en"
        try:
            from app.core.metrics import language_detect_total, language_detect_translated
            language_detect_total.labels(source_lang=lang).inc()
            if lang != "en":
                language_detect_translated.inc()
        except Exception:
            pass
        logger.info(
            "language_detect",
            source_lang=lang,
            query_preview=text[:80] if len(text) > 80 else text,
        )
        return lang
    except LangDetectException:
        logger.debug("langdetect_failed", text_preview=text[:50])
        return "en"
    except Exception as e:
        logger.warning("language_detect_error", error=str(e))
        return "en"
