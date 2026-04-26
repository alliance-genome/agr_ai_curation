"""Unit tests for shared document chunk identity helpers."""

from src.lib.openai_agents.tools.chunk_identity import resolve_chunk_identifier


def test_resolve_chunk_identifier_prefers_chunk_fields_before_metadata():
    chunk = {
        "id": "",
        "chunk_id": "chunk-level-id",
        "metadata": {"chunk_id": "metadata-id"},
    }

    assert resolve_chunk_identifier(chunk) == "chunk-level-id"


def test_resolve_chunk_identifier_uses_metadata_fallbacks():
    chunk = {
        "content": "Evidence text",
        "metadata": {"chunkId": "metadata-camel-id"},
    }

    assert resolve_chunk_identifier(chunk) == "metadata-camel-id"


def test_resolve_chunk_identifier_accepts_explicit_metadata():
    chunk = {"content": "Evidence text"}
    metadata = {"chunk_id": "parsed-metadata-id"}

    assert resolve_chunk_identifier(chunk, metadata) == "parsed-metadata-id"


def test_resolve_chunk_identifier_ignores_empty_and_non_mapping_metadata():
    chunk = {
        "id": "  ",
        "chunk_id": "",
        "chunkId": None,
        "metadata": '{"chunk_id":"json-not-parsed-here"}',
    }

    assert resolve_chunk_identifier(chunk) is None
