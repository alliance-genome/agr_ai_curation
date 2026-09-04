"""Authenticated Workshop APIs for reusable closed generic profile revisions."""

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.auth import get_auth_dependency
from src.lib.agent_studio import generic_profile_service as service
from src.lib.agent_studio.profile_mapping_service import (
    ProfileMappingError, capability_catalog, capability_issues, validate_profile_mappings,
)
from src.lib.group_rules import get_groups_from_provider_groups
from src.models.sql import get_db
from src.schemas.generic_extraction_profile import GenericProfileContract
from src.services.user_service import set_global_user_from_cognito

router = APIRouter(prefix="/api/agent-studio/generic-profiles")


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProfileRequest(RequestModel):
    contract: GenericProfileContract
    visibility: Literal["private", "project"] = "private"
    project_id: UUID | None = None


class ReviseProfileRequest(RequestModel):
    contract: GenericProfileContract
    expected_revision: int = Field(ge=1)


class CloneProfileRequest(RequestModel):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1)


class ArchiveProfileRequest(RequestModel):
    expected_revision: int = Field(ge=1)


class ProfileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: int
    project_id: UUID | None
    visibility: Literal["private", "project"]
    name: str
    description: str
    semantic_class: str
    head_revision: int
    archived: bool
    created_at: datetime
    updated_at: datetime


class ProfileRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    profile_id: UUID
    revision: int
    fingerprint: str
    contract: GenericProfileContract
    creator_id: int
    created_at: datetime


class ProfileDetailResponse(BaseModel):
    profile: ProfileSummary
    revision: ProfileRevisionResponse
    compatibility: list[dict[str, Any]] = Field(default_factory=list)


class ProfileListResponse(BaseModel):
    profiles: list[ProfileSummary]
    next_cursor: UUID | None


class ProfileRevisionListResponse(BaseModel):
    revisions: list[ProfileRevisionResponse]
    next_cursor: int | None


class ValidatedProfileResponse(BaseModel):
    contract: GenericProfileContract
    fingerprint: str


