"""Phase 2 ingestion tests: semantic units and optional parent references."""

from app.services.ingestion import _count_tokens, prepare_document


def _settings(
    *,
    parent_refs: bool,
):
    return type(
        "S",
        (),
        {
            "chunk_min_tokens": 120,
            "chunk_max_tokens": 320,
            "chunk_semantic_min_tokens": 40,
            "chunk_semantic_max_tokens": 90,
            "chunk_parent_refs_enabled": parent_refs,
        },
    )()


def _build_long_text(paragraphs: int = 10) -> str:
    sentence = (
        "This section explains refund eligibility, policy clauses, and required account checks "
        "for cancellation workflows in production systems."
    )
    blocks = [f"## Section {idx}\n" + " ".join([sentence] * 4) for idx in range(paragraphs)]
    return "\n\n".join(blocks)


def test_prepare_document_builds_smaller_semantic_units(monkeypatch):
    monkeypatch.setattr("app.services.ingestion.get_settings", lambda: _settings(parent_refs=True))

    cleaned, raw, chunks = prepare_document({"content": _build_long_text()})

    assert cleaned
    assert raw
    assert len(chunks) >= 4
    assert all(chunk.parent_ref for chunk in chunks)
    assert max(_count_tokens(chunk.chunk_text) for chunk in chunks) <= 110


def test_prepare_document_can_disable_parent_refs(monkeypatch):
    monkeypatch.setattr("app.services.ingestion.get_settings", lambda: _settings(parent_refs=False))

    _, _, chunks = prepare_document({"content": _build_long_text(paragraphs=6)})

    assert chunks
    assert all(chunk.parent_ref is None for chunk in chunks)
