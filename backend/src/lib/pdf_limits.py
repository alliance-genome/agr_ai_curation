"""Shared PDF upload and processing limits."""

MAX_PDF_FILE_SIZE_BYTES = 100 * 1024 * 1024
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
