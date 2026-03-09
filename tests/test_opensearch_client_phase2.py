"""Phase 2 tests for OpenSearch chunk text behavior."""

from types import SimpleNamespace

import pytest

from app.search.opensearch_client import OpenSearchClient


def _make_client(response_payload):
    client = OpenSearchClient.__new__(OpenSearchClient)
    client._settings = SimpleNamespace(opensearch_index="support_docs")

    class _AsyncClient:
        async def search(self, index, body):
            _ = index, body
            return response_payload

    client._client = _AsyncClient()
    client._sync_client = None
    return client


@pytest.mark.asyncio
async def test_search_prefers_full_chunk_text_when_requested():
    response = {
        "hits": {
            "hits": [
                {
                    "_id": "c1",
                    "_score": 12.3,
                    "_source": {
                        "chunk_id": "c1",
                        "document_id": "d1",
                        "chunk_text": "FULL CHUNK TEXT CONTENT",
                        "source_url": "https://example.com",
                        "doc_type": "policy",
                    },
                    "highlight": {"chunk_text": ["SNIPPET CONTENT"]},
                }
            ]
        }
    }
    client = _make_client(response)
    chunks = await client.search("refund policy", prefer_snippet=False, use_highlight=True)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == "FULL CHUNK TEXT CONTENT"
    assert chunks[0].metadata is not None
    assert chunks[0].metadata.get("highlight_snippet") == "SNIPPET CONTENT"


@pytest.mark.asyncio
async def test_search_prefers_snippet_by_default():
    response = {
        "hits": {
            "hits": [
                {
                    "_id": "c2",
                    "_score": 8.0,
                    "_source": {
                        "chunk_id": "c2",
                        "document_id": "d1",
                        "chunk_text": "FULL CONTENT",
                        "source_url": "https://example.com/faq",
                        "doc_type": "faq",
                    },
                    "highlight": {"chunk_text": ["SHORT SNIPPET"]},
                }
            ]
        }
    }
    client = _make_client(response)
    chunks = await client.search("faq", use_highlight=True)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == "SHORT SNIPPET"
