"""Which listed files can be ticked, and the last thing the provider told an import (P6f).

Rows are ABC ``show_all`` shapes through the real Alliance adapter (as in
``test_file_listing``). ABC never lists a main text as running or failed (its rows
carry only ``file_publication_status``), so those two branches of the import
decision are tested through core with provider-neutral decisions. All values are
made up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.lib.document_sources.file_listing import (
    FileListingError,
    ListedFileCheckStatus,
    ListingRoute,
    ProviderObservation,
    list_reference_files,
    listing_to_payload,
    will_ask_provider,
)
from src.lib.document_sources.file_reasons import (
    NOT_TICKABLE_REASONS,
    FileReason,
    FileReasonGroupError,
)
from src.lib.document_sources.identifier_import import (
    ReferenceImportCandidate,
    ReferenceImportDecision,
    ReferenceImportDecisionStatus,
    decide_reference_import,
)
from src.lib.document_sources.models import (
    ProviderBearerKind,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceReference,
)
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    _abc_reference_record,
    _abc_show_all_main_pdf,
    _abc_show_all_row,
    provider_from_fake,
)
from tests.unit.lib.document_sources.test_file_listing import (
    GOOD_TABLE,
    _Client,
    _limits,
    _pair,
    _Store,
)

CURATOR = ProviderBearerKind.CURATOR
SERVICE = ProviderBearerKind.SERVICE


def _main_text(referencefile_id: int, *, display_name: str = "Made_up_main_merged") -> dict[str, Any]:
    """ABC's converted main text: a reference-level ``converted_merged_main`` row, no ``source``."""

    return _abc_show_all_row(
        referencefile_id, None, file_class="converted_merged_main", file_extension="md",
        display_name=display_name,
    )


def _unconverted(original_id: int, *mods: str, extension: str, file_class: str = "supplement") -> dict[str, Any]:
    return _abc_show_all_row(
        original_id, *mods, file_class=file_class, file_extension=extension,
        display_name=f"Made_up_S{original_id}",
    )


# A WB paper: main PDF, one converted Excel supplement, unconverted Excel and PDF supplements,
# an unconverted video and a large data file.
def _wb_rows(*main_text_rows: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _abc_show_all_main_pdf(10, "WB"),
        *main_text_rows,
        *_pair(20, "WB"),
        _unconverted(30, "WB", extension="xlsx"),
        _unconverted(40, "WB", extension="pdf"),
        _unconverted(50, "WB", extension="mov"),
        _unconverted(60, "WB", extension="tsv", file_class="htp_supplement"),
    ]


async def _listing(
    rows, *, bearer_kind=CURATOR, route=ListingRoute.IDENTIFIER, in_paper=(),
    observations=None, payloads=None, library_paper=None,
):
    async def library(_decision):
        return library_paper

    client = _Client({"21": GOOD_TABLE, **(payloads or {})})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    store = _Store()
    listing = await list_reference_files(
        provider=provider, reference=reference, authorized_group_ids=("WB",),
        bearer_kind=bearer_kind, request_bearer_token="curator-token", files_in_paper=in_paper,
        limits=_limits(), check_limit=20, load_stored=store.load, save_assessment=store.save,
        route=route, provider_observations=observations or {},
        find_paper_in_library=library if route is ListingRoute.IDENTIFIER else None,
    )
    return listing, client


def _tick(listing) -> dict[str, tuple[bool, FileReason | None]]:
    return {item.provider_file_id: (item.tickable, item.not_tickable_reason) for item in listing.files}


@pytest.mark.asyncio
async def test_unconverted_main_on_a_curator_login_makes_unconverted_supplements_tickable():
    listing, _ = await _listing(_wb_rows())
    assert _tick(listing) == {
        "10": (True, None),
        "20": (True, None),
        "30": (True, None),
        "40": (True, None),
        "50": (False, FileReason.TYPE_NOT_SUPPORTED),
        "60": (False, FileReason.LARGE_DATA_FILE),
    }


@pytest.mark.asyncio
async def test_converted_main_leaves_unconverted_supplements_not_tickable():
    listing, _ = await _listing(_wb_rows(_main_text(11)))
    ticks = _tick(listing)
    assert ticks["20"] == (True, None)
    assert ticks["30"] == (False, FileReason.PROVIDER_NOT_CONVERTED_YET)
    assert ticks["40"] == (False, FileReason.PROVIDER_NOT_CONVERTED_YET)


