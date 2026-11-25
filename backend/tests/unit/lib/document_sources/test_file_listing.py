"""File listing through the real ABC adapter mapping (P6).

Rows are ABC ``show_all`` shapes (as in ``test_abc_literature_provider``); the
provider under test is the Alliance adapter with a fake HTTP client, so every
artifact the listing sees is one the backend can really produce. All values are
made up.
"""

from __future__ import annotations

import asyncio
import hashlib
import weakref
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.document_sources import file_listing

from src.lib.document_sources.file_assessment import FileAssessment, FileCheckLimits
from src.lib.document_sources.file_reasons import FileReason
from src.lib.document_sources.file_listing import (
    FileListingError,
    FileRoleMappingError,
    ListedFileCheckStatus,
    ListedFileStatus,
    ListingRoute,
    ProviderFileDownloadFailure,
    converted_text_access_allowed,
    download_provider_file,
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


def _pair(original_id: int, *mods, extension="xlsx", converted_mods=None, md5=None, content=GOOD_TABLE):
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
    converted["md5sum"] = hashlib.md5(content).hexdigest()
    return [original, converted]


class _Store:
    def __init__(self):
        self.saved: dict[tuple[str, str, str], Any] = {}

    def load(self, file_id, md5, version):
        return self.saved.get((file_id, md5, version))

    def save(self, file_id, md5, version, assessment):
        self.saved[(file_id, md5, version)] = assessment


async def _not_in_library(_decision):
    return None


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
        route=ListingRoute.PAPER if in_paper else ListingRoute.IDENTIFIER,
        provider_observations={},
        find_paper_in_library=None if in_paper else _not_in_library,
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

    changed_text = GOOD_TABLE + b"more text"
    changed = _pair(20, "WB", content=changed_text)
    _, client = await _listing(changed, payloads={"21": changed_text}, store=store)
    assert [name for name, _ in client.calls if name == "download_referencefile"] == ["download_referencefile"]


@pytest.mark.asyncio
async def test_changed_bytes_are_not_checked_or_cached_and_can_be_retried():
    store = _Store()
    rows = _pair(20, "WB")
    listing, _ = await _listing(rows, payloads={"21": GOOD_TABLE + b"changed"}, store=store)
    file = _by_id(listing)["20"]
    assert file.check.status is ListedFileCheckStatus.NOT_CHECKED
    assert file.check.reason is None
    assert not file.tickable
    assert store.saved == {}

    listing, _ = await _listing(rows, payloads={"21": GOOD_TABLE}, store=store)
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.PASSED


@pytest.mark.asyncio
async def test_a_previous_check_version_is_reassessed():
    store = _Store()
    rows = _pair(20, "WB")
    # Version 2 key for _limits(), before the title rule and byte verification.
    store.save("21", rows[1]["md5sum"], "2-0ca4b65e03f97bcd", FileAssessment(True, FileReason.INVALID_TEXT))
    listing, _ = await _listing(rows, payloads={"21": GOOD_TABLE}, store=store)
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.PASSED
    assert len(store.saved) == 2


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
    listing, _ = await _listing(_pair(20, "WB", content=bad), payloads={"21": bad}, store=store)
    check = _by_id(listing)["20"].check
    assert check.status is ListedFileCheckStatus.REFUSED
    assert check.reason is FileReason.TABLE_CELLS_MISMATCH
    assert [assessment.reason for assessment in store.saved.values()] == [FileReason.TABLE_CELLS_MISMATCH]


@pytest.mark.asyncio
async def test_an_oversized_download_is_reported_without_caching_unverified_bytes():
    store = _Store()
    listing, _ = await _listing(
        [*_pair(20, "WB")], payloads={"21": _http_error(413)}, store=store,
    )
    check = _by_id(listing)["20"].check
    assert check.reason is FileReason.TOO_LARGE and check.limit_value == 10_000
    assert store.saved == {}


@pytest.mark.asyncio
async def test_uppercase_md5_matches_bytes_and_reuses_cached_assessment_across_case():
    store = _Store()
    lower_md5 = hashlib.md5(GOOD_TABLE).hexdigest()
    pair = _pair(20, "WB", md5="  ORIG-UPPER  ")
    pair[1]["md5sum"] = f" {lower_md5.upper()} "
    rows = [_abc_show_all_main_pdf(10, "WB"), *pair]

    listing, client = await _listing(rows, payloads={"21": GOOD_TABLE}, store=store)
    file = _by_id(listing)["20"]
    assert file.check.status is ListedFileCheckStatus.PASSED
    assert file.tickable is True
    assert file.md5 == "orig-upper"
    assert file.converted_md5 == lower_md5
    assert [key[1] for key in store.saved] == [lower_md5]
    assert [name for name, _ in client.calls if name == "download_referencefile"] == ["download_referencefile"]

    # Refresh with lowercase MD5 reuses the cached assessment without downloading again.
    lower_pair = _pair(20, "WB", md5="orig-upper")
    lower_pair[1]["md5sum"] = lower_md5
    listing_cached, client_cached = await _listing(
        [_abc_show_all_main_pdf(10, "WB"), *lower_pair], payloads={}, store=store,
    )
    assert _by_id(listing_cached)["20"].check.status is ListedFileCheckStatus.PASSED
    assert _by_id(listing_cached)["20"].tickable is True
    assert not [name for name, _ in client_cached.calls if name == "download_referencefile"]

    # Whitespace-only MD5 is treated as missing.
    blank_rows = _pair(30, "WB")
    blank_rows[1]["md5sum"] = "   "
    blank_listing, blank_client = await _listing(blank_rows, payloads={"31": GOOD_TABLE}, store=store)
    assert _by_id(blank_listing)["30"].check.status is ListedFileCheckStatus.TEXT_WITHOUT_CHECKSUM
    assert not [name for name, _ in blank_client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_payload", "expected_first_status", "expected_first_reason"),
    [
        (_http_error(413), ListedFileCheckStatus.REFUSED, FileReason.TOO_LARGE),
        (GOOD_TABLE + b"mismatch", ListedFileCheckStatus.NOT_CHECKED, None),
    ],
    ids=["oversized-first-file", "checksum-mismatched-first-file"],
)
async def test_uncached_first_file_failure_does_not_starve_later_files_on_refresh(
    first_payload, expected_first_status, expected_first_reason,
):
    store = _Store()
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(30, "WB")]
    payloads = {"21": first_payload, "31": GOOD_TABLE}

    first_listing, first_client = await _listing(rows, payloads=payloads, store=store, check_limit=1)
    first_files = _by_id(first_listing)
    assert first_files["20"].check.status is expected_first_status
    assert first_files["20"].check.reason is expected_first_reason
    assert first_files["20"].tickable is False
    assert first_files["30"].check.status is ListedFileCheckStatus.CHECK_LIMIT_REACHED
    assert first_files["30"].tickable is False
    assert [kwargs["artifact_id"] for name, kwargs in first_client.calls if name == "download_referencefile"] == ["21"]
    assert store.saved == {}

    second_listing, second_client = await _listing(rows, payloads=payloads, store=store, check_limit=1)
    second_files = _by_id(second_listing)
    assert second_files["20"].check.status is ListedFileCheckStatus.CHECK_LIMIT_REACHED
    assert second_files["30"].check.status is ListedFileCheckStatus.PASSED
    assert second_files["30"].tickable is True
    assert [kwargs["artifact_id"] for name, kwargs in second_client.calls if name == "download_referencefile"] == ["31"]
    assert len(store.saved) == 1

    # Once file 30 is cached, a third listing serves 30 from cache and re-checks 20.
    third_listing, third_client = await _listing(rows, payloads=payloads, store=store, check_limit=1)
    third_files = _by_id(third_listing)
    assert third_files["20"].check.status is expected_first_status
    assert third_files["30"].check.status is ListedFileCheckStatus.PASSED
    assert third_files["30"].tickable is True
    assert [kwargs["artifact_id"] for name, kwargs in third_client.calls if name == "download_referencefile"] == ["21"]


