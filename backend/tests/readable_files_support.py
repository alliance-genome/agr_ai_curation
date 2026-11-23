"""Readable-file values for tests of the document read functions (ALL-1460, P8a).

The shapes match what ``readable_files_in_session`` returns: a ready main file
whose pieces start at 0, then supplements in tab order after it, each with its
kind and the label the model sees.
"""

from __future__ import annotations

from src.lib.document_files.readable import ReadableFile, ReadableFiles, file_kind, file_labels


def _kind(name: str) -> str:
    extension = name.rsplit(".", 1)[-1] if "." in name else None
    return file_kind("local_pdf" if extension == "pdf" else "text", extension)


def single_file(document_id: str, chunk_count: int = 100_000, *, name: str = "paper.pdf") -> ReadableFiles:
    """A one-file paper (every paper before supplements): pieces 0..chunk_count-1."""

    return ReadableFiles(document_id, (ReadableFile(
        "main-file", name, "main", 0, chunk_count - 1, _kind(name), name, _kind(name) == "PDF",
    ),))


def files(document_id: str, *ranges: tuple[str, int, int], converted: tuple[str, ...] = ()) -> ReadableFiles:
    """Readable files from ``(display_name, start, end)``; the first is the main file.

    A PDF has real pages (an upload read by the extraction service) unless it is named
    in ``converted`` (its pieces came from provider-converted text); text files never do.
    """

    labels = file_labels([name for name, _start, _end in ranges])
    return ReadableFiles(document_id, tuple(
        ReadableFile(f"file-{position}", name, "main" if position == 0 else "supplement", start, end,
                     _kind(name), label, _kind(name) == "PDF" and name not in converted)
        for position, ((name, start, end), label) in enumerate(zip(ranges, labels))
    ))
