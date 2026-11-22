"""Readable-file values for tests of the document read functions (ALL-1460, P8a).

The shapes match what ``readable_files_in_session`` returns: a ready main file
whose pieces start at 0, then supplements in tab order after it.
"""

from __future__ import annotations

from src.lib.document_files.readable import ReadableFile, ReadableFiles


def single_file(document_id: str, chunk_count: int = 100_000, *, name: str = "paper.pdf") -> ReadableFiles:
    """A one-file paper (every paper before supplements): pieces 0..chunk_count-1."""

    return ReadableFiles(document_id, (ReadableFile("main-file", name, "main", 0, chunk_count - 1),))


def files(document_id: str, *ranges: tuple[str, int, int]) -> ReadableFiles:
    """Readable files from ``(display_name, start, end)``; the first is the main file."""

    return ReadableFiles(document_id, tuple(
        ReadableFile(f"file-{position}", name, "main" if position == 0 else "supplement", start, end)
        for position, (name, start, end) in enumerate(ranges)
    ))