@contextmanager
def _profile_errors(db: Session):
    try:
        yield
    except service.ProfileNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Profile not found") from exc
    except service.ProfileConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProfileMappingError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "profile_mapping_invalid", "issues": exc.issues}) from exc
    except IntegrityError as exc:
        db.rollback()
        # Never expose SQL parameters/contract bodies through database errors.
        raise HTTPException(
            status_code=409,
            detail="Profile could not be saved; reload and compare before retrying",
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _detail(profile, revision, compatibility=()) -> ProfileDetailResponse:
    return ProfileDetailResponse(
        profile=ProfileSummary.model_validate(profile),
        revision=ProfileRevisionResponse.model_validate(revision),
        compatibility=list(compatibility),
    )


@router.post("", response_model=ProfileDetailResponse, status_code=201)
def create_profile(
    request: CreateProfileRequest,
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        profile, revision = service.create_profile(
            db,
            user_id,
            request.contract,
            visibility=request.visibility,
            project_id=request.project_id,
            active_group_ids=get_groups_from_provider_groups(user.get("cognito:groups", [])),
        )
        db.commit()
        return _detail(profile, revision)


@router.get("", response_model=ProfileListResponse)
def list_profiles(
    after_id: UUID | None = None,
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        rows, next_cursor = service.list_profiles(db, user_id, after_id=after_id)
        return ProfileListResponse(
            profiles=[ProfileSummary.model_validate(row) for row in rows],
            next_cursor=next_cursor,
        )


@router.post("/validate", response_model=ValidatedProfileResponse)
def validate_profile(
    contract: GenericProfileContract,
    user: dict[str, Any] = get_auth_dependency(),
):
    """Validate a local draft without persisting it or creating a revision."""
    try:
        validate_profile_mappings(contract, active_group_ids=get_groups_from_provider_groups(user.get("cognito:groups", [])))
    except ProfileMappingError as exc:
        raise HTTPException(status_code=422, detail={"code": "profile_mapping_invalid", "issues": exc.issues}) from exc
    return ValidatedProfileResponse(
        contract=contract, fingerprint=contract.fingerprint()
    )


@router.get("/validator-capabilities")
def list_validator_capabilities(
    after: str | None = None,
    user: dict[str, Any] = get_auth_dependency(),
):
    """Stable package-owned slots/policies, including honest unavailable states."""
    from src.lib.openai_agents.config import get_generic_profile_list_page_size
    groups = get_groups_from_provider_groups(user.get("cognito:groups", []))
    capabilities = capability_catalog(active_group_ids=groups)
    if after is not None:
        capabilities = [cap for cap in capabilities if cap.key() > after]
    size = get_generic_profile_list_page_size()
    page = capabilities[:size]
    return {"capabilities": [{"capability_ref": cap.ref.model_dump(mode="json"),
        "fingerprint": cap.fingerprint(), "state": cap.binding.state.value,
        "selectable": not capability_issues(cap, groups), "diagnostics": capability_issues(cap, groups),
        "metadata": cap.binding.identity_details()} for cap in page],
        "next_cursor": page[-1].key() if len(capabilities) > size else None}


@router.get("/{profile_id}/revisions/{revision}/validator-mappings")
def inspect_validator_mappings(
    profile_id: UUID,
    revision: int = Path(ge=1),
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        saved = service.get_profile_revision(db, profile_id, revision, user_id, include_archived=True)
        groups = get_groups_from_provider_groups(user.get("cognito:groups", []))
        issues = []
        try:
            validate_profile_mappings(saved.contract, active_group_ids=groups)
        except ProfileMappingError as exc:
            issues = exc.issues
        mappings = saved.contract.get("validator_mappings", [])
        from sqlalchemy import select
        from src.models.sql.profile_validator_capability import ProfileValidatorCapability as Capability, ProfileValidatorCapabilityReference as Reference
        audit = list(db.execute(select(Capability.snapshot).join(Reference,
            Reference.capability_fingerprint == Capability.fingerprint).where(
                Reference.profile_revision_id == saved.id).order_by(Reference.mapping_id)).scalars()) if mappings else []
        return {"profile_revision_id": saved.id, "fingerprint": saved.fingerprint,
            "validator_mappings": mappings, "diagnostics": issues,
            "capability_snapshots": audit,
            "state": "unmapped" if not mappings else "unsupported" if issues else "compatible",
            "semantic_execution": "not_executed", "submission_readiness": "not_asserted"}


@router.get("/{profile_id}/revisions", response_model=ProfileRevisionListResponse)
def list_revisions(
    profile_id: UUID,
    before_revision: int | None = Query(None, ge=1),
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        rows, cursor = service.list_profile_revisions(
            db, profile_id, user_id, before_revision=before_revision
        )
        return ProfileRevisionListResponse(
            revisions=[ProfileRevisionResponse.model_validate(row) for row in rows],
            next_cursor=cursor,
        )


@router.get("/{profile_id}", response_model=ProfileDetailResponse)
def get_profile(
    profile_id: UUID,
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        profile = service.get_profile(db, profile_id, user_id, include_archived=True)
        revision = service.get_profile_revision(
            db, profile_id, profile.head_revision, user_id, include_archived=True
        )
        return _detail(profile, revision)


@router.get(
    "/{profile_id}/revisions/{revision}", response_model=ProfileRevisionResponse
)
def get_revision(
    profile_id: UUID,
    revision: int = Path(ge=1),
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        return ProfileRevisionResponse.model_validate(
            service.get_profile_revision(
                db,
                profile_id,
                revision,
                user_id,
                include_archived=True,
            )
        )


@router.post("/{profile_id}/revisions", response_model=ProfileDetailResponse)
def revise_profile(
    profile_id: UUID,
    request: ReviseProfileRequest,
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        profile, revision, compatibility = service.revise_profile(
            db,
            profile_id,
            user_id,
            request.contract,
            expected_revision=request.expected_revision,
            active_group_ids=get_groups_from_provider_groups(user.get("cognito:groups", [])),
        )
        db.commit()
        return _detail(profile, revision, compatibility)


@router.post(
    "/{profile_id}/clone", response_model=ProfileDetailResponse, status_code=201
)
def clone_profile(
    profile_id: UUID,
    request: CloneProfileRequest,
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        profile, revision = service.clone_profile(
            db, profile_id, request.revision, user_id, name=request.name,
            active_group_ids=get_groups_from_provider_groups(user.get("cognito:groups", [])),
        )
        db.commit()
        return _detail(profile, revision)


@router.post("/{profile_id}/archive", response_model=ProfileSummary)
def archive_profile(
    profile_id: UUID,
    request: ArchiveProfileRequest,
    user: dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    with _profile_errors(db):
        user_id = set_global_user_from_cognito(db, user).id
        profile = service.archive_profile(
            db, profile_id, user_id, expected_revision=request.expected_revision
        )
        db.commit()
        return ProfileSummary.model_validate(profile)
