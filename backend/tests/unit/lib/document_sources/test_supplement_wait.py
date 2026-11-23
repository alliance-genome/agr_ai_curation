"""Classifying ABC's conversion answer for the supplements a curator chose (KANBAN-1976 / ALL-1460, P7w).

Every answer is ABC's own response shape at ``3d3ae96``
(``file_conversion_crud._status_payload``): ``per_file_progress`` entries of
the current job (``_job_progress_payload``, ``:378-401``) carry their source's
id; entries merged in from converted rows (``_db_derived_progress``,
``:468-496``) carry a source inferred from the display name, or ``None``. Each
answer goes through the real ABC adapter, and the listing through the real
file listing over ``show_all`` rows. All values are made up.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.lib.document_sources.file_reasons import FileReason
from src.lib.document_sources.models import (
    DocumentSourceError,
    SourceConversionFileStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.document_sources.supplement_wait import (
    CONVERTED,
    WAITING,
    UnclassifiableConversionAnswer,
    classify_conversion_answer,
)
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    FakeABCLiteratureClient,
    _abc_show_all_main_pdf,
    _abc_show_all_row,
    provider_from_fake,
)
from tests.unit.lib.document_sources.test_file_listing import GOOD_TABLE, _listing, _pair

NOT_CONVERTED = FileReason.PROVIDER_NOT_CONVERTED.value
FAILED = FileReason.PROVIDER_CONVERSION_FAILED.value


def _unconverted(referencefile_id: int, extension: str) -> dict[str, Any]:
    return _abc_show_all_row(
        referencefile_id, "WB", file_class="supplement", file_extension=extension,
        display_name=f"Made_up_S{referencefile_id}",
    )


def _job_entry(
    referencefile_id: int, file_class: str, status: str, *, error: str | None = None,
    converted_id: int | None = None,
) -> dict[str, Any]:
    """One entry of ABC's current job (``_job_progress_payload``)."""

    display_name = f"Made_up_S{referencefile_id}" if file_class == "supplement" else "Made_up_main"
    converted = None
    if converted_id is not None:
        converted = {
            "display_name": f"{display_name}_merged",
            "file_class": f"converted_merged_{'main' if file_class == 'main' else 'supplement'}",
            "referencefile_id": converted_id,
        }
    return {
        "source": {
            "display_name": display_name,
            "file_class": file_class,
            "referencefile_id": referencefile_id,
        },
        "converted": converted,
        "figures": [],
        "status": status,
        "error": error,
    }


def _merged_entry(converted_id: int, *, source_id: int | None, file_class: str) -> dict[str, Any]:
    """An entry ABC merges in from a converted row (``_db_derived_progress``)."""

    source = None
    if source_id is not None:
        source = {
            "display_name": f"Made_up_S{source_id}",
            "file_class": "supplement",
            "referencefile_id": source_id,
        }
    return {
        "source": source,
        "converted": {
            "display_name": f"Made_up_{converted_id}_merged",
            "file_class": file_class,
            "referencefile_id": converted_id,
        },
        "figures": [],
        "status": "success",
        "error": None,
    }


def _answer_payload(
    status: str, progress: list[dict[str, Any]], *, error_message: str | None = None
) -> dict[str, Any]:
    return {
        "reference_curie": "AGRKB:101",
        "status": status,
        "job_id": "made-up-job",
        "previous_job_id": None,
        "error_message": error_message,
        "started_at": "2026-11-22T10:00:00",
        "completed_at": None if status == "running" else "2026-11-22T10:20:00",
        "converted_classes": ["converted_merged_main"],
        "per_file_progress": progress,
        "per_mod_status": [],
    }


async def _answer(payload: dict[str, Any]):
    client = FakeABCLiteratureClient()
    client.conversion_payload = payload
    provider = provider_from_fake(client)
    result = await provider.request_conversion("AGRKB:101", request_bearer_token="curator-token")
    return provider, result


async def _classify(payload, rows, chosen, payloads=None):
    provider, answer = await _answer(payload)
    listing, _ = await _listing(rows, payloads=payloads or {})
    return classify_conversion_answer(
        answer, provider.conversion_file_statuses(answer), chosen, listing
    )


@pytest.mark.asyncio
async def test_files_of_a_running_job_are_waiting():
    payload = _answer_payload("running", [
        _job_entry(10, "main", "success", converted_id=11),
        _job_entry(30, "supplement", "pending"),
        _job_entry(40, "supplement", "pending"),
    ])
    rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf"), _unconverted(40, "xlsx")]

    assert await _classify(payload, rows, ["30", "40"]) == {"30": WAITING, "40": WAITING}


