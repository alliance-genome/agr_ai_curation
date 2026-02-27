"""Unit tests for DocumentContext provider."""

from types import SimpleNamespace

from src.lib.document_context import DocumentContext


def test_fetch_uses_cached_metadata(monkeypatch):
    import src.lib.document_cache as document_cache
    import src.lib.openai_agents.agents.supervisor_agent as supervisor_agent
    import src.lib.openai_agents.prompt_utils as prompt_utils

    cached = SimpleNamespace(
        hierarchy={"sections": [{"name": "Introduction"}, {"name": None}]},
        abstract="Cached abstract",
    )
    calls = {"set_cache": 0}

    monkeypatch.setattr(document_cache, "get_cached_metadata", lambda *_args: cached)
    monkeypatch.setattr(
        document_cache, "set_cached_metadata", lambda *_args: calls.__setitem__("set_cache", calls["set_cache"] + 1)
    )

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("fetch should not run on cache hit")

    monkeypatch.setattr(supervisor_agent, "fetch_document_hierarchy_sync", _unexpected)
    monkeypatch.setattr(prompt_utils, "fetch_document_abstract_sync", _unexpected)

    ctx = DocumentContext.fetch("doc-1", "user-1", "paper.pdf")

    assert ctx.hierarchy == cached.hierarchy
    assert ctx.abstract == "Cached abstract"
    assert ctx.sections == ["Introduction"]
    assert ctx.document_name == "paper.pdf"
    assert calls["set_cache"] == 0


def test_fetch_cache_miss_fetches_and_caches(monkeypatch):
    import src.lib.document_cache as document_cache
    import src.lib.openai_agents.agents.supervisor_agent as supervisor_agent
    import src.lib.openai_agents.prompt_utils as prompt_utils

    calls = {}
    hierarchy = {"sections": [{"name": "Methods"}, {"name": "Results"}, {}]}

    monkeypatch.setattr(document_cache, "get_cached_metadata", lambda *_args: None)
    monkeypatch.setattr(
        supervisor_agent,
        "fetch_document_hierarchy_sync",
        lambda doc_id, user_id: hierarchy if (doc_id, user_id) == ("doc-1", "user-1") else None,
    )

    def _fetch_abstract(doc_id, user_id, hierarchy_arg):
        calls["abstract_args"] = (doc_id, user_id, hierarchy_arg)
        return "Fresh abstract"

    def _cache(user_id, doc_id, hierarchy_arg, abstract_arg):
        calls["cache_args"] = (user_id, doc_id, hierarchy_arg, abstract_arg)

    monkeypatch.setattr(prompt_utils, "fetch_document_abstract_sync", _fetch_abstract)
    monkeypatch.setattr(document_cache, "set_cached_metadata", _cache)

    ctx = DocumentContext.fetch("doc-1", "user-1")

    assert calls["abstract_args"] == ("doc-1", "user-1", hierarchy)
    assert calls["cache_args"] == ("user-1", "doc-1", hierarchy, "Fresh abstract")
    assert ctx.hierarchy == hierarchy
    assert ctx.abstract == "Fresh abstract"
    assert ctx.sections == ["Methods", "Results"]


def test_fetch_without_hierarchy_returns_empty_structure(monkeypatch):
    import src.lib.document_cache as document_cache
    import src.lib.openai_agents.agents.supervisor_agent as supervisor_agent
    import src.lib.openai_agents.prompt_utils as prompt_utils

    monkeypatch.setattr(document_cache, "get_cached_metadata", lambda *_args: None)
    monkeypatch.setattr(supervisor_agent, "fetch_document_hierarchy_sync", lambda *_args: None)
    monkeypatch.setattr(prompt_utils, "fetch_document_abstract_sync", lambda *_args: None)
    monkeypatch.setattr(document_cache, "set_cached_metadata", lambda *_args: None)

    ctx = DocumentContext.fetch("doc-1", "user-1")

    assert ctx.hierarchy is None
    assert ctx.abstract is None
    assert ctx.sections is None
    assert ctx.section_count() == 0
    assert ctx.has_structure() is False


def test_fetch_passes_user_and_document_to_cache_lookup(monkeypatch):
    import src.lib.document_cache as document_cache
    import src.lib.openai_agents.agents.supervisor_agent as supervisor_agent
    import src.lib.openai_agents.prompt_utils as prompt_utils

    calls = {}

    def _cache_lookup(user_id, document_id):
        calls.setdefault("lookups", []).append((user_id, document_id))
        return None

    monkeypatch.setattr(document_cache, "get_cached_metadata", _cache_lookup)
    monkeypatch.setattr(supervisor_agent, "fetch_document_hierarchy_sync", lambda *_args: None)
    monkeypatch.setattr(prompt_utils, "fetch_document_abstract_sync", lambda *_args: None)
    monkeypatch.setattr(document_cache, "set_cached_metadata", lambda *_args: None)

    DocumentContext.fetch("doc-1", "user-a")
    DocumentContext.fetch("doc-1", "user-b")

    assert calls["lookups"] == [("user-a", "doc-1"), ("user-b", "doc-1")]


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
