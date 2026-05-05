"""Unit tests for prompt utility helpers."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

PROMPT_UTILS_PATH = Path(__file__).resolve().parents[4] / "src/lib/openai_agents/prompt_utils.py"
_spec = spec_from_file_location("prompt_utils_under_test", PROMPT_UTILS_PATH)
assert _spec is not None and _spec.loader is not None
prompt_utils = module_from_spec(_spec)
_spec.loader.exec_module(prompt_utils)

format_document_context_for_prompt = prompt_utils.format_document_context_for_prompt
format_hierarchy_for_prompt = prompt_utils.format_hierarchy_for_prompt
format_sections_for_prompt = prompt_utils.format_sections_for_prompt
inject_structured_output_instruction = prompt_utils.inject_structured_output_instruction


class DummyEnvelope:
    """Simple dummy output type for testing name extraction."""


def test_inject_structured_output_instruction_returns_original_without_type():
    instructions = "## Section\nBody"

    result = inject_structured_output_instruction(instructions)

    assert result == instructions


def test_inject_structured_output_instruction_prepends_when_requested():
    instructions = "## Intro\nBody"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type=DummyEnvelope,
        insert_after_first_section=False,
    )

    assert "DummyEnvelope structured output" in result
    assert result.endswith(instructions)


def test_inject_structured_output_instruction_inserts_before_second_section():
    instructions = "## First\nLine A\nLine B\n## Second\nTail"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type_name="TestSchema",
    )

    instruction_idx = result.index("## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON")
    second_section_idx = result.index("## Second")

    assert result.index("## First") < instruction_idx
    assert result.index("Line B") < instruction_idx
    assert instruction_idx < second_section_idx
    assert "TestSchema structured output" in result


def test_inject_structured_output_instruction_falls_back_to_prepend_without_second_section():
    instructions = "## Only Section\nBody"

    result = inject_structured_output_instruction(
        instructions=instructions,
        output_type_name="FallbackSchema",
    )

    assert result.endswith(instructions)
    assert result.index("## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON") < result.index(
        "## Only Section"
    )


def test_format_hierarchy_for_prompt_returns_empty_when_missing_sections():
    assert format_hierarchy_for_prompt({}) == ""
    assert format_hierarchy_for_prompt({"sections": []}) == ""


def test_format_hierarchy_for_prompt_formats_top_level_and_subsections():
    hierarchy = {
        "sections": [
            {
                "name": "Methods",
                "page_numbers": [2, 4],
                "chunk_count": 3,
                "subsections": [
                    {"name": "Fly Strains", "page_numbers": [3], "chunk_count": 1}
                ],
            }
        ],
        "top_level_sections": ["Methods"],
    }

    result = format_hierarchy_for_prompt(hierarchy)

    assert "## Document Structure" in result
    assert "**Methods** (p.2-4, 3 chunks)" in result
    assert "  └─ Fly Strains (p.3, 1 chunks)" in result
    assert "**Top-level sections (in order):** Methods" in result


def test_format_hierarchy_for_prompt_uses_unknown_when_top_level_missing():
    hierarchy = {
        "sections": [{"name": "Introduction", "page_numbers": [], "chunk_count": 0}],
    }

    result = format_hierarchy_for_prompt(hierarchy)

    assert "**Top-level sections (in order):** Unknown" in result


def test_format_sections_for_prompt_returns_empty_when_no_sections():
    assert format_sections_for_prompt([]) == ""


def test_format_sections_for_prompt_formats_entries_with_defaults():
    sections = [
        {"title": "Introduction", "page_numbers": [1], "chunk_count": 2},
        {"page_numbers": [5, 7]},
    ]

    result = format_sections_for_prompt(sections)

    assert "## Document Sections" in result
    assert "- **Introduction** (p.1, 2 chunks)" in result
    assert "- **Unknown** (p.5-7, 0 chunks)" in result


def test_format_document_context_for_prompt_prefers_hierarchy_and_adds_abstract():
    hierarchy = {
        "sections": [
            {"name": "Results", "page_numbers": [8], "chunk_count": 2, "subsections": []}
        ],
        "top_level_sections": ["Results"],
    }
    sections = [{"title": "Fallback", "page_numbers": [1], "chunk_count": 1}]

    context_text, structure_info = format_document_context_for_prompt(
        hierarchy=hierarchy,
        sections=sections,
        abstract="  A short abstract.  ",
    )

    assert "## Document Structure" in context_text
    assert "## Document Sections" not in context_text
    assert "## Paper Abstract" in context_text
    assert "A short abstract." in context_text
    assert structure_info == "hierarchy with 1 sections + abstract"


def test_format_document_context_for_prompt_uses_sections_when_no_hierarchy():
    context_text, structure_info = format_document_context_for_prompt(
        hierarchy={"sections": []},
        sections=[{"title": "Only Section", "page_numbers": [2], "chunk_count": 1}],
        abstract=None,
    )

    assert "## Document Sections" in context_text
    assert "## Document Structure" not in context_text
    assert structure_info == "1 flat sections"


def test_format_document_context_for_prompt_handles_no_structure_or_abstract():
    context_text, structure_info = format_document_context_for_prompt(
        hierarchy=None,
        sections=[],
        abstract="   ",
    )

    assert context_text == ""
    assert structure_info == "no structure"


def test_format_abstract_for_prompt_returns_empty_for_blank_values():
    assert prompt_utils.format_abstract_for_prompt(None) == ""
    assert prompt_utils.format_abstract_for_prompt("   ") == ""


def test_format_abstract_for_prompt_trims_and_formats_text():
    rendered = prompt_utils.format_abstract_for_prompt("  concise abstract  ")
    assert "## Paper Abstract" in rendered
    assert "concise abstract" in rendered


@pytest.mark.asyncio
async def test_extract_abstract_with_llm_omits_temperature_for_gpt5(monkeypatch):
    captured = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="A" * 80))]
            )

    class _FakeAsyncOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI))
    monkeypatch.setenv("ABSTRACT_EXTRACTION_MODEL", "gpt-5.4-mini")

    abstract = await prompt_utils._extract_abstract_with_llm("raw text for abstract extraction")

    assert abstract == "A" * 80
    assert captured["model"] == "gpt-5.4-mini"
    assert "temperature" not in captured


@pytest.mark.asyncio
async def test_extract_abstract_with_llm_sets_temperature_for_non_gpt5(monkeypatch):
    captured = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="B" * 90))]
            )

    class _FakeAsyncOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI))
    monkeypatch.setenv("ABSTRACT_EXTRACTION_MODEL", "gpt-4o-mini")

    abstract = await prompt_utils._extract_abstract_with_llm("raw text for abstract extraction")

    assert abstract == "B" * 90
    assert captured["model"] == "gpt-4o-mini"
    assert captured["temperature"] == 0


@pytest.mark.asyncio
async def test_extract_abstract_with_llm_returns_none_for_short_or_missing_output(monkeypatch):
    class _FakeCompletionsNone:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
            )

    class _FakeAsyncOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletionsNone())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI))
    monkeypatch.setenv("ABSTRACT_EXTRACTION_MODEL", "gpt-5.4-mini")

    assert await prompt_utils._extract_abstract_with_llm("raw text") is None


@pytest.mark.asyncio
async def test_fetch_document_abstract_prefers_llm_identified_section(monkeypatch):
    seen_sections = []

    async def _get_chunks_by_parent_section(document_id, parent_section, user_id):
        seen_sections.append(parent_section)
        if parent_section == "Executive Summary":
            return [{"text": "Alpha"}, {"text": "Beta"}]
        return []

    async def _unused_keyword_search(**_kwargs):
        raise AssertionError("keyword search should not be called when section lookup succeeds")

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_by_parent_section",
        _get_chunks_by_parent_section,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.search_chunks_by_keyword",
        _unused_keyword_search,
    )

    result = await prompt_utils.fetch_document_abstract(
        document_id="doc-1",
        user_id="user-1",
        hierarchy={"abstract_section_title": "Executive Summary"},
    )

    assert result == "Alpha Beta"
    assert seen_sections == ["Executive Summary"]


@pytest.mark.asyncio
async def test_fetch_document_abstract_keyword_path_returns_llm_extracted_value(monkeypatch):
    async def _empty_section_fetch(**_kwargs):
        return []

    async def _keyword_search(**kwargs):
        assert kwargs["keyword"] == "abstract"
        return [{"text": "header abstract long body", "chunk_index": 12}]

    async def _chunks_from_index(**kwargs):
        assert kwargs["start_index"] == 12
        return [{"text": "Chunk A"}, {"text": "Chunk B"}]

    async def _extract(_text):
        return "clean extracted abstract text"

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_by_parent_section",
        _empty_section_fetch,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.search_chunks_by_keyword",
        _keyword_search,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_from_index",
        _chunks_from_index,
    )
    monkeypatch.setattr(prompt_utils, "_extract_abstract_with_llm", _extract)

    result = await prompt_utils.fetch_document_abstract(
        document_id="doc-2",
        user_id="user-2",
    )

    assert result == "clean extracted abstract text"


@pytest.mark.asyncio
async def test_fetch_document_abstract_keyword_path_falls_back_to_combined_text(monkeypatch):
    async def _empty_section_fetch(**_kwargs):
        return []

    async def _keyword_search(**_kwargs):
        return [{"text": "intro ABSTRACT details", "chunk_index": 4}]

    async def _chunks_from_index(**kwargs):
        assert kwargs["start_index"] == 4
        return [{"text": "Chunk One"}, {"text": "Chunk Two"}]

    async def _no_extract(_text):
        return None

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_by_parent_section",
        _empty_section_fetch,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.search_chunks_by_keyword",
        _keyword_search,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_from_index",
        _chunks_from_index,
    )
    monkeypatch.setattr(prompt_utils, "_extract_abstract_with_llm", _no_extract)

    result = await prompt_utils.fetch_document_abstract(
        document_id="doc-3",
        user_id="user-3",
    )

    assert result == "Chunk One Chunk Two"


@pytest.mark.asyncio
async def test_fetch_document_abstract_uses_last_resort_first_chunks(monkeypatch):
    async def _empty_section_fetch(**_kwargs):
        return []

    async def _empty_keyword(**_kwargs):
        return []

    async def _chunks_from_index(**kwargs):
        if kwargs["start_index"] == 0:
            return [{"text": "First chunk"}, {"text": "Second chunk"}]
        return []

    async def _extract(_text):
        return "last resort extracted abstract"

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_by_parent_section",
        _empty_section_fetch,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.search_chunks_by_keyword",
        _empty_keyword,
    )
    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_chunks_from_index",
        _chunks_from_index,
    )
    monkeypatch.setattr(prompt_utils, "_extract_abstract_with_llm", _extract)

    result = await prompt_utils.fetch_document_abstract(
        document_id="doc-4",
        user_id="user-4",
    )

    assert result == "last resort extracted abstract"


def test_fetch_document_abstract_sync_handles_missing_event_loop(monkeypatch):
    import asyncio

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: (_ for _ in ()).throw(RuntimeError()))

    def _fake_run(coro):
        coro.close()
        return "sync abstract result"

    monkeypatch.setattr(asyncio, "run", _fake_run)

    result = prompt_utils.fetch_document_abstract_sync("doc", "user")
    assert result == "sync abstract result"


def test_fetch_document_abstract_sync_returns_none_on_unexpected_error(monkeypatch):
    import asyncio
    import concurrent.futures

    class _RunningLoop:
        @staticmethod
        def is_running():
            return True

    class _BrokenPool:
        def __enter__(self):
            raise RuntimeError("boom")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _RunningLoop())
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", lambda: _BrokenPool())

    result = prompt_utils.fetch_document_abstract_sync("doc", "user")
    assert result is None