@pytest.mark.asyncio
async def test_a_file_converted_earlier_is_converted_by_the_listing_whatever_its_merged_entry():
    payload = _answer_payload("running", [
        _job_entry(30, "supplement", "pending"),
        # Converted in an earlier run: a merged entry with an inferred source.
        _merged_entry(21, source_id=20, file_class="converted_merged_supplement"),
        # A merged entry whose source ABC could not infer.
        _merged_entry(99, source_id=None, file_class="converted_merged_supplement"),
    ])
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "pdf")]

    result = await _classify(payload, rows, ["20", "30"], payloads={"21": GOOD_TABLE})

    assert result == {"20": CONVERTED, "30": WAITING}


@pytest.mark.asyncio
async def test_a_file_absent_from_a_final_converted_answer_was_not_converted():
    """A paper outside ABC's supplement groups: ABC answers converted at once."""

    payload = _answer_payload("converted", [
        _job_entry(10, "main", "success", converted_id=11),
    ])
    rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(60, "docx")]

    assert await _classify(payload, rows, ["60"]) == {"60": NOT_CONVERTED}


@pytest.mark.asyncio
async def test_a_failed_pdf_job_keeps_the_files_abc_converted():
    """One failed file fails ABC's whole job; ABC still stores the other files."""

    payload = _answer_payload("failed", [
        _job_entry(10, "main", "success", converted_id=11),
        _job_entry(30, "supplement", "failed", error="Supplemental PDF exceeds 5 MB limit"),
        _job_entry(50, "supplement", "success", converted_id=51),
        _job_entry(40, "supplement", "pending"),
    ], error_message="1 file failed")
    rows = [
        _abc_show_all_main_pdf(10, "WB"),
        _unconverted(30, "pdf"),
        *_pair(50, "WB"),
        _unconverted(40, "pdf"),
    ]

    result = await _classify(payload, rows, ["30", "50", "40"], payloads={"51": GOOD_TABLE})

    assert result == {"30": FAILED, "50": CONVERTED, "40": NOT_CONVERTED}


@pytest.mark.asyncio
async def test_a_failure_reported_while_the_job_still_runs_is_final_for_that_file():
    payload = _answer_payload("running", [
        _job_entry(30, "supplement", "failed", error="made-up error"),
        _job_entry(40, "supplement", "pending"),
    ])
    rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf"), _unconverted(40, "pdf")]

    assert await _classify(payload, rows, ["30", "40"]) == {"30": FAILED, "40": WAITING}


@pytest.mark.asyncio
async def test_an_inline_office_failure_names_no_file_so_the_file_was_not_converted():
    """ABC's Office-only path answers failed with free text and the progress of
    its most recent job (here an older one that converted the main paper); no
    entry names the Office file, so without reading the text it is not converted."""

    payload = _answer_payload("failed", [
        _job_entry(10, "main", "success", converted_id=11),
        _merged_entry(11, source_id=None, file_class="converted_merged_main"),
    ], error_message="Made_up_S40.docx: conversion error")
    rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(40, "docx")]

    assert await _classify(payload, rows, ["40"]) == {"40": NOT_CONVERTED}


@pytest.mark.asyncio
async def test_the_free_text_error_never_changes_the_result():
    def payload(error_text):
        return _answer_payload("failed", [
            _job_entry(30, "supplement", "failed", error=error_text),
        ], error_message=error_text)

    rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")]

    first = await _classify(payload("Supplemental PDF exceeds 5 MB limit"), rows, ["30"])
    second = await _classify(payload("Supplemental PDF has more than 20 pages"), rows, ["30"])
    assert first == second == {"30": FAILED}


@pytest.mark.asyncio
async def test_an_answer_that_is_neither_running_nor_final_is_refused():
    rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")]
    listing, _ = await _listing(rows)
    answer = SourceConversionResult(provider="abc_literature", status=SourceConversionStatus.UNKNOWN)

    with pytest.raises(UnclassifiableConversionAnswer):
        classify_conversion_answer(answer, {}, ["30"], listing)


@pytest.mark.asyncio
async def test_the_adapter_reads_statuses_by_source_id_and_a_failure_outranks_the_rest():
    provider, answer = await _answer(_answer_payload("running", [
        _merged_entry(31, source_id=30, file_class="converted_merged_supplement"),
        _job_entry(30, "supplement", "failed", error="made-up error"),
        _job_entry(40, "supplement", "pending"),
        _merged_entry(41, source_id=40, file_class="converted_merged_supplement"),
        _merged_entry(99, source_id=None, file_class="converted_merged_supplement"),
    ]))

    assert provider.conversion_file_statuses(answer) == {
        "30": SourceConversionFileStatus.FAILED,
        "40": SourceConversionFileStatus.PENDING,
    }


@pytest.mark.asyncio
async def test_the_adapter_refuses_a_per_file_status_abc_does_not_send():
    provider, answer = await _answer(_answer_payload("running", [
        {**_job_entry(30, "supplement", "pending"), "status": "skipped"},
    ]))

    with pytest.raises(DocumentSourceError):
        provider.conversion_file_statuses(answer)