@pytest.mark.asyncio
async def test_a_legacy_tei_main_text_counts_as_no_main_text():
    listing, _ = await _listing(_wb_rows(_main_text(11, display_name="Made_up_main_tei")))
    assert _tick(listing)["30"] == (True, None)
    assert _tick(listing)["40"] == (True, None)


@pytest.mark.asyncio
async def test_a_machine_login_never_makes_unconverted_supplements_tickable():
    listing, _ = await _listing(_wb_rows(), bearer_kind=SERVICE)
    assert _tick(listing)["30"] == (False, FileReason.PROVIDER_NOT_CONVERTED_YET)
    assert _tick(listing)["40"] == (False, FileReason.PROVIDER_NOT_CONVERTED_YET)


@pytest.mark.asyncio
async def test_the_per_paper_route_never_marks_unconverted_files_tickable():
    listing, _ = await _listing(_wb_rows(), route=ListingRoute.PAPER, in_paper=("10",))
    ticks = _tick(listing)
    assert ticks["10"] == (False, FileReason.ALREADY_IN_PAPER)
    assert ticks["20"] == (True, None)
    assert ticks["30"] == (False, FileReason.PROVIDER_NOT_CONVERTED_YET)
    assert ticks["40"] == (False, FileReason.PROVIDER_NOT_CONVERTED_YET)


@pytest.mark.asyncio
async def test_a_supplement_already_in_the_paper_is_not_tickable():
    listing, _ = await _listing(_wb_rows(_main_text(11)), route=ListingRoute.PAPER, in_paper=("10", "20"))
    assert _tick(listing)["20"] == (False, FileReason.ALREADY_IN_PAPER)


@pytest.mark.asyncio
async def test_a_converted_text_that_did_not_pass_its_check_is_not_tickable():
    refused = b"# One\n\n# Two\n"  # an Office file must have exactly one top-level title (R7)
    listing, _ = await _listing(_wb_rows(_main_text(11)), payloads={"21": refused})
    entry = {item.provider_file_id: item for item in listing.files}["20"]
    assert entry.check.status is ListedFileCheckStatus.REFUSED
    assert (entry.tickable, entry.not_tickable_reason) == (False, FileReason.CHECK_NOT_PASSED)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [ListingRoute.IDENTIFIER, ListingRoute.PAPER])
@pytest.mark.parametrize("bearer_kind", [CURATOR, SERVICE])
async def test_a_converted_main_never_makes_an_unconverted_file_tickable(route, bearer_kind):
    """No request asks the provider for supplements of a converted paper (P7d is not part of P6f)."""

    listing, _ = await _listing(_wb_rows(_main_text(11)), route=route, bearer_kind=bearer_kind)
    unconverted = [item for item in listing.files if item.provider_file_id in {"30", "40"}]
    assert [item.tickable for item in unconverted] == [False, False]
    assert {item.not_tickable_reason for item in unconverted} == {FileReason.PROVIDER_NOT_CONVERTED_YET}


@pytest.mark.asyncio
@pytest.mark.parametrize("main_text_rows", [(), (_main_text(11),), (_main_text(11, display_name="Made_up_main_tei"),)])
@pytest.mark.parametrize("bearer_kind", [CURATOR, SERVICE])
async def test_the_listing_says_a_request_would_be_sent_exactly_when_an_import_sends_it(main_text_rows, bearer_kind):
    """The rule is read from the import's own decision, so it cannot drift from what an import does."""

    rows = _wb_rows(*main_text_rows)
    listing, _ = await _listing(rows, bearer_kind=bearer_kind)
    listing_says = _tick(listing)["30"][0]

    client = _Client({})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    client.conversion_payload = {"reference_curie": "AGRKB:101", "status": "running", "per_file_progress": []}
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    await decide_reference_import(
        provider=provider, identifier="AGRKB:101", reference=reference,
        artifacts=await provider.list_artifacts(reference, request_bearer_token="curator-token"),
        authorized_group_ids=("WB",), bearer_kind=bearer_kind, request_bearer_token="curator-token",
        allow_conversion_request=True,
    )
    import_sent = any(name == "request_referencefile_conversion" for name, _ in client.calls)
    assert listing_says is import_sent


