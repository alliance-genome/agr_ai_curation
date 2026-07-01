"""Shared PDF upload and processing limits."""

from src.lib.openai_agents.config import get_pdf_max_file_size_bytes

# Env-configurable via PDF_MAX_FILE_SIZE_BYTES (default 500 MB); see config.py.
MAX_PDF_FILE_SIZE_BYTES = get_pdf_max_file_size_bytes()
# Derived MB value (kept in sync with the byte limit; not separately configurable).
MAX_PDF_FILE_SIZE_MB = MAX_PDF_FILE_SIZE_BYTES // (1024 * 1024)


def pdf_file_size_limit_message(file_size_bytes: int | None = None) -> str:
    """Return a consistent validation message for oversized PDFs."""
    if file_size_bytes is None:
        return f"PDF file size exceeds the maximum allowed ({MAX_PDF_FILE_SIZE_MB} MB)."

    file_size_mb = file_size_bytes / (1024 * 1024)
    return (
        f"PDF file size ({file_size_mb:.2f} MB) exceeds the maximum allowed "
        f"({MAX_PDF_FILE_SIZE_MB} MB)."
    )
