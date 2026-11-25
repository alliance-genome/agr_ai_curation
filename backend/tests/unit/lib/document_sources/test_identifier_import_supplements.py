"""Importing a new paper with chosen supplements: the request side (KANBAN-1976 / ALL-1460, P7b).

The fakes are the importer tests' own (``test_identifier_import``). All values are made up.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.identifier_import import (
    FILE_NOT_TICKABLE_MESSAGE,
    IdentifierImportService,
    SupplementSelectionError,
    select_reference_import_candidate,
)
from src.lib.document_sources.models import (
    ProviderBearerKind,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.document_sources.supplement_plan import ChosenSupplement
from src.models.api_schemas import DocumentSourceSupplementChoice
from tests.unit.lib.document_sources.test_identifier_import import (
    _DispatchRecorder,
    _FakeConversionProvider,
    _FakeProvider,
    _FakeSession,
    _provider_metadata_artifact,
    _ready_artifacts,
    _source_artifact,
)

EXPIRES = 1_900_000_000


async def _signed_in_user(monkeypatch):
    """The user the real auth dependency returns for a validated login expiring at EXPIRES."""

    import importlib
    from types import SimpleNamespace as _NS

    from fastapi.security import SecurityScopes

    auth_api = importlib.import_module("src.api.auth")

    class _Provider:
        async def validate_token(self, _token):
            return {"iss": "https://identity.example.org/pool", "exp": EXPIRES}

        def extract_principal(self, _claims):
            return _NS(subject="user-1", email="u@example.org", display_name="User 1",
                       provider="cognito", groups=["FBStaff"])

    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(auth_api, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_api, "is_auth_configured", lambda: True)
    monkeypatch.setattr(auth_api, "_get_provider_or_503", lambda: _Provider())
    return await auth_api._get_user_from_cookie_impl(
        _NS(headers={}, cookies={"auth_token": "made-up-token"}, base_url="https://app.example.org/"),
        SecurityScopes(),
    )


def _running_answer():
    return SourceConversionResult(
        provider="abc_literature", status=SourceConversionStatus.RUNNING,
        reference_curie="AGRKB:123", job_id="abc-job-1",
    )


def _service(provider, tmp_path, dispatch, provision=None, created_jobs=None):
    sessions = []

    async def _create_document(user_sub, document):
        return {"success": True, "document_id": document.id}

    def _create_job(**kwargs):
        if created_jobs is not None:
            created_jobs.append(kwargs)
        return SimpleNamespace(job_id="job-1")

    return IdentifierImportService(
        upload_execution_service=dispatch,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=provision or AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=lambda: provider,
        create_document_fn=_create_document,
        create_job_fn=_create_job,
        get_document_fn=lambda *_args, **_kwargs: None,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )


def _context(bearer_kind=ProviderBearerKind.CURATOR):
    return DocumentSourceRequestContext(
        bearer_kind=bearer_kind, provider_groups=("FBStaff",), authorized_group_ids=("FB",),
        curator_token="curator-token",
    )


def _choice(provider_file_id, *, identifier="123", converted_md5=None, display_name=None):
    return DocumentSourceSupplementChoice(
        identifier=identifier, provider_file_id=provider_file_id,
        md5=f"md5-{provider_file_id}", converted_md5=converted_md5,
        display_name=display_name,
    )


def _supplement_artifact(artifact_id: str, display_name: str | None, *, group_ids: tuple[str, ...] = ("FB",)):
    from src.lib.document_sources.models import (
        SourceAccessPolicy,
        SourceAccessScope,
        SourceArtifact,
        SourceArtifactFormat,
        SourceArtifactRole,
        SourceArtifactStatus,
    )

    return SourceArtifact(
        provider="abc_literature",
        artifact_id=artifact_id,
        role=SourceArtifactRole.SOURCE_SUPPLEMENT,
        artifact_format=SourceArtifactFormat.UNKNOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="123",
        reference_curie="AGRKB:123",
        display_name=display_name,
        md5sum=f"md5-{artifact_id}",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=group_ids),
    )


@pytest.mark.asyncio
async def test_an_import_that_asks_abc_carries_the_chosen_supplements_and_the_login_expiry(tmp_path, monkeypatch):
    provider = _FakeConversionProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _provider_metadata_artifact(),
            _supplement_artifact("30", "Table_S1.xlsx"),
            _supplement_artifact("40", "Figure_S2.pdf"),
        ),
        conversion_result=_running_answer(),
    )
    provider.provider_id = "abc_literature"
    dispatch = _DispatchRecorder()
    created_jobs = []

    result = await _service(provider, tmp_path, dispatch, created_jobs=created_jobs).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123",
        user=await _signed_in_user(monkeypatch), document_source_context=_context(),
        supplements=[_choice("30"), _choice("40", converted_md5="md5-41")],
    )

    assert result.results[0].status == "imported"
    request = dispatch.provider_conversion_calls[0]["request"]
    assert request.login_expires_at == float(EXPIRES)
    plan = request.supplements
    assert plan.chosen == (
        ChosenSupplement(provider_file_id="30", md5="md5-30", converted_md5=None, display_name="Table_S1.xlsx"),
        ChosenSupplement(provider_file_id="40", md5="md5-40", converted_md5="md5-41", display_name="Figure_S2.pdf"),
    )
    assert (plan.owner_user_id, plan.reference, plan.login_expires_at) == (42, "AGRKB:123", float(EXPIRES))
    assert plan.request_context.authorized_group_ids == ("FB",)
    # G3: accepted import job records initial queued supplement entries with real display_name.
    metadata = created_jobs[0]["metadata"]
    assert "on_demand_sent_at" in metadata["provider_conversion_requests"]
    assert metadata["supplements"] == [
        {
            "provider_file_id": "30",
            "display_name": "Table_S1.xlsx",
            "state": "queued",
            "reason_code": None,
            "observed_at": None,
            "file_id": None,
            "measured_value": None,
            "limit_value": None,
        },
        {
            "provider_file_id": "40",
            "display_name": "Figure_S2.pdf",
            "state": "queued",
            "reason_code": None,
            "observed_at": None,
            "file_id": None,
            "measured_value": None,
            "limit_value": None,
        },
    ]


@pytest.mark.asyncio
async def test_an_import_without_supplements_dispatches_as_today(tmp_path):
    provider = _FakeConversionProvider(
        artifacts=(_source_artifact(artifact_id="pdf-1"),), conversion_result=_running_answer(),
    )
    dispatch = _DispatchRecorder()
    created_jobs = []

    await _service(provider, tmp_path, dispatch, created_jobs=created_jobs).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123", user={"sub": "user-1"},
        document_source_context=_context(),
    )

    request = dispatch.provider_conversion_calls[0]["request"]
    assert request.supplements is None
    assert request.login_expires_at is None  # no exp in these claims
    # Imports with no chosen supplements must not write an empty supplements list.
    assert "supplements" not in (created_jobs[0].get("metadata") or {})


@pytest.mark.asyncio
async def test_display_name_resolution_prefers_authorized_listing_and_never_falls_back_to_id(tmp_path):
    provider = _FakeProvider(
        artifacts=(
            *_ready_artifacts(),
            _provider_metadata_artifact(),
            _supplement_artifact("40", "Authoritative_S40.xlsx", group_ids=("FB",)),
            _supplement_artifact("50", "Secret_Other_Group.xlsx", group_ids=("OTHER",)),
            _supplement_artifact("60", None, group_ids=("FB",)),
        ),
    )
    dispatch = _DispatchRecorder()
    created_jobs = []

    await _service(provider, tmp_path, dispatch, created_jobs=created_jobs).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123",
        user={"sub": "user-1"}, document_source_context=_context(),
        supplements=[
            _choice("40", converted_md5="md5-41", display_name="Stale_Caller_Name.xlsx"),
            _choice("50", converted_md5="md5-51"),
            _choice("60", converted_md5="md5-61"),
            _choice("70", converted_md5="md5-71", display_name="Caller_Provided.docx"),
        ],
    )

    plan = dispatch.provider_markdown_calls[0]["request"].supplements
    assert [(item.provider_file_id, item.display_name) for item in plan.chosen] == [
        ("40", "Authoritative_S40.xlsx"),
        ("50", None),  # unauthorized artifact display_name is never leaked
        ("60", None),  # unnamed artifact stays None, never "60"
        ("70", "Caller_Provided.docx"),
    ]
    assert [entry["display_name"] for entry in created_jobs[0]["metadata"]["supplements"]] == [
        "Authoritative_S40.xlsx",
        None,
        None,
        "Caller_Provided.docx",
    ]


@pytest.mark.asyncio
async def test_an_unconverted_file_is_not_tickable_when_this_import_does_not_ask_abc(tmp_path):
    """The main text is converted (READY, no request): an unconverted supplement
    would never be converted by this import."""

    provider = _FakeProvider(artifacts=(*_ready_artifacts(), _provider_metadata_artifact()))
    dispatch = _DispatchRecorder()

    result = await _service(provider, tmp_path, dispatch).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123",
        user={"sub": "user-1"}, document_source_context=_context(),
        supplements=[_choice("30")],
    )

    row = result.results[0]
    assert (row.status, row.error_code, row.message) == ("error", "file_not_tickable", FILE_NOT_TICKABLE_MESSAGE)
    assert dispatch.provider_markdown_calls == dispatch.upload_calls == dispatch.provider_conversion_calls == []


@pytest.mark.asyncio
async def test_a_converted_file_on_a_converted_main_goes_through_without_a_wait(tmp_path):
    provider = _FakeProvider(artifacts=(*_ready_artifacts(), _provider_metadata_artifact()))
    dispatch = _DispatchRecorder()

    await _service(provider, tmp_path, dispatch).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123",
        user={"sub": "user-1"}, document_source_context=_context(),
        supplements=[_choice("40", converted_md5="md5-41")],
    )

    request = dispatch.provider_markdown_calls[0]["request"]
    assert request.supplements.chosen[0].provider_file_id == "40"
    assert (request.conversion_requested, request.last_conversion_answer) == (False, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supplements", "code", "expected_limit"),
    [
        ([_choice("30", identifier="999")], "supplement_identifier_unknown", None),
        ([_choice("30"), _choice("30")], "file_chosen_twice", None),
        ([_choice(str(index)) for index in range(20)], "paper_file_limit", 20),  # 20 + the main
    ],
)
async def test_a_selection_that_cannot_be_imported_is_refused_before_anything_is_imported(
    tmp_path, monkeypatch, supplements, code, expected_limit
):
    monkeypatch.delenv("DOCUMENT_MAX_FILES_PER_PAPER", raising=False)
    provision = AsyncMock(return_value=SimpleNamespace(id=42))
    provider = _FakeProvider(artifacts=_ready_artifacts())

    with pytest.raises(SupplementSelectionError) as refused:
        await _service(provider, tmp_path, _DispatchRecorder(), provision).import_identifiers(
            background_tasks=BackgroundTasks(), identifiers="123", user={"sub": "user-1"},
            document_source_context=_context(), supplements=supplements,
        )

    assert refused.value.code == code
    assert refused.value.limit_value == expected_limit
    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_machine_reader_has_no_curator_login_to_expire(tmp_path):
    provider = _FakeProvider(artifacts=(*_ready_artifacts(), _provider_metadata_artifact()))
    dispatch = _DispatchRecorder()

    await _service(provider, tmp_path, dispatch).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123",
        user={"sub": "dev-user"},
        document_source_context=_context(ProviderBearerKind.SERVICE),
        supplements=[_choice("40", converted_md5="md5-41")],
    )

    request = dispatch.provider_markdown_calls[0]["request"]
    assert request.login_expires_at is None
    assert request.supplements.login_expires_at is None


@pytest.mark.asyncio
async def test_a_decision_that_sent_the_request_keeps_abc_s_answer():
    answer = _running_answer()
    provider = _FakeConversionProvider(
        artifacts=(_source_artifact(artifact_id="pdf-1"),), conversion_result=answer,
    )

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR, provider=provider, identifier="PMID:123",
        authorized_group_ids=("FB",), request_bearer_token="curator-token",
    )

    assert decision.conversion_answer is answer


@pytest.mark.asyncio
async def test_a_decision_that_sent_no_request_has_no_answer():
    provider = _FakeProvider(artifacts=_ready_artifacts())

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR, provider=provider, identifier="PMID:123",
        authorized_group_ids=("FB",), request_bearer_token="curator-token",
    )

    assert decision.is_ready
    assert decision.conversion_answer is None



@pytest.mark.asyncio
async def test_an_expired_login_on_an_ordinary_import_shows_what_it_showed_before_and_is_not_reported(
    tmp_path, monkeypatch
):
    from src.lib.document_sources import identifier_import
    from src.lib.document_sources.models import DocumentSourceLoginExpired

    reported = []
    monkeypatch.setattr(identifier_import, "_report_source_failure", lambda exc, code: reported.append(code))

    class _Refusing(_FakeProvider):
        async def download_artifact(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
            raise DocumentSourceLoginExpired("ABC Literature artifact download failed")

    result = await _service(_Refusing(artifacts=_ready_artifacts()), tmp_path, _DispatchRecorder()).import_identifiers(
        background_tasks=BackgroundTasks(), identifiers="123", user={"sub": "user-1"},
        document_source_context=_context(),
    )

    row = result.results[0]
    assert (row.status, row.error_code, row.message) == (
        "error", "document_source_unavailable", "Document-source lookup is unavailable.",
    )
    assert reported == []