def _decision(status: str, *, converted: bool) -> ReferenceImportDecision:
    reference = SourceReference(provider="made_up", reference_id="1", reference_curie="MADE:1")
    source = SourceArtifact(
        provider="made_up", artifact_id="10", role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF, status=SourceArtifactStatus.AVAILABLE,
    )
    text = SourceArtifact(
        provider="made_up", artifact_id="11", role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN, status=SourceArtifactStatus.AVAILABLE,
    )
    candidate = ReferenceImportCandidate(
        reference=reference, source_artifact=source, converted_artifact=text if converted else None,
    )
    return ReferenceImportDecision(
        status=status, provider="made_up", identifier="MADE:1", reference=reference, selected=candidate,
    )


@pytest.mark.parametrize(
    ("status", "converted", "bearer_kind", "route", "expected"),
    [
        (ReferenceImportDecisionStatus.READY, False, CURATOR, ListingRoute.IDENTIFIER, True),
        (ReferenceImportDecisionStatus.READY, True, CURATOR, ListingRoute.IDENTIFIER, False),
        (ReferenceImportDecisionStatus.READY, False, SERVICE, ListingRoute.IDENTIFIER, False),
        # An add from an imported paper sends no request.
        (ReferenceImportDecisionStatus.READY, False, CURATOR, ListingRoute.PAPER, False),
        # A provider that lists its main text as running or failed (ABC's listing cannot):
        # the import returns before any request.
        (ReferenceImportDecisionStatus.CONVERSION_RUNNING, False, CURATOR, ListingRoute.IDENTIFIER, False),
        (ReferenceImportDecisionStatus.CONVERSION_FAILED, False, CURATOR, ListingRoute.IDENTIFIER, False),
    ],
)
def test_the_rule_over_core_decisions(status, converted, bearer_kind, route, expected):
    decision = _decision(status, converted=converted)
    assert will_ask_provider(decision, bearer_kind=bearer_kind, route=route) is expected


@pytest.mark.asyncio
async def test_a_last_observation_is_shown_on_its_file_and_in_the_payload():
    seen = datetime(2026, 11, 22, 15, 4, tzinfo=timezone.utc)
    observations = {"40": ProviderObservation(code=FileReason.PROVIDER_CONVERSION_FAILED, observed_at=seen)}
    listing, _ = await _listing(
        _wb_rows(_main_text(11)), route=ListingRoute.PAPER, in_paper=("10",), observations=observations,
    )
    entries = {item["provider_file_id"]: item for item in listing_to_payload(listing)["files"]}
    assert entries["40"]["last_provider_observation"] == {
        "code": "provider_conversion_failed", "observed_at": "2026-11-22T15:04:00+00:00",
    }
    assert entries["30"]["last_provider_observation"] is None
    assert entries["40"]["tickable"] is False
    assert entries["40"]["not_tickable_reason"] == "provider_not_converted_yet"
    assert entries["20"]["tickable"] is True and entries["20"]["not_tickable_reason"] is None


@pytest.mark.asyncio
async def test_the_identifier_route_refuses_observations():
    """Observations come from this paper's jobs; a paper that is not imported yet has none."""

    with pytest.raises(FileListingError, match="per-paper"):
        await _listing(
            _wb_rows(), observations={"40": ProviderObservation(
                code=FileReason.NOT_CONVERTED_IN_TIME, observed_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
            )},
        )


def test_an_observation_code_outside_its_group_is_refused():
    with pytest.raises(FileReasonGroupError):
        ProviderObservation(code=FileReason.TOO_LARGE, observed_at=datetime(2026, 11, 22, tzinfo=timezone.utc))


def test_every_not_tickable_reason_is_a_code_of_the_shared_enum():
    assert all(isinstance(reason, FileReason) for reason in NOT_TICKABLE_REASONS)


# The job metadata P7b writes (interface I1, plan section 4.4).
def _entry(file_id: str, state: str, reason_code: str | None = None, observed_at: str | None = None) -> dict:
    return {
        "provider_file_id": file_id, "display_name": f"Made_up_S{file_id}", "state": state,
        "reason_code": reason_code, "observed_at": observed_at, "file_id": None,
    }


