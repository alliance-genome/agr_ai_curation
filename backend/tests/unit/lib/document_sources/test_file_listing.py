"""File listing through the real ABC adapter mapping (P6).

Rows are ABC ``show_all`` shapes (as in ``test_abc_literature_provider``); the
provider under test is the Alliance adapter with a fake HTTP client, so every
artifact the listing sees is one the backend can really produce. All values are
made up.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any
from uuid import uuid4

import pytest

from src.lib.document_sources import file_listing

from src.lib.document_sources.file_assessment import FileCheckLimits, FileCheckReason
from src.lib.document_sources.file_listing import (
    FileListingError,
    FileRoleMappingError,
    ListedFileCheckStatus,
    ListedFileStatus,
    ConvertedTextDownloadFailure,
    converted_text_access_allowed,
    download_converted_text,
    file_role_for_artifact_role,
    list_reference_files,
    listing_to_payload,
)
from src.lib.document_sources.models import SupplementFileCategory
from src.lib.document_sources.models import ProviderBearerKind, SourceArtifactRole
from agr_ai_curation_alliance.literature.client import ABCLiteratureHTTPError
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    FakeABCLiteratureClient,
    _abc_reference_record,
    _abc_show_all_main_pdf,
    _abc_show_all_row,
    _abc_source,
    provider_from_fake,
)

GOOD_TABLE = (
    "# Made-up workbook\n\n## Sheet1\n\nMade-up strains and scores for a made-up assay.\n\n"
    "| strain | score |\n| --- | --- |\n| s1 | 1 |\n| s2 | 2 |\n"
).encode()


def _http_error(status: int) -> ABCLiteratureHTTPError:
    return ABCLiteratureHTTPError("made-up error", status_code=status, endpoint="download_file")


class _Client(FakeABCLiteratureClient):
    def __init__(self, payloads: dict[str, bytes | Exception]):
        super().__init__()
        self.payloads = payloads

    async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
        self.calls.append(("download_referencefile", {"artifact_id": artifact_id, "max_bytes": max_bytes}))
        payload = self.payloads[artifact_id]
        if isinstance(payload, Exception):
            raise payload
        return payload


def _pair(original_id: int, *mods, extension="xlsx", converted_mods=None, md5=None):
    original = _abc_show_all_row(
        original_id, *mods, file_class="supplement", file_extension=extension,
        display_name=f"Made_up_S{original_id}",
    )
    if md5 is not None:
        original["md5sum"] = md5
    converted = _abc_show_all_row(
        original_id + 1, *(mods if converted_mods is None else converted_mods),
        file_class="converted_merged_supplement", file_extension="md",
        display_name=f"Made_up_S{original_id}_{extension}", source=_abc_source(original),
    )
    return [original, converted]


class _Store:
    def __init__(self):
        self.saved: dict[tuple[str, str, str], Any] = {}

    def load(self, file_id, md5, version):
        return self.saved.get((file_id, md5, version))

    def save(self, file_id, md5, version, assessment):
        self.saved[(file_id, md5, version)] = assessment


def _limits(**overrides) -> FileCheckLimits:
    values = dict(
        max_bytes=10_000, few_characters_below=10, row_token_limit=200, count_tokens=len,
        token_encoding="made-up-encoding",
    )
    values.update(overrides)
    return FileCheckLimits(**values)


async def _listing(
    rows, *, groups=("WB",), payloads=None, store=None, licence_open=None, licence_name=None,
    in_paper=(), limits=None, check_limit=20,
):
    client = _Client(payloads or {})
    client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=licence_open, copyright_license_name=licence_name,
    )
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    store = store or _Store()
    listing = await list_reference_files(
        provider=provider, reference=reference, authorized_group_ids=tuple(groups),
        bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
        files_in_paper=in_paper, limits=limits or _limits(), check_limit=check_limit,
        load_stored=store.load, save_assessment=store.save,
    )
    return listing, client


def _by_id(listing):
    return {item.provider_file_id: item for item in listing.files}


@pytest.mark.asyncio
async def test_a_main_paper_and_a_checked_ready_supplement():
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")]
    listing, client = await _listing(rows, payloads={"21": GOOD_TABLE})

    files = _by_id(listing)
    assert files["10"].file_role == "main" and files["10"].check.status is ListedFileCheckStatus.NOT_CHECKED
    supplement = files["20"]
    assert supplement.status is ListedFileStatus.READY
    assert supplement.file_role == "supplement"
    assert supplement.converted_text_id == "21"
    assert supplement.check.status is ListedFileCheckStatus.PASSED
    assert "21" not in files  # converted text is never its own entry
    assert listing.not_shown_count == 0
    downloads = [kwargs for name, kwargs in client.calls if name == "download_referencefile"]
    assert downloads == [{"artifact_id": "21", "max_bytes": 10_000}]


@pytest.mark.asyncio
async def test_a_second_listing_downloads_nothing_and_a_changed_md5_is_checked_again():
    store = _Store()
    rows = [*_pair(20, "WB")]
    await _listing(rows, payloads={"21": GOOD_TABLE}, store=store)
    _, client = await _listing(rows, payloads={}, store=store)
    assert not [name for name, _ in client.calls if name == "download_referencefile"]

    changed = [*_pair(20, "WB")]
    changed[1]["md5sum"] = "md5-changed"
    _, client = await _listing(changed, payloads={"21": GOOD_TABLE}, store=store)
    assert [name for name, _ in client.calls if name == "download_referencefile"] == ["download_referencefile"]


@pytest.mark.asyncio
async def test_a_changed_threshold_checks_again():
    store = _Store()
    rows = [*_pair(20, "WB")]
    await _listing(rows, payloads={"21": GOOD_TABLE}, store=store)
    _, client = await _listing(rows, payloads={"21": GOOD_TABLE}, store=store, limits=_limits(row_token_limit=199))
    assert [name for name, _ in client.calls if name == "download_referencefile"] == ["download_referencefile"]


@pytest.mark.asyncio
async def test_a_refusal_is_stored_and_reported_with_its_code():
    store = _Store()
    bad = "# T\n\n| a | b |\n| --- | --- |\n| 1 | 2 | 3 |\n".encode()
    listing, _ = await _listing([*_pair(20, "WB")], payloads={"21": bad}, store=store)
    check = _by_id(listing)["20"].check
    assert check.status is ListedFileCheckStatus.REFUSED
    assert check.reason is FileCheckReason.TABLE_CELLS_MISMATCH
    assert [assessment.reason for assessment in store.saved.values()] == [FileCheckReason.TABLE_CELLS_MISMATCH]


@pytest.mark.asyncio
async def test_an_oversized_download_is_a_too_large_refusal():
    listing, _ = await _listing(
        [*_pair(20, "WB")], payloads={"21": _http_error(413)},
    )
    check = _by_id(listing)["20"].check
    assert check.reason is FileCheckReason.TOO_LARGE and check.limit_value == 10_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original_mods", "converted_mods", "shown"),
    [
        (("WB",), ("WB",), True),
        # The original allows the curator, its converted text's own rule does not (pre-sync).
        (("WB",), ("FB",), False),
        # The converted text's own rule allows the curator, the original's does not.
        (("FB",), ("WB",), False),
        # Each rule allows a different group of a curator in both: checked separately, shown.
        (("FB",), ("WB",), True),
    ],
)
async def test_a_converted_supplement_needs_both_rules_checked_separately(original_mods, converted_mods, shown):
    groups = ("WB", "FB") if shown and original_mods != converted_mods else ("WB",)
    listing, _ = await _listing(
        [*_pair(20, *original_mods, converted_mods=converted_mods)], groups=groups, payloads={"21": GOOD_TABLE},
    )
    assert ("20" in _by_id(listing)) is shown
    assert listing.not_shown_count == (0 if shown else 1)
    if not shown:
        assert all(item.display_name is None or "S20" not in item.display_name for item in listing.files)


@pytest.mark.asyncio
async def test_a_licence_that_refuses_everyone_refuses_an_unrestricted_supplement():
    # A no-MOD row is open to any group only under the MOD rule; a licence that
    # is present with its open-access flag unset refuses everyone (as ABC does).
    listing, client = await _listing(
        [*_pair(20, None)], licence_name="Made-up licence", licence_open=None, payloads={"21": GOOD_TABLE},
    )
    assert listing.files == () and listing.not_shown_count == 1
    assert not [name for name, _ in client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
async def test_listing_refuses_an_unresolved_reference():
    client = _Client({})
    provider = provider_from_fake(client)
    with pytest.raises(FileListingError, match="resolved"):
        await list_reference_files(
            provider=provider, reference="AGRKB:101", authorized_group_ids=("WB",),  # type: ignore[arg-type]
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="t", limits=_limits(),
            check_limit=20, load_stored=_Store().load, save_assessment=_Store().save,
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_same_md5_copies_collapse_to_the_lowest_readable_copy():
    rows = [*_pair(30, "FB", md5="same"), *_pair(40, "WB", md5="same"), *_pair(50, "WB", md5="same")]
    listing, _ = await _listing(rows, groups=("WB",), payloads={"41": GOOD_TABLE})
    ids = [item.provider_file_id for item in listing.files]
    assert ids == ["40"]
    assert listing.duplicate_copies_count == 1
    assert listing.not_shown_count == 1  # the FB copy the curator cannot open


@pytest.mark.asyncio
async def test_statuses_for_files_without_text_data_files_and_other_types():
    no_text = _abc_show_all_row(60, "WB", file_class="supplement", file_extension="docx", display_name="Made_up_S60")
    data_file = _abc_show_all_row(70, "WB", file_class="htp_supplement", file_extension="tsv", display_name="Made_up_S70")
    other = _abc_show_all_row(80, "WB", file_class="supplement", file_extension="zip", display_name="Made_up_S80")
    draft = _abc_show_all_row(90, "WB", file_class="supplement", file_extension="xlsx", display_name="Made_up_S90")
    draft["file_publication_status"] = "temp"
    listing, client = await _listing([no_text, data_file, other, draft])
    files = _by_id(listing)
    assert files["60"].status is ListedFileStatus.NO_CONVERTED_TEXT and files["60"].file_role is None
    assert files["70"].status is ListedFileStatus.LARGE_DATA_FILE
    assert files["80"].status is ListedFileStatus.TYPE_NOT_SUPPORTED
    assert "90" not in files
    assert not [name for name, _ in client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
async def test_files_already_in_the_paper_are_marked():
    listing, _ = await _listing(
        [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE}, in_paper=("10",),
    )
    files = _by_id(listing)
    assert files["10"].in_this_paper is True and files["20"].in_this_paper is False


@pytest.mark.asyncio
async def test_the_single_title_rule_comes_from_the_provider_for_office_files_only():
    two_titles = "# One\n\n# Two\n\nreadable made-up text here\n".encode()
    pdf_pair = _pair(20, "WB", extension="pdf")
    office_pair = _pair(30, "WB")
    listing, _ = await _listing([*pdf_pair, *office_pair], payloads={"21": two_titles, "31": two_titles})
    files = _by_id(listing)
    assert files["20"].check.status is ListedFileCheckStatus.PASSED
    assert files["30"].check.reason is FileCheckReason.TITLE_COUNT


@pytest.mark.asyncio
async def test_unavailable_or_missing_text_is_reported_not_stored():
    store = _Store()
    listing, _ = await _listing(
        [*_pair(20, "WB"), *_pair(30, "WB")], store=store,
        payloads={"21": _http_error(502), "31": _http_error(404)},
    )
    files = _by_id(listing)
    assert files["20"].check.status is ListedFileCheckStatus.TEXT_UNAVAILABLE
    assert files["30"].check.status is ListedFileCheckStatus.TEXT_MISSING
    assert store.saved == {}


def test_file_roles_are_mapped_explicitly():
    assert file_role_for_artifact_role(SourceArtifactRole.SOURCE_PDF, is_main=True) == "main"
    assert file_role_for_artifact_role(SourceArtifactRole.SOURCE_PDF, is_main=False) == "supplement"
    assert file_role_for_artifact_role(SourceArtifactRole.SOURCE_SUPPLEMENT, is_main=False) == "supplement"
    for role, is_main in [
        (SourceArtifactRole.SOURCE_SUPPLEMENT, True),
        (SourceArtifactRole.CONVERTED_TEXT, False),
        (SourceArtifactRole.UNKNOWN, False),
        (SourceArtifactRole.PROVIDER_METADATA, True),
    ]:
        with pytest.raises(FileRoleMappingError):
            file_role_for_artifact_role(role, is_main=is_main)


@pytest.mark.asyncio
async def test_entries_carry_both_md5s_format_category_and_profile_for_an_import():
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB", md5="original-md5")]
    rows[2]["md5sum"] = "converted-md5"
    listing, _ = await _listing(rows, payloads={"21": GOOD_TABLE})
    files = _by_id(listing)
    supplement = files["20"]
    assert supplement.md5 == "original-md5"
    assert supplement.converted_md5 == "converted-md5"
    # Core formats only: an Office original is "unknown"; a PDF supplement is "pdf".
    assert supplement.artifact_format == "unknown"
    assert supplement.category is SupplementFileCategory.CONVERTIBLE
    assert supplement.single_title_required is True
    assert files["10"].category is None and files["10"].single_title_required is False

    entry = {item["provider_file_id"]: item for item in listing_to_payload(listing)["files"]}["20"]
    assert entry["md5"] == "original-md5" and entry["converted_md5"] == "converted-md5"
    assert entry["format"] == "unknown" and entry["category"] == "convertible"


@pytest.mark.parametrize(("original_mods", "converted_mods", "allowed"), [
    (("WB",), ("WB",), True),
    (("WB",), ("FB",), False),
    (("FB",), ("WB",), False),
])
@pytest.mark.asyncio
async def test_converted_text_access_checks_both_rules(original_mods, converted_mods, allowed):
    client = _Client({})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = _pair(20, *original_mods, converted_mods=converted_mods)  # type: ignore[assignment]
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    artifacts = {item.artifact_id: item for item in await provider.list_artifacts(
        reference, request_bearer_token="curator-token",
    )}
    assert converted_text_access_allowed(
        artifacts["21"], artifacts["20"], authorized_group_ids=("WB",),
    ) is allowed


@pytest.mark.asyncio
async def test_the_shared_download_helper_bounds_size_and_reports_failure_codes():
    client = _Client({"1": b"text", "2": _http_error(404), "3": _http_error(502), "4": _http_error(403)})
    provider = provider_from_fake(client)
    results = {
        artifact_id: await download_converted_text(
            provider, artifact_id, request_bearer_token="curator-token", max_bytes=50, use=len,
        )
        for artifact_id in ("1", "2", "3", "4")
    }
    assert results["1"].result == 4 and results["1"].failure is None
    assert results["2"].failure is ConvertedTextDownloadFailure.MISSING
    assert results["3"].failure is ConvertedTextDownloadFailure.UNAVAILABLE
    assert results["4"].failure is ConvertedTextDownloadFailure.ACCESS_DENIED
    downloads = [kwargs for name, kwargs in client.calls if name == "download_referencefile"]
    assert len(downloads) == 4 and all(item["max_bytes"] == 50 for item in downloads)


class _GatedClient(_Client):
    """Counts downloads in progress, and holds each until released."""

    def __init__(self, payloads):
        super().__init__(payloads)
        self.active = 0
        self.most_active = 0
        self.release = asyncio.Event()

    async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        await self.release.wait()
        try:
            return await super().download_referencefile(
                artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
            )
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_downloads_share_one_process_wide_limit_and_hold_it_while_the_bytes_are_used(monkeypatch):
    monkeypatch.setattr(file_listing, "_download_limiters", weakref.WeakKeyDictionary())
    monkeypatch.setenv("DOCUMENT_FILE_DOWNLOAD_CONCURRENCY", "2")
    client = _GatedClient({str(i): b"x" for i in range(6)})
    provider = provider_from_fake(client)
    in_use = {"now": 0, "most": 0}

    def use(content):
        in_use["now"] += 1
        in_use["most"] = max(in_use["most"], in_use["now"])
        in_use["now"] -= 1
        return len(content)

    # Two separate callers (two listings, or a listing and an import) still share the limit.
    first = [asyncio.create_task(download_converted_text(
        provider, str(i), request_bearer_token="t", max_bytes=10, use=use)) for i in range(3)]
    second = [asyncio.create_task(download_converted_text(
        provider, str(i), request_bearer_token="t", max_bytes=10, use=use)) for i in range(3, 6)]
    await asyncio.sleep(0.05)
    assert client.active == 2
    client.release.set()
    results = await asyncio.gather(*first, *second)
    assert [item.result for item in results] == [1] * 6
    assert client.most_active == 2


@pytest.mark.asyncio
async def test_a_listing_checks_at_most_the_limit_and_codes_the_rest():
    rows = [pair_row for original in (20, 30, 40) for pair_row in _pair(original, "WB")]
    store = _Store()
    listing, client = await _listing(
        rows, payloads={"21": GOOD_TABLE, "31": GOOD_TABLE, "41": GOOD_TABLE}, store=store, check_limit=2,
    )
    files = _by_id(listing)
    assert files["20"].check.status is ListedFileCheckStatus.PASSED
    assert files["30"].check.status is ListedFileCheckStatus.PASSED
    assert files["40"].check.status is ListedFileCheckStatus.CHECK_LIMIT_REACHED
    assert len([name for name, _ in client.calls if name == "download_referencefile"]) == 2

    # Files checked before do not count against the limit.
    listing, client = await _listing(rows, payloads={"41": GOOD_TABLE}, store=store, check_limit=1)
    assert _by_id(listing)["40"].check.status is ListedFileCheckStatus.PASSED
    assert [kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile"] == ["41"]


@pytest.mark.asyncio
async def test_a_converted_text_without_an_md5_is_never_downloaded():
    rows = _pair(20, "WB")
    rows[1]["md5sum"] = None
    listing, client = await _listing(rows, payloads={"21": GOOD_TABLE})
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.TEXT_WITHOUT_CHECKSUM
    assert not [name for name, _ in client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
async def test_too_large_is_stored_so_a_second_listing_does_not_download_it_again():
    store = _Store()
    rows = _pair(20, "WB")
    await _listing(rows, payloads={"21": b"x" * 20}, store=store, limits=_limits(max_bytes=10))
    listing, client = await _listing(rows, payloads={}, store=store, limits=_limits(max_bytes=10))
    assert _by_id(listing)["20"].check.reason is FileCheckReason.TOO_LARGE
    assert not [name for name, _ in client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
async def test_an_unavailable_text_is_tried_once_per_listing():
    listing, client = await _listing(_pair(20, "WB"), payloads={"21": _http_error(502)})
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.TEXT_UNAVAILABLE
    assert len([name for name, _ in client.calls if name == "download_referencefile"]) == 1


@pytest.mark.asyncio
async def test_two_readable_converted_texts_list_the_newest_and_keep_the_rest_of_the_listing():
    original, older = _pair(20, "WB")
    newer = dict(older, referencefile_id=29, md5sum="md5-29", display_name="Made_up_S20_xlsx_again")
    rows = [_abc_show_all_main_pdf(10, "WB"), original, older, newer, *_pair(30, "WB")]
    listing, client = await _listing(rows, payloads={"29": GOOD_TABLE, "31": GOOD_TABLE})
    files = _by_id(listing)
    assert files["20"].converted_text_id == "29" and files["20"].converted_md5 == "md5-29"
    assert files["30"].check.status is ListedFileCheckStatus.PASSED and "10" in files
    assert [kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile"] in (
        ["29", "31"], ["31", "29"],
    )


@pytest.mark.asyncio
async def test_provider_ids_of_different_widths_keep_numeric_order():
    rows = [*_pair(100, "WB", md5="same"), *_pair(98, "WB", md5="same")]
    listing, _ = await _listing(rows, payloads={"99": GOOD_TABLE})
    assert [item.provider_file_id for item in listing.files] == ["98"]


@pytest.mark.asyncio
async def test_the_listing_and_the_import_choose_the_same_main_file():
    from src.lib.document_sources.identifier_import import select_reference_import_candidate

    rows = [
        _abc_show_all_main_pdf(10, "FB"), _abc_show_all_main_pdf(12, "WB"), _abc_show_all_main_pdf(14),
        *_pair(20, "WB"),
    ]
    listing, client = await _listing(rows, groups=("WB",), payloads={"21": GOOD_TABLE})
    listed_main = [item for item in listing.files if item.file_role == "main"]

    decision = await select_reference_import_candidate(
        provider=provider_from_fake(client), identifier="AGRKB:101", authorized_group_ids=("WB",),
        bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
    )
    assert decision.selected is not None and len(listed_main) == 1
    assert listed_main[0].provider_file_id == decision.selected.source_artifact.artifact_id


@pytest.mark.asyncio
async def test_an_imported_paper_is_listed_by_its_curie_never_by_its_numeric_id(monkeypatch):
    """ABC reads a bare number as a PMID: the stored reference id 101 must never be looked up."""

    from src.lib.document_sources import file_listing_service
    from src.lib.document_sources.access import DocumentSourceRequestContext

    client = _Client({"21": GOOD_TABLE})
    client.show_reference_payload = _abc_reference_record()
    # A different paper whose PMID happens to be 101.
    client.external_lookup_payload = {"reference_curie": "AGRKB:999"}
    client.show_referencefiles_payload = _pair(20, "WB")  # type: ignore[assignment]
    provider = provider_from_fake(client)
    store = _Store()
    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", _limits)
    monkeypatch.setattr(
        file_listing_service, "_paper_lookup",
        lambda *_: file_listing_service._PaperSource(
            provider=provider.provider_id, reference_id="101", reference_curie="AGRKB:101", file_ids=("20",),
        ),
    )
    context = DocumentSourceRequestContext(
        provider_groups=("WB",), authorized_group_ids=("WB",), bearer_kind=ProviderBearerKind.CURATOR,
        curator_token="curator-token",
    )
    listing = await file_listing_service.list_files_for_paper(
        uuid4(), owner_user_id=7, provider=provider, context=context,
    )
    assert listing.reference_id == "101" and listing.reference_curie == "AGRKB:101"
    assert _by_id(listing)["20"].in_this_paper is True
    assert not [name for name, _ in client.calls if name == "lookup_external_curie"]
    assert [kwargs["reference"] for name, kwargs in client.calls if name == "show_reference"] == ["AGRKB:101"]
