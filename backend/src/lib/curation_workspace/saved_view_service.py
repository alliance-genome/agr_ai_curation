"""Persistence helpers for curation inventory saved views."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import CurationSavedView as SavedViewModel
from src.models.sql.user import User
from src.schemas.curation_workspace import (
    CurationActorRef,
    CurationSavedView,
    CurationSavedViewCreateRequest,
    CurationSavedViewCreateResponse,
    CurationSavedViewDeleteResponse,
    CurationSavedViewListResponse,
    CurationSessionFilters,
)


def _load_users(db: Session, actor_ids: Iterable[str | None]) -> dict[str, User]:
    user_ids = sorted({actor_id for actor_id in actor_ids if actor_id})
    if not user_ids:
        return {}

    users = db.scalars(select(User).where(User.auth_sub.in_(user_ids))).all()
    return {user.auth_sub: user for user in users}


def _actor_ref(user_map: dict[str, User], actor_id: str | None) -> CurationActorRef | None:
    if not actor_id:
        return None

    user = user_map.get(actor_id)
    if user is None:
        return CurationActorRef(actor_id=actor_id)

    return CurationActorRef(
        actor_id=user.auth_sub,
        display_name=user.display_name or user.email or user.auth_sub,
        email=user.email,
    )


def _saved_view_filters(filters: dict[str, Any] | None) -> CurationSessionFilters:
    payload = dict(filters or {})
    payload["origin_session_id"] = None
    payload["saved_view_id"] = None
    return CurationSessionFilters.model_validate(payload)


def _saved_view_payload(
    saved_view: SavedViewModel,
    user_map: dict[str, User],
) -> CurationSavedView:
    return CurationSavedView(
        view_id=str(saved_view.id),
        name=saved_view.name,
        description=saved_view.description,
        filters=_saved_view_filters(saved_view.filters),
        sort_by=saved_view.sort_by,
        sort_direction=saved_view.sort_direction,
        is_default=saved_view.is_default,
        created_by=_actor_ref(user_map, saved_view.created_by_id),
        created_at=saved_view.created_at,
        updated_at=saved_view.updated_at,
    )


def list_saved_views(
    db: Session,
    *,
    current_user_id: str,
) -> CurationSavedViewListResponse:
    saved_views = db.scalars(
        select(SavedViewModel)
        .where(SavedViewModel.created_by_id == current_user_id)
        .order_by(
            SavedViewModel.is_default.desc(),
            func.lower(SavedViewModel.name),
            SavedViewModel.created_at,
        )
    ).all()
    user_map = _load_users(db, [saved_view.created_by_id for saved_view in saved_views])

    return CurationSavedViewListResponse(
        views=[_saved_view_payload(saved_view, user_map) for saved_view in saved_views]
    )


def create_saved_view(
    db: Session,
    request: CurationSavedViewCreateRequest,
    *,
    current_user_id: str,
) -> CurationSavedViewCreateResponse:
    normalized_name = request.name.strip()
    if not normalized_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Saved view name is required",
        )

    normalized_description = request.description.strip() if request.description else None
    normalized_filters = request.filters.model_copy(
        update={
            "origin_session_id": None,
            "saved_view_id": None,
        }
    )
    now = datetime.now(timezone.utc)

    if request.is_default:
        db.execute(
            update(SavedViewModel)
            .where(
                SavedViewModel.created_by_id == current_user_id,
                SavedViewModel.is_default.is_(True),
            )
            .values(is_default=False, updated_at=now)
        )

    saved_view = SavedViewModel(
        name=normalized_name,
        description=normalized_description or None,
        filters=normalized_filters.model_dump(mode="json"),
        sort_by=request.sort_by,
        sort_direction=request.sort_direction,
        is_default=request.is_default,
        created_by_id=current_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(saved_view)
    db.commit()
    db.refresh(saved_view)

    user_map = _load_users(db, [saved_view.created_by_id])
    return CurationSavedViewCreateResponse(
        view=_saved_view_payload(saved_view, user_map)
    )


def delete_saved_view(
    db: Session,
    view_id: UUID,
    *,
    current_user_id: str,
) -> CurationSavedViewDeleteResponse:
    saved_view = db.scalar(
        select(SavedViewModel).where(
            SavedViewModel.id == view_id,
            SavedViewModel.created_by_id == current_user_id,
        )
    )
    if saved_view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved view not found",
        )

    deleted_view_id = str(saved_view.id)
    db.delete(saved_view)
    db.commit()
    return CurationSavedViewDeleteResponse(deleted_view_id=deleted_view_id)


__all__ = [
    "create_saved_view",
    "delete_saved_view",
    "list_saved_views",
]