def test_observations_take_the_newest_entry_per_file_across_this_papers_jobs():
    from src.lib.document_sources.file_listing_service import provider_observations_from_jobs

    newest = {"supplements": [
        _entry("40", "left_out", "provider_conversion_failed", "2026-11-22T15:04:00+00:00"),
        _entry("30", "added"),
    ]}
    older = {"supplements": [
        _entry("30", "left_out", "not_converted_in_time", "2026-11-21T09:00:00+00:00"),
        _entry("70", "left_out", "provider_not_converted", "2026-11-21T09:00:00+00:00"),
        _entry("80", "left_out", "too_large"),  # left out by a check: not a provider observation
    ]}
    before_p7b = {"stage": "made-up"}
    observations = provider_observations_from_jobs([newest, None, before_p7b, older])

    assert set(observations) == {"40", "70"}  # 30 was added later; 80 was refused by a check
    assert observations["40"].code is FileReason.PROVIDER_CONVERSION_FAILED
    assert observations["40"].observed_at == datetime(2026, 11, 22, 15, 4, tzinfo=timezone.utc)
    assert observations["70"].code is FileReason.PROVIDER_NOT_CONVERTED


def test_a_file_still_waiting_in_the_newest_job_has_no_observation():
    from src.lib.document_sources.file_listing_service import provider_observations_from_jobs

    observations = provider_observations_from_jobs([
        {"supplements": [_entry("40", "waiting")]},
        {"supplements": [_entry("40", "left_out", "provider_not_converted", "2026-11-21T09:00:00+00:00")]},
    ])
    assert observations == {}


@pytest.mark.parametrize(
    "supplements",
    [
        "not-a-list",
        ["not-an-object"],
        [_entry("", "left_out")],
        [_entry("40", "made_up_state")],
        [_entry("40", "left_out", "made_up_code", "2026-11-22T15:04:00+00:00")],
        [_entry("40", "left_out", "provider_not_converted", None)],
        [_entry("40", "left_out", "provider_not_converted", "yesterday")],
        [_entry("40", "left_out", "provider_not_converted", "2026-11-22T15:04:00")],
    ],
)
def test_supplement_metadata_in_another_shape_is_a_named_error(supplements):
    from src.lib.document_sources.file_listing_service import (
        SupplementProgressMetadataError,
        provider_observations_from_jobs,
    )

    with pytest.raises(SupplementProgressMetadataError):
        provider_observations_from_jobs([{"supplements": supplements}])


