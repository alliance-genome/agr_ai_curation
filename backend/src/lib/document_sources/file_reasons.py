"""The reason codes of a paper's files: one enum shared by the listing, the checks and the imports.

Every reason a file is refused, cannot be ticked, or was left out of an import is a
member of ``FileReason``. APIs return the codes; wording lives with the screens.
Values are stored (``document_file_assessments.reason_code``, job metadata) and
sent to the frontend, so a value is never renamed. Other parts add their codes
here, never in an enum of their own.

Each group below names the codes one consumer may write or read; a consumer
refuses a code outside its group with ``FileReasonGroupError`` instead of
reading it as something else.
"""

from __future__ import annotations

from enum import Enum


class FileReason(str, Enum):
    """Why a file is refused, not tickable, or not imported. Values are codes; never rename one."""

    # A check on a converted text refused it (P6; draft3 R1 to R7).
    TOO_LARGE = "too_large"                          # R1
    INVALID_TEXT = "invalid_text"                    # R2
    TABLE_CELLS_MISMATCH = "table_cells_mismatch"    # R4
    TABLE_ROW_TOO_WIDE = "table_row_too_wide"        # R5
    TABLE_STRAY_SEPARATOR = "table_stray_separator"  # R6
    TITLE_COUNT = "title_count"                      # R7

    # Why a listed file cannot be ticked (P6f).
    # The provider has no converted text for it, and this import would not ask for one.
    PROVIDER_NOT_CONVERTED_YET = "provider_not_converted_yet"
    LARGE_DATA_FILE = "large_data_file"
    TYPE_NOT_SUPPORTED = "type_not_supported"
    # Its converted text did not pass its check, or was not checked; the entry's check says which.
    CHECK_NOT_PASSED = "check_not_passed"
    ALREADY_IN_PAPER = "already_in_paper"
    # An import of this identifier would end with an error for the whole paper (no main
    # file, access denied, an ambiguous main, a failed conversion); ``main_status`` says which.
    PAPER_NOT_IMPORTABLE = "paper_not_importable"
    # The curator already has this paper: an import answers ``duplicate``; files are added
    # through that paper's own listing (``existing_document_id``).
    PAPER_ALREADY_IN_LIBRARY = "paper_already_in_library"

    # What the provider last told an import about a chosen file (P7b writes, P6f reads).
    # The provider's answer was final and the file had neither converted text nor a failure.
    PROVIDER_NOT_CONVERTED = "provider_not_converted"
    # The provider reported that it tried and could not convert the file.
    PROVIDER_CONVERSION_FAILED = "provider_conversion_failed"
    # The import stopped waiting before the provider had converted the file.
    NOT_CONVERTED_IN_TIME = "not_converted_in_time"


class SupplementState(str, Enum):
    """The state of one chosen supplement in a job's ``metadata.supplements`` (interface I1).

    Shared by every writer (imports, adds) and the listing that reads them back.
    """

    QUEUED = "queued"
    WAITING = "waiting"
    ADDING = "adding"
    ADDED = "added"
    LEFT_OUT = "left_out"


class FileReasonGroupError(ValueError):
    """A code was read or written where its group is not allowed."""


CHECK_REASONS: frozenset[FileReason] = frozenset({
    FileReason.TOO_LARGE,
    FileReason.INVALID_TEXT,
    FileReason.TABLE_CELLS_MISMATCH,
    FileReason.TABLE_ROW_TOO_WIDE,
    FileReason.TABLE_STRAY_SEPARATOR,
    FileReason.TITLE_COUNT,
})

NOT_TICKABLE_REASONS: frozenset[FileReason] = frozenset({
    FileReason.PROVIDER_NOT_CONVERTED_YET,
    FileReason.LARGE_DATA_FILE,
    FileReason.TYPE_NOT_SUPPORTED,
    FileReason.CHECK_NOT_PASSED,
    FileReason.ALREADY_IN_PAPER,
    FileReason.PAPER_NOT_IMPORTABLE,
    FileReason.PAPER_ALREADY_IN_LIBRARY,
})

PROVIDER_OBSERVATION_REASONS: frozenset[FileReason] = frozenset({
    FileReason.PROVIDER_NOT_CONVERTED,
    FileReason.PROVIDER_CONVERSION_FAILED,
    FileReason.NOT_CONVERTED_IN_TIME,
})


def reason_in_group(code: str, group: frozenset[FileReason], *, group_name: str) -> FileReason:
    """Read ``code`` as a member of ``group``; any other code is a named error."""

    try:
        reason = FileReason(code)
    except ValueError:
        raise FileReasonGroupError(f"Unknown file reason code {code!r}") from None
    if reason not in group:
        raise FileReasonGroupError(f"File reason {reason.value!r} is not one of the {group_name}")
    return reason
