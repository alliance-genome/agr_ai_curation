"""Tests for provider-backed Markdown ingestion."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.lib.document_sources.ingestion import (
    DocumentSourceIngestionError,
    DocumentSourceMarkdownValidationError,
    ProviderMarkdownIngestionRequest,
    ingest_provider_markdown_document,
)
from src.lib.document_sources.figure_metadata import (
    apply_provider_figure_page_provenance,
    append_provider_figure_metadata_markdown,
    normalize_provider_figure_metadata_sidecar,
    render_provider_figure_metadata_appendix,
)
from src.lib.openai_agents.evidence_spans import build_evidence_spans
import src.lib.openai_agents.tools.record_evidence as record_evidence
from src.lib.pipeline.chunk import chunk_parsed_document
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
import src.lib.document_sources.ingestion as ingestion
from src.models.pipeline import ProcessingStage
from src.models.strategy import ChunkingStrategy


def test_provider_markdown_structural_findings_are_non_blocking_warnings() -> None:
    warnings = ingestion._validate_provider_markdown(
        "## Methods\n\nText.\n\n# First title\n\n# Second title\n\n"
        "| allele | source |\n| a1 | paper |\n"
    )

    assert any(message.startswith("S01:") for message in warnings)
    assert any(message.startswith("S02:") for message in warnings)
    assert any(message.startswith("S07:") for message in warnings)


def test_provider_markdown_unknown_validation_error_remains_blocking(monkeypatch) -> None:
    import agr_abc_document_parsers

    unknown_issue = SimpleNamespace(
        rule_id="S99",
        line=12,
        message="Unsafe future structural condition",
    )
    monkeypatch.setattr(
        agr_abc_document_parsers,
        "validate_markdown",
        lambda _markdown: SimpleNamespace(errors=[unknown_issue], warnings=[]),
    )

    with pytest.raises(
        DocumentSourceMarkdownValidationError,
        match="S99: line 12: Unsafe future structural condition",
    ):
        ingestion._validate_provider_markdown("# Title\n")


def _source_provenance(**overrides):
    payload = {
        "provider": "abc_literature",
        "reference_id": "101",
        "reference_curie": "AGRKB:101",
        "source_file_id": "source-file-1",
        "converted_artifact_id": "converted-file-1",
        "source_md5": "abc123",
        "file_class": "converted_merged_main",
        "file_extension": "md",
        "access_scope": "restricted",
        "access_mods": {"mods": ["FB"]},
        "viewer_mode": "local_pdf",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_ingest_provider_markdown_document_runs_pipeline(monkeypatch):
    monkeypatch.setattr(
        ingestion,
        "_save_source_markdown",
        AsyncMock(return_value="user-1/source_markdown/doc-1.md"),
    )
    monkeypatch.setattr(
        ingestion,
        "_save_processed_json",
        AsyncMock(return_value="user-1/processed_json/doc-1.json"),
    )
    persist_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_persist_ingestion_metadata", persist_mock)
    monkeypatch.setattr(ingestion, "_store_hierarchy_metadata", AsyncMock())
    status_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", status_mock)
    monkeypatch.setattr(ingestion, "_require_owned_document", AsyncMock())

    async def fake_resolve(elements):
        return elements, SimpleNamespace(model_dump=lambda: {"sections": []})

    async def fake_chunk(elements, strategy, document_id):
        del strategy
        assert document_id == "doc-1"
        return [{"chunk_index": 0, "content": elements[0]["text"], "metadata": {}}]

    store_mock = AsyncMock()
    monkeypatch.setattr(
        "src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy",
        fake_resolve,
    )
    monkeypatch.setattr("src.lib.pipeline.chunk.chunk_parsed_document", fake_chunk)
    monkeypatch.setattr("src.lib.pipeline.store.store_to_weaviate", store_mock)

    result = await ingest_provider_markdown_document(
        ProviderMarkdownIngestionRequest(
            document_id="doc-1",
            user_id="user-1",
            document_owner_user_id=42,
            markdown="# Results\n\n<!-- page: 2 -->\nSignal from **B cells**.\n",
            source_provenance=_source_provenance(access_scope="Restricted"),
        ),
        weaviate_client=object(),
    )

    assert result.processing_result.success is True
    assert result.processing_result.stages_completed == [
        ProcessingStage.PARSING,
        ProcessingStage.CHUNKING,
        ProcessingStage.STORING,
    ]
    assert result.element_count == 2
    assert result.chunk_count == 1
    store_mock.assert_awaited_once()
    persist_mock.assert_awaited_once()
    persisted_call = persist_mock.await_args_list[-1]
    persisted_kwargs = persisted_call.kwargs
    assert persisted_kwargs["source_provenance"]["access_scope"] == "restricted"
    assert persisted_kwargs["owner_user_id"] == 42
    assert persisted_kwargs["source_markdown_path"] == "user-1/source_markdown/doc-1.md"
    assert status_mock.await_args_list[-1].kwargs["status"] == "completed"
    assert status_mock.await_args_list[-1].kwargs["user_id"] == "user-1"
    assert status_mock.await_args_list[-1].kwargs["owner_user_id"] == 42


@pytest.mark.asyncio
async def test_ingest_provider_markdown_document_requires_access_scope(monkeypatch):
    status_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", status_mock)

    with pytest.raises(DocumentSourceIngestionError, match="source access scope"):
        await ingest_provider_markdown_document(
            ProviderMarkdownIngestionRequest(
                document_id="doc-1",
                user_id="user-1",
                document_owner_user_id=42,
                markdown="# Results\n\nText.\n",
                source_provenance=_source_provenance(access_scope=None),
            ),
            weaviate_client=object(),
        )

    status_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("access_scope", ["unknown", "alliance"])
async def test_ingest_provider_markdown_document_rejects_unknown_access_scope(
    monkeypatch,
    access_scope,
):
    status_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", status_mock)

    with pytest.raises(DocumentSourceIngestionError, match="global or restricted"):
        await ingest_provider_markdown_document(
            ProviderMarkdownIngestionRequest(
                document_id="doc-1",
                user_id="user-1",
                document_owner_user_id=42,
                markdown="# Results\n\nText.\n",
                source_provenance=_source_provenance(access_scope=access_scope),
            ),
            weaviate_client=object(),
        )

    status_mock.assert_not_awaited()


def test_provider_markdown_ingestion_allows_global_without_mods() -> None:
    provenance = ingestion._require_ingestable_provenance(
        _source_provenance(access_scope="GLOBAL", access_mods=None)
    )

    assert provenance["access_scope"] == "global"


def test_render_provider_figure_metadata_appendix_uses_caption_as_legend() -> None:
    appendix = render_provider_figure_metadata_appendix(
        [
            {
                "metadata_artifact_id": "110",
                "display_name": "paper_image_001",
                "figure_label": "Figure 1",
                "figure_number": "1",
                "caption_text": "Fig. 1A shows wg expression in the wing disc.",
                "nearby_text": "Nearby result text.",
                "page_index": 2,
            }
        ]
    )

    assert "## Provider Figure Metadata" in appendix
    assert "### Provider Figure: Figure 1" in appendix
    assert "Metadata artifact: 110" in appendix
    assert "Legend:\nFig. 1A shows wg expression in the wing disc." in appendix
    assert "Nearby text:\nNearby result text." in appendix


def test_render_provider_figure_metadata_appendix_falls_back_to_nearby_text() -> None:
    appendix = render_provider_figure_metadata_appendix(
        [
            {
                "metadata_artifact_id": "111",
                "display_name": "paper_image_002",
                "figure_label": "Figure 2",
                "nearby_text": "Fig. 2 shows eve expression.",
            }
        ]
    )

    assert "### Provider Figure: Figure 2" in appendix
    assert "Legend:" not in appendix
    assert "Nearby text:\nFig. 2 shows eve expression." in appendix


def test_render_provider_figure_metadata_escapes_markdown_control_lines() -> None:
    appendix = render_provider_figure_metadata_appendix(
        [
            {
                "metadata_artifact_id": "112",
                "display_name": "paper_image_003",
                "figure_label": "Figure 3",
                "caption_text": "## Abstract\nFig. 3A shows expression.",
                "nearby_text": "<!-- page: 1 -->\nFig. 3B nearby.",
            }
        ]
    )

    assert "Legend:\n\\## Abstract\nFig. 3A shows expression." in appendix
    assert "Nearby text:\n\\<!-- page: 1 -->\nFig. 3B nearby." in appendix


def test_normalize_provider_figure_metadata_sidecar_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="not valid UTF-8 JSON"):
        normalize_provider_figure_metadata_sidecar(
            b"{not-json",
            metadata_artifact_id="bad-meta",
        )


def test_normalize_provider_figure_metadata_sidecar_rejects_empty_payload() -> None:
    with pytest.raises(ValueError, match="no indexable figure metadata"):
        normalize_provider_figure_metadata_sidecar(
            b'{"display_name":"   ","caption_text":" "}',
            metadata_artifact_id="empty-meta",
        )


def test_normalize_provider_figure_metadata_converts_zero_based_page_once() -> None:
    entry = normalize_provider_figure_metadata_sidecar(
        b'{"figure_label":"Figure 1","caption_text":"Legend.","page_index":2}',
        metadata_artifact_id="figure-meta-1",
    )

    assert entry["page_index"] == 2
    assert entry["page_number"] == 3


@pytest.mark.parametrize("page_index", [None, -1, True, 2.5, "2", "bad"])
def test_normalize_provider_figure_metadata_does_not_invent_invalid_page(
    page_index,
) -> None:
    raw = (
        '{"figure_label":"Figure 1","caption_text":"Legend.","page_index":'
        + json.dumps(page_index)
        + "}"
    ).encode()

    entry = normalize_provider_figure_metadata_sidecar(
        raw,
        metadata_artifact_id="figure-meta-invalid-page",
    )

    assert "page_index" not in entry
    assert "page_number" not in entry


@pytest.mark.asyncio
@pytest.mark.parametrize("page_field", ["", ',"page_index":"bad"'])
async def test_provider_figure_chunk_does_not_invent_absent_or_malformed_page(
    page_field,
) -> None:
    entry = normalize_provider_figure_metadata_sidecar(
        (
            '{"figure_label":"Figure 1","caption_text":"Provider legend."'
            + page_field
            + "}"
        ).encode(),
        metadata_artifact_id="figure-meta-unknown-page",
    )
    markdown = append_provider_figure_metadata_markdown("# Results\n\nText.", [entry])
    elements = markdown_to_pipeline_elements(markdown)
    apply_provider_figure_page_provenance(elements, [entry])

    chunks = await chunk_parsed_document(
        elements,
        ChunkingStrategy.get_research_strategy(),
        "doc-unknown-page",
    )
    provider_chunk = next(
        chunk for chunk in chunks if "Provider legend." in chunk.content
    )

    assert provider_chunk.page_number is None


@pytest.mark.asyncio
async def test_ingest_provider_markdown_document_indexes_provider_figure_metadata(
    monkeypatch,
) -> None:
    saved_markdown = {}

    async def fake_save_source_markdown(**kwargs):
        saved_markdown.update(kwargs)
        return "user-1/source_markdown/doc-1.md"

    monkeypatch.setattr(
        ingestion,
        "_save_source_markdown",
        fake_save_source_markdown,
    )
    monkeypatch.setattr(
        ingestion,
        "_save_processed_json",
        AsyncMock(return_value="user-1/processed_json/doc-1.json"),
    )
    monkeypatch.setattr(ingestion, "_persist_ingestion_metadata", AsyncMock())
    monkeypatch.setattr(ingestion, "_store_hierarchy_metadata", AsyncMock())
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", AsyncMock())
    monkeypatch.setattr(ingestion, "_require_owned_document", AsyncMock())

    captured_elements = {}

    async def fake_resolve(elements):
        captured_elements["before_hierarchy"] = elements
        return elements, SimpleNamespace(model_dump=lambda: {"sections": []})

    async def fake_chunk(elements, strategy, document_id):
        captured_elements["chunked"] = elements
        chunks = await chunk_parsed_document(elements, strategy, document_id)
        captured_elements["chunks"] = chunks
        return chunks

    monkeypatch.setattr(
        "src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy",
        fake_resolve,
    )
    monkeypatch.setattr("src.lib.pipeline.chunk.chunk_parsed_document", fake_chunk)
    monkeypatch.setattr("src.lib.pipeline.store.store_to_weaviate", AsyncMock())

    provider_metadata = normalize_provider_figure_metadata_sidecar(
        b'{"display_name":"paper_image_001","figure_label":"Figure 1",'
        b'"caption_text":"Fig. 1A shows wg expression in the wing disc.",'
        b'"page_index":2}',
        metadata_artifact_id="110",
    )

    result = await ingest_provider_markdown_document(
        ProviderMarkdownIngestionRequest(
            document_id="doc-1",
            user_id="user-1",
            document_owner_user_id=42,
            markdown="# Results\n\nNative result text.\n",
            source_provenance=_source_provenance(access_scope="Restricted"),
            provider_figure_metadata=(provider_metadata,),
        ),
        weaviate_client=object(),
    )

    assert result.processing_result.success is True
    assert saved_markdown["markdown"] == "# Results\n\nNative result text.\n"
    indexed_text = "\n".join(
        element["text"] for element in captured_elements["before_hierarchy"]
    )
    assert "Provider Figure Metadata" in indexed_text
    assert "Provider Figure: Figure 1" in indexed_text
    assert "Fig. 1A shows wg expression in the wing disc." in indexed_text

    provider_elements = [
        element
        for element in captured_elements["before_hierarchy"]
        if "Provider Figure: Figure 1"
        in element["metadata"].get("section_path", [])
    ]
    assert provider_elements
    assert {element["metadata"]["page_number"] for element in provider_elements} == {3}

    provider_chunk = next(
        chunk
        for chunk in captured_elements["chunks"]
        if "Fig. 1A shows wg expression in the wing disc." in chunk.content
    )
    assert provider_chunk.page_number == 3

    stored_chunk = provider_chunk.model_dump()
    stored_chunk["text"] = stored_chunk.pop("content")

    async def fake_get_chunk_by_id(**_kwargs):
        return stored_chunk

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", fake_get_chunk_by_id)
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)
    span = next(
        candidate
        for candidate in build_evidence_spans(
            chunk_id=provider_chunk.id,
            chunk_text=provider_chunk.content,
            page_number=provider_chunk.page_number,
            section_title=provider_chunk.section_title,
        )
        if "wg expression" in candidate.text
    )

    evidence = await record_evidence.create_record_evidence_tool(
        "doc-1",
        "user-1",
    )(entity="wg", span_ids=[span.span_id])

    assert evidence["status"] == "verified"
    assert evidence["page"] == 3
    assert evidence["source_fragments"][0]["page"] == 3


@pytest.mark.asyncio
async def test_require_owned_document_requires_matching_auth_sub(monkeypatch):
    import src.models.sql.database as database

    fake_session = SimpleNamespace(close=Mock())
    query_mock = Mock(return_value=None)
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(ingestion, "_query_owned_document", query_mock)

    with pytest.raises(DocumentSourceIngestionError, match="user mismatch"):
        await ingestion._require_owned_document("doc-1", "wrong-user", 42)

    query_mock.assert_called_once()
    assert query_mock.call_args.kwargs["document_id"] == "doc-1"
    assert query_mock.call_args.kwargs["user_id"] == "wrong-user"
    assert query_mock.call_args.kwargs["owner_user_id"] == 42
    fake_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_sync_sql_document_status_does_not_mutate_on_user_mismatch(monkeypatch):
    import src.models.sql.database as database

    fake_session = SimpleNamespace(close=Mock(), commit=Mock(), rollback=Mock())
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(ingestion, "_query_owned_document", Mock(return_value=None))

    await ingestion._sync_sql_document_status(
        "doc-1",
        user_id="wrong-user",
        owner_user_id=42,
        status="failed",
        error_message="nope",
    )

    fake_session.commit.assert_not_called()
    fake_session.rollback.assert_not_called()
    fake_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_provider_markdown_document_requires_restricted_mods(monkeypatch):
    status_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", status_mock)

    with pytest.raises(DocumentSourceIngestionError, match="source access MODs"):
        await ingest_provider_markdown_document(
            ProviderMarkdownIngestionRequest(
                document_id="doc-1",
                user_id="user-1",
                document_owner_user_id=42,
                markdown="# Results\n\nText.\n",
                source_provenance=_source_provenance(access_mods={"mods": []}),
            ),
            weaviate_client=object(),
        )

    status_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_provider_markdown_document_marks_validation_failure(monkeypatch):
    status_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", status_mock)
    monkeypatch.setattr(ingestion, "_require_owned_document", AsyncMock())
    monkeypatch.setattr(
        ingestion,
        "_validate_provider_markdown",
        lambda _markdown: (_ for _ in ()).throw(
            DocumentSourceMarkdownValidationError("bad markdown")
        ),
    )

    with pytest.raises(DocumentSourceMarkdownValidationError, match="bad markdown"):
        await ingest_provider_markdown_document(
            ProviderMarkdownIngestionRequest(
                document_id="doc-1",
                user_id="user-1",
                document_owner_user_id=42,
                markdown="# Results\n\nText.\n",
                source_provenance=_source_provenance(),
            ),
            weaviate_client=object(),
        )

    assert status_mock.await_args_list[-1].kwargs == {
        "user_id": "user-1",
        "owner_user_id": 42,
        "status": "failed",
        "error_message": "bad markdown",
    }


@pytest.mark.asyncio
async def test_ingest_provider_markdown_document_checks_owner_before_file_writes(
    monkeypatch,
) -> None:
    save_markdown_mock = AsyncMock()
    save_processed_mock = AsyncMock()
    status_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "_save_source_markdown", save_markdown_mock)
    monkeypatch.setattr(ingestion, "_save_processed_json", save_processed_mock)
    monkeypatch.setattr(ingestion, "_sync_sql_document_status", status_mock)
    monkeypatch.setattr(
        ingestion,
        "_require_owned_document",
        AsyncMock(
            side_effect=DocumentSourceIngestionError(
                "Document row not found for provider ingestion"
            )
        ),
    )

    with pytest.raises(DocumentSourceIngestionError, match="Document row not found"):
        await ingest_provider_markdown_document(
            ProviderMarkdownIngestionRequest(
                document_id="doc-1",
                user_id="user-1",
                document_owner_user_id=42,
                markdown="# Results\n\nText.\n",
                source_provenance=_source_provenance(),
            ),
            weaviate_client=object(),
        )

    save_markdown_mock.assert_not_awaited()
    save_processed_mock.assert_not_awaited()
    assert status_mock.await_args_list[-1].kwargs == {
        "user_id": "user-1",
        "owner_user_id": 42,
        "status": "failed",
        "error_message": "Document row not found for provider ingestion",
    }


def test_strip_markdown_image_assets_preserves_alt_text() -> None:
    markdown = "# Results\n\nText before ![Figure 1 phenotype](fig1.png) after.\n"

    assert ingestion._strip_markdown_image_assets(markdown) == (
        "# Results\n\nText before Figure 1 phenotype after.\n"
    )