@pytest.mark.asyncio
async def test_the_per_paper_service_lists_with_this_papers_observations_and_the_identifier_service_without(monkeypatch):
    from uuid import uuid4

    from src.lib.document_sources import file_listing_service
    from src.lib.document_sources.access import DocumentSourceRequestContext

    rows = _wb_rows(_main_text(11))
    client = _Client({"21": GOOD_TABLE})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    store = _Store()
    seen = datetime(2026, 11, 22, 15, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", _limits)
    monkeypatch.setattr(
        file_listing_service, "_paper_lookup",
        lambda *_: file_listing_service._PaperSource(
            provider=provider.provider_id, reference_id="101", reference_curie="AGRKB:101", file_ids=("10",),
            provider_observations={"40": ProviderObservation(code=FileReason.NOT_CONVERTED_IN_TIME, observed_at=seen)},
        ),
    )
    context = DocumentSourceRequestContext(
        provider_groups=("WB",), authorized_group_ids=("WB",), bearer_kind=CURATOR, curator_token="curator-token",
    )

    paper = await file_listing_service.list_files_for_paper(
        uuid4(), owner_user_id=7, provider=provider, context=context,
    )
    by_id = {item.provider_file_id: item for item in paper.files}
    assert by_id["40"].last_provider_observation == ProviderObservation(
        code=FileReason.NOT_CONVERTED_IN_TIME, observed_at=seen,
    )
    assert by_id["10"].not_tickable_reason is FileReason.ALREADY_IN_PAPER

    async def not_in_library(_decision):
        return None

    monkeypatch.setattr(file_listing_service, "_library_lookup", lambda *_: not_in_library)
    before_import = await file_listing_service.list_files_for_identifier(
        "AGRKB:101", owner_user_id=7, provider=provider, context=context,
    )
    assert all(item.last_provider_observation is None for item in before_import.files)


# Review fixes (22 Nov): F2 and F6.


@pytest.mark.asyncio
async def test_a_file_already_in_the_paper_shows_no_old_observation():
    seen = datetime(2026, 11, 22, 15, 4, tzinfo=timezone.utc)
    observations = {"20": ProviderObservation(code=FileReason.PROVIDER_NOT_CONVERTED, observed_at=seen)}
    listing, _ = await _listing(
        _wb_rows(_main_text(11)), route=ListingRoute.PAPER, in_paper=("10", "20"), observations=observations,
    )
    entry = {item.provider_file_id: item for item in listing.files}["20"]
    assert entry.not_tickable_reason is FileReason.ALREADY_IN_PAPER
    assert entry.last_provider_observation is None


@pytest.mark.asyncio
async def test_a_paper_already_in_the_library_has_no_tickable_file_and_names_that_paper():
    listing, _ = await _listing(_wb_rows(), library_paper="made-up-paper-id")
    assert {item.tickable for item in listing.files} == {False}
    assert {item.not_tickable_reason for item in listing.files} == {FileReason.PAPER_ALREADY_IN_LIBRARY}
    payload = listing_to_payload(listing)
    assert payload["existing_document_id"] == "made-up-paper-id"


@pytest.mark.asyncio
async def test_a_reference_without_a_main_file_cannot_be_imported_so_nothing_is_tickable():
    """The import of an identifier with no main PDF ends with an error (no_source_artifact)."""

    rows = [row for row in _wb_rows() if row["referencefile_id"] != 10]
    listing, _ = await _listing(rows)
    assert listing.main_status == ReferenceImportDecisionStatus.NO_SOURCE_ARTIFACT
    assert {item.not_tickable_reason for item in listing.files} == {FileReason.PAPER_NOT_IMPORTABLE}
    assert listing_to_payload(listing)["existing_document_id"] is None


@pytest.mark.asyncio
async def test_a_main_file_the_curator_cannot_open_makes_nothing_tickable():
    rows = [_abc_show_all_main_pdf(10, "FB"), *_pair(20, "WB")]
    listing, _ = await _listing(rows)
    assert listing.main_status == ReferenceImportDecisionStatus.ACCESS_DENIED
    assert [(item.provider_file_id, item.not_tickable_reason) for item in listing.files] == [
        ("20", FileReason.PAPER_NOT_IMPORTABLE),
    ]


@pytest.mark.asyncio
async def test_the_per_paper_listing_does_not_depend_on_the_main_files_decision():
    """Adds to an imported paper go ahead whatever the provider now says about its main file."""

    rows = [row for row in _wb_rows() if row["referencefile_id"] != 10]
    listing, _ = await _listing(rows, route=ListingRoute.PAPER, in_paper=())
    assert {item.provider_file_id: item.tickable for item in listing.files}["20"] is True


@pytest.mark.parametrize(
    "status",
    [
        ReferenceImportDecisionStatus.AMBIGUOUS_MATCH,
        ReferenceImportDecisionStatus.CONVERSION_FAILED,
        ReferenceImportDecisionStatus.NO_CONVERTED_TEXT,
    ],
)
@pytest.mark.asyncio
async def test_every_decision_the_import_refuses_refuses_the_whole_paper(status):
    from src.lib.document_sources.file_listing import _paper_refusal

    async def not_reached(_decision):
        raise AssertionError("the Library is checked only for an importable paper")

    reason, existing = await _paper_refusal(
        _decision(status, converted=False), ListingRoute.IDENTIFIER, not_reached,
    )
    assert (reason, existing) == (FileReason.PAPER_NOT_IMPORTABLE, None)


@pytest.mark.asyncio
async def test_a_main_text_still_running_keeps_the_paper_importable():
    from src.lib.document_sources.file_listing import _paper_refusal

    async def not_in_library(_decision):
        return None

    reason, _ = await _paper_refusal(
        _decision(ReferenceImportDecisionStatus.CONVERSION_RUNNING, converted=False), ListingRoute.IDENTIFIER,
        not_in_library,
    )
    assert reason is None
