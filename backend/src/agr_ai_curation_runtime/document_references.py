"""Model selection types and the application-owned source binding bridge."""
from __future__ import annotations

from importlib import import_module
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

LocalIndex = Annotated[int, Field(strict=True, ge=0)]


class DocumentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_index: LocalIndex
    span_index: LocalIndex


def _implementation():
    return import_module("src.lib.openai_agents.document_references")


def scoped_document_tool(tool: Any, *, document_id: str, user_id: str) -> Any:
    """Bind the existing stored source to a server-owned conversation, not aliases."""
    invoke = tool.on_invoke_tool

    async def scoped_invoke(ctx, arguments):
        async with _implementation().document_reference_scope(document_id, user_id):
            return await invoke(ctx, arguments)

    tool.on_invoke_tool = scoped_invoke
    return tool


def current_readable_files() -> Any:
    """The readable files (``ReadableFiles``) of the document tool call in progress."""
    return _implementation().current_readable_files()


async def retain_issued_spans(reads: list[tuple[dict, list[dict], list[int]]]) -> None:
    """Issue the spans one read displayed, as ``(chunk, spans, issued_indexes)`` per chunk."""
    await _implementation().retain_issued_spans(reads)


async def resolve_selections(selections: list[DocumentSelection]) -> list[str]:
    return await _implementation().resolve_selections(selections)
