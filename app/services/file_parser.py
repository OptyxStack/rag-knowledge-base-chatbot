"""Extract text from plain files: .txt, .md, .pdf."""

from io import BytesIO
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
ALLOWED_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
}


def _extract_txt_or_md(content: bytes, filename: str) -> str:
    """Decode text/markdown content. Tries utf-8, falls back to latin-1."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return content.decode("latin-1")
        except UnicodeDecodeError:
            logger.warning("file_parser_decode_failed", filename=filename)
            raise ValueError(f"Cannot decode {filename}: invalid encoding")


def _extract_pdf(content: bytes, filename: str) -> str:
    """Extract text from PDF using pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    if not parts:
        raise ValueError(f"No extractable text in PDF: {filename}")
    return "\n\n".join(parts)


def extract_text_from_file(
    content: bytes,
    filename: str,
    content_type: str | None = None,
) -> str:
    """
    Extract plain text from .txt, .md, or .pdf file content.

    Args:
        content: Raw file bytes
        filename: Original filename (used for extension detection)
        content_type: Optional MIME type (e.g. application/pdf)

    Returns:
        Extracted text string

    Raises:
        ValueError: Unsupported format or extraction failure
    """
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {filename}. Allowed: .txt, .md, .pdf"
        )

    if ext in (".txt", ".md"):
        return _extract_txt_or_md(content, filename)
    if ext == ".pdf":
        return _extract_pdf(content, filename)

    raise ValueError(f"Unsupported file type: {filename}")
