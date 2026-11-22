"""Unit tests for document metadata cache helpers."""

from src.lib import document_cache

ONE_FILE = (("main-file", 0, 41),)
WITH_SHEET = (("main-file", 0, 41), ("sheet-file", 42, 60))


def setup_function():
    """Reset module cache between tests."""
    document_cache._cache.clear()


def test_get_cached_metadata_returns_none_on_miss():
    assert document_cache.get_cached_metadata("user-1", "doc-1", ONE_FILE) is None


def test_set_then_get_cached_metadata_returns_hit(monkeypatch):
    monkeypatch.setattr(document_cache.time, "time", lambda: 1000.0)

    document_cache.set_cached_metadata(
        user_id="user-1",
        document_id="doc-1",
        hierarchy={"sections": [{"name": "Intro"}]},
        readable_fingerprint=ONE_FILE,
    )

    cached = document_cache.get_cached_metadata("user-1", "doc-1", ONE_FILE)
    assert cached is not None
    assert cached.hierarchy == {"sections": [{"name": "Intro"}]}


def test_cache_is_isolated_by_user_for_same_document(monkeypatch):
    monkeypatch.setattr(document_cache.time, "time", lambda: 1000.0)

    document_cache.set_cached_metadata(
        user_id="user-1",
        document_id="doc-1",
        hierarchy={"sections": [{"name": "User1"}]},
        readable_fingerprint=ONE_FILE,
    )
    document_cache.set_cached_metadata(
        user_id="user-2",
        document_id="doc-1",
        hierarchy={"sections": [{"name": "User2"}]},
        readable_fingerprint=ONE_FILE,
    )

    cached_user1 = document_cache.get_cached_metadata("user-1", "doc-1", ONE_FILE)
    cached_user2 = document_cache.get_cached_metadata("user-2", "doc-1", ONE_FILE)

    assert cached_user1 is not None and cached_user2 is not None
    assert cached_user1.hierarchy != cached_user2.hierarchy


def test_get_cached_metadata_removes_expired_entry(monkeypatch):
    key = ("user-1", "doc-1")
    document_cache._cache[key] = document_cache.CachedDocumentMetadata(
        hierarchy={"sections": []},
        readable_fingerprint=ONE_FILE,
        fetched_at=1000.0,
    )
    monkeypatch.setattr(
        document_cache.time,
        "time",
        lambda: 1000.0 + 600 + 1,
    )

    cached = document_cache.get_cached_metadata("user-1", "doc-1", ONE_FILE)

    assert cached is None
    assert key not in document_cache._cache


def test_invalidate_cache_is_idempotent():
    document_cache.set_cached_metadata(
        user_id="user-1",
        document_id="doc-1",
        hierarchy={"sections": []},
        readable_fingerprint=ONE_FILE,
    )
    assert ("user-1", "doc-1") in document_cache._cache

    document_cache.invalidate_cache("user-1", "doc-1")
    assert ("user-1", "doc-1") not in document_cache._cache

    # No-op when entry does not exist.
    document_cache.invalidate_cache("user-1", "doc-1")


def test_a_hierarchy_is_reused_only_for_the_same_readable_files(monkeypatch):
    """A batch (main file only) and a chat run, or a paper whose new file became ready, never share one."""
    monkeypatch.setattr(document_cache.time, "time", lambda: 1000.0)
    document_cache.set_cached_metadata(
        user_id="user-1", document_id="doc-1", hierarchy={"sections": [{"name": "Methods"}]},
        readable_fingerprint=ONE_FILE,
    )

    assert document_cache.get_cached_metadata("user-1", "doc-1", WITH_SHEET) is None
    assert document_cache.get_cached_metadata("user-1", "doc-1", ONE_FILE).hierarchy == {
        "sections": [{"name": "Methods"}],
    }