@pytest.mark.asyncio
async def test_listing_refresh_cursor_cache_respects_configured_max_entries(monkeypatch):
    monkeypatch.setenv("DOCUMENT_FILE_LISTING_CURSOR_CACHE_MAX_ENTRIES", "1")
    rows = [*_pair(20, "WB"), *_pair(30, "WB")]
    payloads = {"21": _http_error(413), "31": GOOD_TABLE}

    for ref_id in ("101", "202"):
        client = _Client(payloads)
        client.show_reference_payload = dict(_abc_reference_record(), reference_id=int(ref_id), curie=f"AGRKB:{ref_id}")
        client.show_referencefiles_payload = rows  # type: ignore[assignment]
        provider = provider_from_fake(client)
        reference = await provider.resolve_reference(f"AGRKB:{ref_id}", request_bearer_token="curator-token")
        store = _Store()
        await list_reference_files(
            provider=provider, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            limits=_limits(), check_limit=1, load_stored=store.load, save_assessment=store.save,
            route=ListingRoute.IDENTIFIER, provider_observations={}, find_paper_in_library=_not_in_library,
        )

    cursors = file_listing._listing_cursor_store()
    assert len(cursors) == 1
    assert ("abc_literature", "202", ("WB",)) in cursors


@pytest.mark.asyncio
async def test_overlapping_slow_refresh_does_not_rewind_listing_cursor():
    store = _Store()
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(30, "WB"), *_pair(40, "WB")]
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class _SlowFirstClient(_Client):
        async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
            if artifact_id == "21":
                first_started.set()
                await release_first.wait()
            return await super().download_referencefile(
                artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
            )

    async def run_listing(payloads):
        client = _SlowFirstClient(payloads)
        client.show_reference_payload = _abc_reference_record()
        client.show_referencefiles_payload = rows  # type: ignore[assignment]
        provider = provider_from_fake(client)
        reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
        listing = await list_reference_files(
            provider=provider, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            limits=_limits(), check_limit=1, load_stored=store.load, save_assessment=store.save,
            route=ListingRoute.IDENTIFIER, provider_observations={}, find_paper_in_library=_not_in_library,
        )
        return listing, client

    payloads = {"21": _http_error(413), "31": GOOD_TABLE, "41": GOOD_TABLE}
    slow_first = asyncio.create_task(run_listing(payloads))
    await first_started.wait()

    # Second overlapping refresh reserves file 30 and completes before the slow first request finishes.
    second_listing, second_client = await run_listing(payloads)
    assert [kwargs["artifact_id"] for name, kwargs in second_client.calls if name == "download_referencefile"] == ["31"]
    assert _by_id(second_listing)["30"].check.status is ListedFileCheckStatus.PASSED

    # Completing the slow first request late must not rewind the cursor back to file 20.
    release_first.set()
    first_listing, first_client = await slow_first
    assert [kwargs["artifact_id"] for name, kwargs in first_client.calls if name == "download_referencefile"] == ["21"]
    assert _by_id(first_listing)["20"].check.reason is FileReason.TOO_LARGE

    # Third refresh advances directly to file 40 instead of re-checking oversized file 20.
    third_listing, third_client = await run_listing(payloads)
    assert [kwargs["artifact_id"] for name, kwargs in third_client.calls if name == "download_referencefile"] == ["41"]
    assert _by_id(third_listing)["40"].check.status is ListedFileCheckStatus.PASSED
    assert _by_id(third_listing)["40"].tickable is True


