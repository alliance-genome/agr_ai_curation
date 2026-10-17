"""Required source-read failures shared by package tools and application runners."""
from __future__ import annotations

from builtins import BaseExceptionGroup


class DocumentReadError(RuntimeError):
    """A required PDF read failed; callers must stop, not invent missing evidence.

    Keep the original exception as the cause for sanitized diagnostics. The
    public message contains no source text, identifiers, or provider payloads.
    """

    def __init__(self, tool_name: str = "document_read") -> None:
        self.tool_name = tool_name
        super().__init__("Could not read required PDF content.")


def document_read_failure(error: BaseException) -> DocumentReadError | None:
    """Find the typed failure through SDK/runner exception wrappers."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, DocumentReadError):
            return current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None:
            pending.append(current.__context__)
    return None


def raise_if_document_read_failed(error: BaseException) -> None:
    """Do not downgrade source failure to a recoverable validator finding."""
    if document_read_failure(error) is not None:
        raise error
