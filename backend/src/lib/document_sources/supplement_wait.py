"""Following a provider's conversion of the supplements a curator chose.

``classify_conversion_answer`` says, from one conversion answer and a fresh file
listing, where each chosen supplement stands. It reads only structured fields:
the listing's converted texts, the provider's per-file statuses (by provider
file id) and the answer's overall status. The provider's free-text errors are
never read, so a cause the provider states only in words (its group rule, its
size limits) is not known here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from src.lib.document_sources.file_listing import FileListing
from src.lib.document_sources.file_reasons import FileReason
from src.lib.document_sources.models import (
    SourceConversionFileStatus,
    SourceConversionResult,
    SourceConversionStatus,
)

# The listing has converted text for the file: it can be added.
CONVERTED = "converted"
# The provider is still working and has not reported on the file.
WAITING = "waiting"

_FINAL_ANSWERS = frozenset({
    SourceConversionStatus.CONVERTED,
    SourceConversionStatus.FAILED,
    SourceConversionStatus.NO_SOURCES,
})


class UnclassifiableConversionAnswer(ValueError):
    """The answer's overall status is neither running nor final."""


def classify_conversion_answer(
    answer: SourceConversionResult,
    file_statuses: Mapping[str, SourceConversionFileStatus],
    chosen_provider_file_ids: Iterable[str],
    listing: FileListing,
) -> dict[str, str]:
    """Per chosen supplement: ``converted``, ``waiting`` or the reason it is not converted.

    ``file_statuses`` is the provider's ``conversion_file_statuses(answer)``;
    ``listing`` is listed after ``answer`` was received.

    - ``converted``: the listing has converted text for the file.
    - ``provider_conversion_failed``: the provider reported a failure for the file.
    - ``waiting``: the provider is still running.
    - ``provider_not_converted``: the answer is final and the file has neither.
    """

    if answer.status is SourceConversionStatus.RUNNING:
        final = False
    elif answer.status in _FINAL_ANSWERS:
        final = True
    else:
        raise UnclassifiableConversionAnswer(
            f"Conversion answer status {answer.status.value!r} is neither running nor final"
        )
    converted = {
        listed.provider_file_id
        for listed in listing.files
        if listed.converted_text_id is not None
    }
    classified: dict[str, str] = {}
    for provider_file_id in chosen_provider_file_ids:
        if provider_file_id in converted:
            classified[provider_file_id] = CONVERTED
        elif file_statuses.get(provider_file_id) is SourceConversionFileStatus.FAILED:
            classified[provider_file_id] = FileReason.PROVIDER_CONVERSION_FAILED.value
        elif not final:
            classified[provider_file_id] = WAITING
        else:
            classified[provider_file_id] = FileReason.PROVIDER_NOT_CONVERTED.value
    return classified
