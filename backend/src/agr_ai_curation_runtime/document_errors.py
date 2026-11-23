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


class DocumentFilesChanged(Exception):
    """The paper's readable files changed during one tool call, but nothing the call
    showed came from a file that stopped being readable.

    Recoverable: the call answers the model with ``FILES_CHANGED_ANSWER`` (its result
    is withheld and nothing it staged is saved) and the run goes on; the model's next
    read uses the current files.
    """


FILES_CHANGED_ERROR_CODE = "readable_files_changed"

# Proposed wording (for Chris's sign-off). Keep in step with the outline's
# "Files are still being added to this paper; they cannot be read yet."
FILES_CHANGED_MESSAGE = "This paper's files changed while reading; read again to use the current files."
# record_evidence's other refusals carry "saved": false and begin "Nothing was saved."
FILES_CHANGED_RECORD_MESSAGE = (
    "Nothing was saved. This paper's files changed while recording; read the sentences again, "
    "then record them."
)


def files_changed_answer(tool_name: str) -> dict[str, object]:
    if tool_name == "record_evidence":
        return {"status": "files_changed", "error_code": FILES_CHANGED_ERROR_CODE, "saved": False,
                "message": FILES_CHANGED_RECORD_MESSAGE}
    return {"status": "files_changed", "error_code": FILES_CHANGED_ERROR_CODE, "message": FILES_CHANGED_MESSAGE}


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