@pytest.mark.asyncio
async def test_listing_refresh_cursor_is_isolated_by_access_scope():
    store = _Store()
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(30, "FB")]
    payloads = {"21": _http_error(413), "31": GOOD_TABLE}

    # Curator A sees both 20 (oversized, WB) and 30 (valid, FB).
    first_a, client_a1 = await _listing(rows, groups=("WB", "FB"), payloads=payloads, store=store, check_limit=1)
    assert _by_id(first_a)["20"].check.reason is FileReason.TOO_LARGE
    assert _by_id(first_a)["30"].check.status is ListedFileCheckStatus.CHECK_LIMIT_REACHED
    assert [kwargs["artifact_id"] for name, kwargs in client_a1.calls if name == "download_referencefile"] == ["21"]

    # Curator B sees only 20 (WB) and must not clear or rewind Curator A's cursor.
    first_b, client_b = await _listing(rows, groups=("WB",), payloads=payloads, store=store, check_limit=1)
    assert set(_by_id(first_b)) == {"10", "20"}
    assert _by_id(first_b)["20"].check.reason is FileReason.TOO_LARGE
    assert [kwargs["artifact_id"] for name, kwargs in client_b.calls if name == "download_referencefile"] == ["21"]

    # Curator A's second refresh (even with different group order/casing) advances to 30.
    second_a, client_a2 = await _listing(rows, groups=("fb", "wb"), payloads=payloads, store=store, check_limit=1)
    assert _by_id(second_a)["30"].check.status is ListedFileCheckStatus.PASSED
    assert _by_id(second_a)["30"].tickable is True
    assert [kwargs["artifact_id"] for name, kwargs in client_a2.calls if name == "download_referencefile"] == ["31"]


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
            route=ListingRoute.IDENTIFIER, provider_observations={}, find_paper_in_library=_not_in_library,
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
    store = _Store()
    pdf = _pair(20, "WB", extension="pdf", content=two_titles)
    office = _pair(20, "WB", content=two_titles)
    for rows, expected in [(pdf, None), (office, FileReason.TITLE_COUNT)]:
        listing, _ = await _listing(rows, payloads={"21": two_titles}, store=store)
        assert _by_id(listing)["20"].check.reason is expected

    assert len(store.saved) == 2
    listing, _ = await _listing(pdf, store=store)
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.PASSED


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
    listing, _ = await _listing(rows, payloads={"21": GOOD_TABLE})
    files = _by_id(listing)
    supplement = files["20"]
    assert supplement.md5 == "original-md5"
    assert supplement.converted_md5 == hashlib.md5(GOOD_TABLE).hexdigest()
    # Core formats only: an Office original is "unknown"; a PDF supplement is "pdf".
    assert supplement.artifact_format == "unknown"
    assert supplement.category is SupplementFileCategory.CONVERTIBLE
    assert supplement.single_title_required is True
    assert files["10"].category is None and files["10"].single_title_required is False

    entry = {item["provider_file_id"]: item for item in listing_to_payload(listing)["files"]}["20"]
    assert entry["md5"] == "original-md5" and entry["converted_md5"] == hashlib.md5(GOOD_TABLE).hexdigest()
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
        artifact_id: await download_provider_file(
            provider, artifact_id, request_bearer_token="curator-token", max_bytes=50, use=len,
        )
        for artifact_id in ("1", "2", "3", "4")
    }
    assert results["1"].result == 4 and results["1"].failure is None
    assert results["2"].failure is ProviderFileDownloadFailure.MISSING
    assert results["3"].failure is ProviderFileDownloadFailure.UNAVAILABLE
    assert results["4"].failure is ProviderFileDownloadFailure.ACCESS_DENIED
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
    first = [asyncio.create_task(download_provider_file(
        provider, str(i), request_bearer_token="t", max_bytes=10, use=use)) for i in range(3)]
    second = [asyncio.create_task(download_provider_file(
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
    rows = _pair(20, "WB", content=b"x" * 20)
    await _listing(rows, payloads={"21": b"x" * 20}, store=store, limits=_limits(max_bytes=10))
    listing, client = await _listing(rows, payloads={}, store=store, limits=_limits(max_bytes=10))
    assert _by_id(listing)["20"].check.reason is FileReason.TOO_LARGE
    assert not [name for name, _ in client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
async def test_an_unavailable_text_is_tried_once_per_listing():
    listing, client = await _listing(_pair(20, "WB"), payloads={"21": _http_error(502)})
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.TEXT_UNAVAILABLE
    assert len([name for name, _ in client.calls if name == "download_referencefile"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reporter_fails", [False, True])
async def test_listing_aggregates_download_outages(monkeypatch, caplog, reporter_fails):
    from src.lib.observability import runtime
    from tests.unit.api.test_document_source_file_listing_endpoints import _capture_sentry_events

    private_id = 987654321
    outage = ABCLiteratureHTTPError("private storage diagnostic", status_code=503, endpoint="download_file")
    reporter = Mock(wraps=runtime.report_runtime_exception)
    if reporter_fails:
        reporter.side_effect = RuntimeError("private reporter detail")
    monkeypatch.setattr(runtime, "report_runtime_exception", reporter)
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(30, "WB"), *_pair(private_id, "WB")]
    with _capture_sentry_events(monkeypatch, "/private/listing/url") as events:
        listing, _ = await _listing(rows, payloads={"21": GOOD_TABLE, "31": outage, str(private_id + 1): outage})

    files = _by_id(listing)
    assert files["20"].check.status is ListedFileCheckStatus.PASSED
    assert files["30"].check.status is files[str(private_id)].check.status is ListedFileCheckStatus.TEXT_UNAVAILABLE
    reporter.assert_called_once()
    records = [record for record in caplog.records if record.name == file_listing.__name__]
    assert len(records) == (2 if reporter_fails else 1)
    assert all(record.sentry_skip_event and record.exc_info is None for record in records)
    for secret in (str(private_id), str(private_id + 1), "Made_up_S", "AGRKB:101", "curator-token",
                   str(outage), "private reporter detail", "/private/listing/url"):
        assert secret not in repr(events) and secret not in caplog.text
    if reporter_fails:
        assert events == []
    else:
        event, = events
        assert "request" not in event
        assert event["level"] == "warning"
        assert event["fingerprint"] == ["file_listing_download_unavailable"]
        assert event["contexts"]["runtime_exception"] == {
            "component": "document_source", "operation": "file_listing_download_unavailable",
            "unavailable_count": 2, "checked_count": 3,
        }


@pytest.mark.asyncio
async def test_listing_ordinary_refusals_are_quiet(monkeypatch):
    report = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    rows = [row for file_id in (20, 30, 40) for row in _pair(file_id, "WB")]
    listing, _ = await _listing(rows, payloads={"21": _http_error(403), "31": _http_error(404), "41": _http_error(413)})
    files = _by_id(listing)
    assert files["20"].check.status is ListedFileCheckStatus.TEXT_ACCESS_DENIED
    assert files["30"].check.status is ListedFileCheckStatus.TEXT_MISSING
    assert files["40"].check.reason is FileReason.TOO_LARGE
    report.assert_not_called()


@pytest.mark.asyncio
async def test_a_pdf_supplement_with_merged_and_legacy_tei_texts_lists_the_merged_one():
    """As ABC produces it: both texts name one PDF supplement, the legacy ``_tei`` row has the higher id."""

    original, merged = _pair(20, "WB", extension="pdf")
    merged["display_name"] = "Made_up_S20_merged"
    tei = dict(merged, referencefile_id=25, md5sum="md5-25", display_name="Made_up_S20_tei")
    rows = [_abc_show_all_main_pdf(10, "WB"), original, merged, tei, *_pair(30, "WB")]
    listing, client = await _listing(rows, payloads={"21": GOOD_TABLE, "31": GOOD_TABLE})
    files = _by_id(listing)
    assert files["20"].converted_text_id == "21" and files["20"].converted_md5 == merged["md5sum"]
    assert files["30"].check.status is ListedFileCheckStatus.PASSED and "10" in files
    assert sorted(kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile") == [
        "21", "31",
    ]


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
            provider_observations={},
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("groups", "licence_open", "licence_name", "bearer_kind"),
    [
        (("WB",), None, None, ProviderBearerKind.CURATOR),
        (("FB",), None, None, ProviderBearerKind.CURATOR),
        ((), True, "CC BY 4.0", ProviderBearerKind.CURATOR),
        (("WB",), None, "Restricted License", ProviderBearerKind.CURATOR),
        (("WB",), False, "Proprietary", ProviderBearerKind.SERVICE),
    ],
    ids=["authorized_group", "other_group", "open_access_licence", "refused_licence", "service_bearer"],
)
async def test_listing_from_prefetched_artifacts_matches_fresh_listing_without_provider_reads(
    groups, licence_open, licence_name, bearer_kind,
):
    """ALL-1751: reusing already-fetched artifacts and resolved reference gives identical
    filtering (groups, licence, bearer kind, MD5, stored-check reuse) with zero extra reads."""
    upper_md5 = hashlib.md5(GOOD_TABLE).hexdigest().upper()
    wb_pair = _pair(20, "WB", md5="  ORIG-20  ", content=GOOD_TABLE)
    wb_pair[1]["md5sum"] = f" {upper_md5} "
    mixed_access_pair = _pair(30, "WB", converted_mods=("FB",), content=GOOD_TABLE)
    fb_pair = _pair(40, "FB", content=GOOD_TABLE)
    rows = [_abc_show_all_main_pdf(10, "WB"), *wb_pair, *mixed_access_pair, *fb_pair]
    payloads = {"21": GOOD_TABLE, "31": GOOD_TABLE, "41": GOOD_TABLE}

    fresh_client = _Client(payloads)
    fresh_client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=licence_open, copyright_license_name=licence_name,
    )
    fresh_client.show_referencefiles_payload = rows  # type: ignore[assignment]
    fresh_provider = provider_from_fake(fresh_client)
    reference = await fresh_provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    fresh_store = _Store()
    fresh = await list_reference_files(
        provider=fresh_provider, reference=reference, authorized_group_ids=groups,
        bearer_kind=bearer_kind, request_bearer_token="curator-token", files_in_paper=("10",),
        limits=_limits(), check_limit=20, load_stored=fresh_store.load, save_assessment=fresh_store.save,
        route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
    )
    assert fresh.reference == reference
    assert "reference" not in listing_to_payload(fresh)

    # Fetch artifacts once with the resolved reference (as the wait's cheap read does),
    # then build the listing from those rows with zero resolve_reference / list_artifacts calls.
    reused_client = _Client(payloads)
    reused_client.show_referencefiles_payload = rows  # type: ignore[assignment]
    reused_provider = provider_from_fake(reused_client)
    artifacts = await reused_provider.list_artifacts(reference, request_bearer_token="curator-token")
    reused_client.calls.clear()

    reused_store = _Store()
    reused = await list_reference_files(
        provider=reused_provider, reference=reference, authorized_group_ids=groups,
        bearer_kind=bearer_kind, request_bearer_token="curator-token", files_in_paper=("10",),
        limits=_limits(), check_limit=20, load_stored=reused_store.load, save_assessment=reused_store.save,
        route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
        artifacts=artifacts,
    )

    assert reused == fresh
    assert listing_to_payload(reused) == listing_to_payload(fresh)
    assert not [name for name, _ in reused_client.calls if name in ("show_reference", "show_referencefiles")]

    # A second reused-rows listing reuses stored checks and downloads nothing.
    reused_client.calls.clear()
    reused_cached = await list_reference_files(
        provider=reused_provider, reference=reference, authorized_group_ids=groups,
        bearer_kind=bearer_kind, request_bearer_token="curator-token", files_in_paper=("10",),
        limits=_limits(), check_limit=20, load_stored=reused_store.load, save_assessment=reused_store.save,
        route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
        artifacts=artifacts,
    )
    assert reused_cached == fresh
    assert reused_client.calls == []


@pytest.mark.asyncio
async def test_reused_listing_rejects_changed_bytes_and_bad_artifacts_without_falling_back():
    from src.lib.document_sources.models import SourceArtifact, SourceReference

    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")]
    client = _Client({"21": GOOD_TABLE + b"changed"})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    artifacts = await provider.list_artifacts(reference, request_bearer_token="curator-token")
    client.calls.clear()
    store = _Store()

    listing = await list_reference_files(
        provider=provider, reference=reference, authorized_group_ids=("WB",),
        bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
        files_in_paper=("10",), limits=_limits(), check_limit=20,
        load_stored=store.load, save_assessment=store.save,
        route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
        artifacts=artifacts,
    )
    assert _by_id(listing)["20"].check.status is ListedFileCheckStatus.NOT_CHECKED
    assert store.saved == {}
    assert [name for name, _ in client.calls] == ["download_referencefile"]

    client.calls.clear()
    for bad_artifacts in (
        [SourceArtifact(provider="other_provider", artifact_id="20", reference_id="101")],
        [SourceArtifact(provider="abc_literature", artifact_id="20", reference_id="999")],
        [SourceArtifact(provider="abc_literature", artifact_id="20", reference_curie="AGRKB:999")],
        [SourceArtifact(provider="abc_literature", artifact_id="20", reference_id=None, reference_curie=None)],
        [SourceArtifact(provider="abc_literature", artifact_id="20", reference_id="   ", reference_curie="  ")],
        ["not-an-artifact"],
        bytearray(b"not-artifacts"),
    ):
        with pytest.raises(FileListingError):
            await list_reference_files(
                provider=provider, reference=reference, authorized_group_ids=("WB",),
                bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
                files_in_paper=("10",), limits=_limits(), check_limit=20,
                load_stored=store.load, save_assessment=store.save,
                route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
                artifacts=bad_artifacts,  # type: ignore[arg-type]
            )
    assert client.calls == []

    wrong_provider_ref = SourceReference(provider="other_provider", reference_id="101", reference_curie="AGRKB:101")
    with pytest.raises(FileListingError):
        await list_reference_files(
            provider=provider, reference=wrong_provider_ref, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            files_in_paper=("10",), limits=_limits(), check_limit=20,
            load_stored=store.load, save_assessment=store.save,
            route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
            artifacts=artifacts,
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_list_files_for_paper_from_artifacts_reuses_reference_and_rows_without_fallback(monkeypatch):
    from src.lib.document_sources import file_listing_service
    from src.lib.document_sources.access import DocumentSourceRequestContext
    from src.lib.document_sources.file_listing_service import FileListingUnavailable
    from src.lib.document_sources.models import SourceReference

    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")]
    client = _Client({"21": GOOD_TABLE})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    store = _Store()
    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", _limits)
    monkeypatch.setattr(
        file_listing_service, "_paper_lookup",
        lambda *_: file_listing_service._PaperSource(
            provider=provider.provider_id, reference_id="101", reference_curie="AGRKB:101", file_ids=("10",),
            provider_observations={},
        ),
    )
    context = DocumentSourceRequestContext(
        provider_groups=("WB",), authorized_group_ids=("WB",), bearer_kind=ProviderBearerKind.CURATOR,
        curator_token="curator-token",
    )

    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    artifacts = await provider.list_artifacts(reference, request_bearer_token="curator-token")
    client.calls.clear()

    reused = await file_listing_service.list_files_for_paper_from_artifacts(
        uuid4(), owner_user_id=7, provider=provider, context=context,
        reference=reference, artifacts=artifacts,
    )
    assert _by_id(reused)["20"].check.status is ListedFileCheckStatus.PASSED
    assert reused.reference == reference
    assert [name for name, _ in client.calls] == ["download_referencefile"]

    # Stored check is reused on a subsequent call: zero ABC requests at all.
    client.calls.clear()
    cached = await file_listing_service.list_files_for_paper_from_artifacts(
        uuid4(), owner_user_id=7, provider=provider, context=context,
        reference=reference, artifacts=artifacts,
    )
    assert cached == reused
    assert client.calls == []

    # Mismatched reference id or missing artifacts fail with named errors and zero ABC calls.
    wrong_ref = SourceReference(provider=provider.provider_id, reference_id="999", reference_curie="AGRKB:999")
    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service.list_files_for_paper_from_artifacts(
            uuid4(), owner_user_id=7, provider=provider, context=context,
            reference=wrong_ref, artifacts=artifacts,
        )
    assert raised.value.code == "paper_reference_mismatch"

    with pytest.raises(FileListingError):
        await file_listing_service.list_files_for_paper_from_artifacts(
            uuid4(), owner_user_id=7, provider=provider, context=context,
            reference=reference, artifacts=None,  # type: ignore[arg-type]
        )

    other_provider_ref = SourceReference(provider="other_provider", reference_id="101", reference_curie="AGRKB:101")
    with pytest.raises(FileListingError):
        await file_listing_service.list_files_for_paper_from_artifacts(
            uuid4(), owner_user_id=7, provider=provider, context=context,
            reference=other_provider_ref, artifacts=artifacts,
        )

    no_token_context = DocumentSourceRequestContext(
        provider_groups=("WB",), authorized_group_ids=("WB",), bearer_kind=ProviderBearerKind.CURATOR,
        curator_token=None,
    )
    with pytest.raises(FileListingUnavailable) as no_token:
        await file_listing_service.list_files_for_paper_from_artifacts(
            uuid4(), owner_user_id=7, provider=provider, context=no_token_context,
            reference=reference, artifacts=artifacts,
        )
    assert no_token.value.code == "document_source_curator_token_unavailable"

    monkeypatch.setattr(
        file_listing_service, "_paper_lookup",
        lambda *_: file_listing_service._PaperSource(
            provider=None, reference_id=None, reference_curie=None, file_ids=(), provider_observations={},
        ),
    )
    with pytest.raises(FileListingUnavailable) as uploaded:
        await file_listing_service.list_files_for_paper_from_artifacts(
            uuid4(), owner_user_id=7, provider=provider, context=context,
            reference=reference, artifacts=artifacts,
        )
    assert uploaded.value.code == "not_imported_from_provider"
    assert client.calls == []


@pytest.mark.asyncio
async def test_401_during_check_downloads_stops_queued_downloads_immediately(monkeypatch):
    """ALL-1751 review: with download concurrency=1 and 3 unchecked files, a 401 on the first
    check download aborts queued checks immediately (strictly 1 download call, not 3)."""
    import weakref

    from src.lib.document_sources import file_listing
    from src.lib.document_sources.models import DocumentSourceLoginExpired

    monkeypatch.setenv("DOCUMENT_FILE_DOWNLOAD_CONCURRENCY", "1")
    monkeypatch.setattr(file_listing, "_download_limiters", weakref.WeakKeyDictionary())

    rows = [
        _abc_show_all_main_pdf(10, "WB"),
        *_pair(20, "WB"),
        *_pair(30, "WB"),
        *_pair(40, "WB"),
    ]
    client = _Client({"21": _http_error(401), "31": GOOD_TABLE, "41": GOOD_TABLE})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    store = _Store()
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    artifacts = await provider.list_artifacts(reference, request_bearer_token="curator-token")
    client.calls.clear()

    with pytest.raises(DocumentSourceLoginExpired):
        await list_reference_files(
            provider=provider, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            files_in_paper=("10",), limits=_limits(), check_limit=20,
            load_stored=store.load, save_assessment=store.save,
            route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
            artifacts=artifacts,
        )

    assert [kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile"] == ["21"]


@pytest.mark.asyncio
async def test_401_during_check_downloads_reports_drained_sibling_failure_once_and_reraises_401(monkeypatch):
    """ALL-1751 review: an unexpected sibling check failure drained alongside a 401 is reported
    once (sanitized, without blocking re-raise) before DocumentSourceLoginExpired propagates."""
    import asyncio
    import weakref

    from src.lib.document_sources import file_listing
    from src.lib.document_sources.models import DocumentSourceLoginExpired

    monkeypatch.setenv("DOCUMENT_FILE_DOWNLOAD_CONCURRENCY", "2")
    monkeypatch.setattr(file_listing, "_download_limiters", weakref.WeakKeyDictionary())

    both_started = asyncio.Event()
    started = 0

    class _ConcurrentClient(_Client):
        async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
            nonlocal started
            started += 1
            if started >= 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=5)
            return await super().download_referencefile(
                artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
            )

    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(30, "WB")]
    client = _ConcurrentClient({"21": _http_error(401), "31": GOOD_TABLE})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    artifacts = await provider.list_artifacts(reference, request_bearer_token="curator-token")
    client.calls.clear()

    secret = "secret-db-token-xyz999"

    def _failing_save(*_args):
        raise RuntimeError(f"assessment store broke: {secret}")

    reported = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: reported.append((exc, kwargs)),
    )

    with pytest.raises(DocumentSourceLoginExpired):
        await list_reference_files(
            provider=provider, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            files_in_paper=("10",), limits=_limits(), check_limit=20,
            load_stored=lambda *_: None, save_assessment=_failing_save,
            route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
            artifacts=artifacts,
        )

    assert len(reported) == 1
    exc, kwargs = reported[0]
    assert kwargs["operation"] == "file_listing_drained_check_failed"
    assert kwargs["include_request"] is False
    assert kwargs["context"] == {"exception_type": "RuntimeError"}
    assert secret not in repr((str(exc), kwargs))

    # Reporter failure itself must never block re-raising DocumentSourceLoginExpired.
    started = 0
    both_started = asyncio.Event()
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sentry down")),
    )
    with pytest.raises(DocumentSourceLoginExpired):
        await list_reference_files(
            provider=provider, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            files_in_paper=("10",), limits=_limits(), check_limit=20,
            load_stored=lambda *_: None, save_assessment=_failing_save,
            route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
            artifacts=artifacts,
        )


@pytest.mark.asyncio
async def test_non_401_provider_error_reports_drained_sibling_store_failure_once_and_propagates(monkeypatch):
    """ALL-1751 review: when check 1 raises a provider DocumentSourceError and check 2 raises an
    unexpected store failure concurrently, check 1 propagates while check 2 is reported once
    (sanitized, no secret leak); duplicate provider errors do not spam Sentry."""
    import asyncio
    import weakref

    from src.lib.document_sources import file_listing
    from src.lib.document_sources.models import DocumentSourceError

    monkeypatch.setenv("DOCUMENT_FILE_DOWNLOAD_CONCURRENCY", "2")
    monkeypatch.setattr(file_listing, "_download_limiters", weakref.WeakKeyDictionary())

    both_started = asyncio.Event()
    started = 0

    class _ConcurrentClient(_Client):
        async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
            nonlocal started
            started += 1
            if started >= 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=5)
            return await super().download_referencefile(
                artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
            )

    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(30, "WB")]
    client = _ConcurrentClient({"21": _http_error(500), "31": GOOD_TABLE})
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(client)
    reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
    artifacts = await provider.list_artifacts(reference, request_bearer_token="curator-token")
    client.calls.clear()

    secret = "secret-store-token-xyz777"

    def _failing_save(*_args):
        raise RuntimeError(f"assessment store broke: {secret}")

    reported = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: reported.append((exc, kwargs)),
    )

    with pytest.raises(DocumentSourceError):
        await list_reference_files(
            provider=provider, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            files_in_paper=("10",), limits=_limits(), check_limit=20,
            load_stored=lambda *_: None, save_assessment=_failing_save,
            route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
            artifacts=artifacts,
        )

    assert len(reported) == 1
    exc, kwargs = reported[0]
    assert kwargs["operation"] == "file_listing_drained_check_failed"
    assert kwargs["include_request"] is False
    assert kwargs["context"] == {"exception_type": "RuntimeError"}
    assert secret not in repr((str(exc), kwargs))

    # Two concurrent provider errors propagate the first without reporting the sibling to Sentry.
    reported.clear()
    started = 0
    both_started = asyncio.Event()
    client_both_500 = _ConcurrentClient({"21": _http_error(500), "31": _http_error(500)})
    client_both_500.show_reference_payload = _abc_reference_record()
    client_both_500.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider_both_500 = provider_from_fake(client_both_500)
    with pytest.raises(DocumentSourceError):
        await list_reference_files(
            provider=provider_both_500, reference=reference, authorized_group_ids=("WB",),
            bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
            files_in_paper=("10",), limits=_limits(), check_limit=20,
            load_stored=lambda *_: None, save_assessment=lambda *_: None,
            route=ListingRoute.PAPER, provider_observations={}, find_paper_in_library=None,
            artifacts=artifacts,
        )
    assert reported == []
