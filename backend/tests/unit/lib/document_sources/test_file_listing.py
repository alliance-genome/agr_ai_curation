"""File listing through the real ABC adapter mapping (P6).

Rows are ABC ``show_all`` shapes (as in ``test_abc_literature_provider``); the
provider under test is the Alliance adapter with a fake HTTP client, so every
artifact the listing sees is one the backend can really produce. All values are
made up.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.lib.document_sources.file_assessment import FileCheckLimits, FileCheckReason
from src.lib.document_sources.file_listing import (
    FileListingError,
    FileRoleMappingError,
    ListedFileCheckStatus,
    ListedFileStatus,
    file_role_for_artifact_role,
    list_reference_files,
)
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
    values = dict(max_bytes=10_000, few_characters_below=10, row_token_limit=200, count_tokens=len)
    values.update(overrides)
    return FileCheckLimits(**values)


async def _listing(
    rows, *, groups=("WB",), payloads=None, store=None, licence_open=None, licence_name=None,
    in_paper=(), limits=None,
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
        files_in_paper=in_paper, limits=limits or _limits(), download_concurrency=2,
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
            download_concurrency=1, load_stored=_Store().load, save_assessment=_Store().save,
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
