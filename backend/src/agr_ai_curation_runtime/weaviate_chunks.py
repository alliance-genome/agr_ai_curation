"""Public Weaviate chunk helpers for package-owned document tools."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _load_chunks_module() -> Any:
    """Resolve the backend Weaviate chunk implementation lazily."""
    return import_module("src.lib.weaviate_client.chunks")


async def hybrid_search_chunks(*args: Any, **kwargs: Any) -> Any:
    return await _load_chunks_module().hybrid_search_chunks(*args, **kwargs)


async def get_document_sections(*args: Any, **kwargs: Any) -> Any:
    return await _load_chunks_module().get_document_sections(*args, **kwargs)


async def get_chunks_by_parent_section(*args: Any, **kwargs: Any) -> Any:
    return await _load_chunks_module().get_chunks_by_parent_section(*args, **kwargs)


async def get_chunks_by_subsection(*args: Any, **kwargs: Any) -> Any:
    return await _load_chunks_module().get_chunks_by_subsection(*args, **kwargs)


__all__ = [
    "get_chunks_by_parent_section",
    "get_chunks_by_subsection",
    "get_document_sections",
    "hybrid_search_chunks",
]
