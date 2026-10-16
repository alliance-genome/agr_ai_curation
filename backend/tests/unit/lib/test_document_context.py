"""Async preparation, cache lifecycle and optional-context contracts."""

import asyncio
from contextvars import ContextVar
from unittest.mock import AsyncMock, Mock

import pytest

from src.lib import document_cache
from src.lib.document_context import DocumentContext


@pytest.fixture(autouse=True)
def cache():
    document_cache._cache.clear()
    document_cache._pending_fills.clear()
    yield
    assert not document_cache._pending_fills
    document_cache._cache.clear()


@pytest.fixture
def retrieval(monkeypatch):
    hierarchy = AsyncMock(return_value={"sections": [{"name": "Methods"}, {}], "abstract_section_title": "Synopsis"})
    abstract = AsyncMock(return_value="Abstract")
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_document_sections_hierarchical", hierarchy)
    monkeypatch.setattr("src.lib.openai_agents.prompt_utils.fetch_document_abstract", abstract)
    return hierarchy, abstract


@pytest.mark.asyncio
async def test_cold_warm_expired_and_tenant_cache(retrieval, monkeypatch):
    hierarchy, abstract = retrieval
    first = await DocumentContext.fetch("doc", "user", "paper.pdf")
    assert first.document_name == "paper.pdf" and first.sections == ["Methods"]
    abstract.assert_awaited_once_with("doc", "user", first.hierarchy)
    warm = await DocumentContext.fetch("doc", "user", "new.pdf")
    assert warm.document_name == "new.pdf" and warm.abstract == first.abstract
    hierarchy.assert_awaited_once()
    await DocumentContext.fetch("doc", "other")
    assert hierarchy.await_count == 2
    monkeypatch.setenv("DOCUMENT_CONTEXT_CACHE_TTL_SECONDS", "1")
    document_cache._cache[("user", "doc")].fetched_at -= 2
    await DocumentContext.fetch("doc", "user")
    assert hierarchy.await_count == 3


@pytest.mark.asyncio
async def test_negative_cache(retrieval):
    hierarchy, abstract = retrieval
    hierarchy.return_value = abstract.return_value = None
    result = await DocumentContext.fetch("doc", "user")
    assert result.sections is None and not result.has_structure()
    await DocumentContext.fetch("doc", "user")
    hierarchy.assert_awaited_once()
    abstract.assert_awaited_once_with("doc", "user", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [0, 1])
async def test_delayed_stage_yields_and_cancellation_never_publishes(retrieval, stage):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        entered.set()
        await release.wait()

    retrieval[stage].side_effect = delayed
    task = asyncio.create_task(DocumentContext.fetch("doc", "user"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # The observer can run while retrieval remains blocked on its barrier.
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert document_cache.get_cached_metadata("user", "doc") is None
        if stage == 0:
            retrieval[1].assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("capture_fails", [False, True])
async def test_partial_failure_timeout_and_sanitized_reporting(retrieval, monkeypatch, caplog, stage, timeout, capture_fails):
    report = Mock(side_effect=RuntimeError("SECRET telemetry") if capture_fails else None)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    monkeypatch.setenv("DOCUMENT_CONTEXT_STAGE_TIMEOUT_SECONDS", "0.02")

    async def failure(*args):
        if timeout:
            await asyncio.Event().wait()
        raise RuntimeError("SECRET query")

    retrieval[stage].side_effect = failure
    result = await asyncio.wait_for(DocumentContext.fetch("doc", "user"), 5)
    if stage == 0:
        assert result.hierarchy is None and result.abstract == "Abstract"
        retrieval[1].assert_awaited_once_with("doc", "user", None)
    else:
        assert result.hierarchy and result.abstract is None
    report.assert_called_once()
    assert "SECRET" not in caplog.text + str(report.call_args)
    exc = report.call_args.args[0]
    assert exc.__cause__ is None and exc.__context__ is None


@pytest.mark.asyncio
async def test_invalidation_revokes_old_fill_but_allows_new_fill(retrieval):
    entered, release = asyncio.Event(), asyncio.Event()

    async def old(*args):
        entered.set()
        await release.wait()
        return "obsolete"

    retrieval[1].side_effect = old
    task = asyncio.create_task(DocumentContext.fetch("doc", "user"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        document_cache.invalidate_cache("user", "doc")
        retrieval[1].side_effect = None
        retrieval[1].return_value = "fresh"
        await DocumentContext.fetch("doc", "user")
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    cached = document_cache.get_cached_metadata("user", "doc")
    assert cached is not None and cached.abstract == "fresh"


@pytest.mark.asyncio
async def test_concurrent_misses_are_independent_and_keep_context(retrieval):
    run = ContextVar("run")
    arrived, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def abstract(doc, user, hierarchy):
        seen.append((user, run.get()))
        if len(seen) == 3:
            arrived.set()
        await release.wait()
        return run.get()

    retrieval[1].side_effect = abstract
    async def prepare(user, value):
        token = run.set(value)
        try:
            return await DocumentContext.fetch("doc", user)
        finally:
            run.reset(token)

    tasks = [asyncio.create_task(prepare(user, value)) for user, value in [("one", "a"), ("one", "b"), ("two", "c")]]
    try:
        await asyncio.wait_for(arrived.wait(), 5)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert not tasks[1].done()
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
    assert all(isinstance(r, DocumentContext) for r in results[1:])
    assert [r.abstract for r in results[1:] if isinstance(r, DocumentContext)] == ["b", "c"]
    assert seen == [("one", "a"), ("one", "b"), ("two", "c")]


def test_document_context_helper_methods():
    ctx = DocumentContext(
        document_id="doc-1",
        user_id="user-1",
        document_name="paper.pdf",
        hierarchy={"sections": [{"name": "One"}, {"name": "Two"}]},
        abstract="A",
        sections=["One", "Two"],
    )

    assert ctx.to_agent_kwargs() == {
        "document_id": "doc-1",
        "user_id": "user-1",
        "document_name": "paper.pdf",
        "hierarchy": {"sections": [{"name": "One"}, {"name": "Two"}]},
        "abstract": "A",
        "sections": ["One", "Two"],
    }
    assert ctx.has_structure() is True
    assert ctx.section_count() == 2

    ctx_only_hierarchy = DocumentContext(
        document_id="doc-2",
        user_id="user-2",
        hierarchy={"sections": [{"name": "Only"}]},
        sections=None,
    )
    assert ctx_only_hierarchy.has_structure() is True
    assert ctx_only_hierarchy.section_count() == 1


@pytest.mark.parametrize("name, getter, default", [
    ("DOCUMENT_CONTEXT_STAGE_TIMEOUT_SECONDS", "get_document_context_stage_timeout_seconds", 10),
    ("DOCUMENT_CONTEXT_CACHE_TTL_SECONDS", "get_document_context_cache_ttl_seconds", 600),
])
@pytest.mark.parametrize("value", [None, "nan", "inf", "-1", "0", "invalid", "25.5"])
def test_preparation_limits(monkeypatch, name, getter, default, value):
    from src.lib.openai_agents import config
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert getattr(config, getter)() == (25.5 if value == "25.5" else default)
