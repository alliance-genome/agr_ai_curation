"""Authorized profile revision lifecycle; callers own the enclosing transaction."""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.lib.agent_studio.profile_compatibility import profile_compatibility
from src.lib.agent_studio.profile_mapping_service import validate_profile_mappings, persist_capability_references
from src.lib.openai_agents.config import get_generic_profile_list_page_size
from src.models.sql.generic_extraction_profile import (
    GenericExtractionProfile as Profile,
    GenericExtractionProfileRevision as Revision,
)
from src.schemas.generic_extraction_profile import normalize_profile_contract


class ProfileNotFoundError(ValueError):
    """Missing and unauthorized profiles share one non-disclosing response."""


class ProfileConflictError(ValueError):
    """The curator's expected revision is stale."""


def _project_ids(db: Session, user_id: int) -> list[UUID]:
    from src.lib.agent_studio.agent_service import get_project_ids_for_user

    return list(get_project_ids_for_user(db, user_id))


def _visibility(db: Session, user_id: int):
    return or_(
        and_(Profile.visibility == "private", Profile.owner_id == user_id),
        and_(
            Profile.visibility == "project",
            Profile.project_id.in_(_project_ids(db, user_id)),
        ),
    )


def get_profile(
    db: Session,
    profile_id: UUID,
    user_id: int,
    *,
    include_archived: bool = False,
    for_update: bool = False,
) -> Profile:
    query = select(Profile).where(Profile.id == profile_id, _visibility(db, user_id))
    if not include_archived:
        query = query.where(Profile.archived.is_(False))
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = db.execute(query).scalar_one_or_none()
    if result is None:
        raise ProfileNotFoundError("Profile not found")
    return result


def get_profile_revision(
    db: Session,
    profile_id: UUID,
    revision: int,
    user_id: int,
    *,
    include_archived: bool = False,
) -> Revision:
    get_profile(db, profile_id, user_id, include_archived=include_archived)
    row = db.execute(
        select(Revision).where(
            Revision.profile_id == profile_id,
            Revision.revision == revision,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProfileNotFoundError("Profile revision not found")
    parsed = normalize_profile_contract(row.contract)
    if parsed.fingerprint() != row.fingerprint:
        raise ValueError("Stored profile revision fingerprint mismatch")
    return row


def list_profiles(
    db: Session, user_id: int, *, after_id: UUID | None = None
) -> tuple[list[Profile], UUID | None]:
    query = select(Profile).where(_visibility(db, user_id), Profile.archived.is_(False))
    if after_id is not None:
        query = query.where(Profile.id > after_id)
    page_size = get_generic_profile_list_page_size()
    rows = list(db.execute(query.order_by(Profile.id).limit(page_size + 1)).scalars())
    next_id = rows[page_size - 1].id if len(rows) > page_size else None
    return rows[:page_size], next_id


def list_profile_revisions(
    db: Session,
    profile_id: UUID,
    user_id: int,
    *,
    before_revision: int | None = None,
) -> tuple[list[Revision], int | None]:
    get_profile(db, profile_id, user_id, include_archived=True)
    query = select(Revision).where(Revision.profile_id == profile_id)
    if before_revision is not None:
        query = query.where(Revision.revision < before_revision)
    page_size = get_generic_profile_list_page_size()
    rows = list(
        db.execute(
            query.order_by(Revision.revision.desc()).limit(page_size + 1)
        ).scalars()
    )
    next_revision = rows[page_size - 1].revision if len(rows) > page_size else None
    for row in rows[:page_size]:
        if normalize_profile_contract(row.contract).fingerprint() != row.fingerprint:
            raise ValueError("Stored profile revision fingerprint mismatch")
    return rows[:page_size], next_revision


def _set_visibility(
    db: Session, row: Profile, user_id: int, visibility: str, project_id: UUID | None
) -> None:
    if visibility == "private" and project_id is None:
        row.visibility, row.project_id = visibility, None
    elif visibility == "project" and project_id in _project_ids(db, user_id):
        row.visibility, row.project_id = visibility, project_id
    else:
        raise ValueError("Choose private visibility or a project you belong to")


def create_profile(
    db: Session,
    user_id: int,
    contract,
    *,
    visibility: str = "private",
    project_id: UUID | None = None,
    active_group_ids=(),
) -> tuple[Profile, Revision]:
    parsed = normalize_profile_contract(contract)
    capabilities = validate_profile_mappings(parsed, active_group_ids=active_group_ids)
    row = Profile(
        owner_id=user_id,
        name=parsed.name,
        description=parsed.description,
        semantic_class=parsed.semantic_class,
        head_revision=1,
        archived=False,
    )
    _set_visibility(db, row, user_id, visibility, project_id)
    db.add(row)
    db.flush()
    revision = Revision(
        profile_id=row.id,
        revision=1,
        fingerprint=parsed.fingerprint(),
        contract=parsed.model_dump(mode="json"),
        creator_id=user_id,
    )
    db.add(revision)
    db.flush()
    persist_capability_references(db, revision, capabilities)
    return row, revision


def revise_profile(
    db: Session,
    profile_id: UUID,
    user_id: int,
    contract,
    *,
    expected_revision: int,
    active_group_ids=(),
) -> tuple[Profile, Revision, list[dict]]:
    parsed = normalize_profile_contract(contract)
    capabilities = validate_profile_mappings(parsed, active_group_ids=active_group_ids)
    row = get_profile(db, profile_id, user_id, for_update=True)
    if row.owner_id != user_id:
        raise ProfileNotFoundError(
            "Profile not found; clone a shared profile to edit it"
        )
    if row.head_revision != expected_revision:
        raise ProfileConflictError(
            "Profile changed since it was opened; compare or reload before saving"
        )
    previous = get_profile_revision(db, profile_id, row.head_revision, user_id)
    findings = profile_compatibility(previous.contract, parsed)
    if previous.fingerprint == parsed.fingerprint():
        return row, previous, findings
    revision = Revision(
        profile_id=row.id,
        revision=row.head_revision + 1,
        fingerprint=parsed.fingerprint(),
        contract=parsed.model_dump(mode="json"),
        creator_id=user_id,
    )
    db.add(revision)
    db.flush()
    persist_capability_references(db, revision, capabilities)
    row.head_revision = revision.revision
    row.name, row.description, row.semantic_class = (
        parsed.name,
        parsed.description,
        parsed.semantic_class,
    )
    db.flush()
    return row, revision, findings


def clone_profile(
    db: Session,
    profile_id: UUID,
    revision: int,
    user_id: int,
    *,
    name: str,
    active_group_ids=(),
) -> tuple[Profile, Revision]:
    source = get_profile_revision(
        db, profile_id, revision, user_id, include_archived=True
    )
    contract = {**source.contract, "name": name}
    return create_profile(db, user_id, contract, active_group_ids=active_group_ids)


def archive_profile(
    db: Session, profile_id: UUID, user_id: int, *, expected_revision: int
) -> Profile:
    row = get_profile(db, profile_id, user_id, include_archived=True, for_update=True)
    if row.owner_id != user_id:
        raise ProfileNotFoundError("Profile not found")
    if row.head_revision != expected_revision:
        raise ProfileConflictError("Profile changed since it was opened")
    row.archived = True
    db.flush()
    return row
