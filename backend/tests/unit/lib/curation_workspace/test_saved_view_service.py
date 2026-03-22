"""Unit tests for curation workspace saved-view helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_workspace import saved_view_service as module
from src.lib.curation_workspace.models import CurationSavedView as SavedViewModel
from src.models.sql.database import Base
from src.models.sql.user import User
from src.schemas.curation_workspace import (
    CurationSavedViewCreateRequest,
    CurationSessionSortField,
    CurationSortDirection,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    User.__table__,
    SavedViewModel.__table__,
]


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    restored_defaults = []
    restored_indexes = []
    for table in TEST_TABLES:
        restored_indexes.append((table, set(table.indexes)))
        table.indexes.clear()
        for column in table.columns:
            restored_defaults.append((column, column.server_default))
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = session_local()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=TEST_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for column, server_default in restored_defaults:
            column.server_default = server_default


def _now() -> datetime:
    return datetime(2026, 3, 22, 13, 5, tzinfo=timezone.utc)


def _create_user(db_session, auth_sub: str, *, name: str) -> User:
    user = User(
        auth_sub=auth_sub,
        email=f"{auth_sub}@example.org",
        display_name=name,
        is_active=True,
        created_at=_now(),
        last_login=_now(),
    )
    db_session.add(user)
    db_session.commit()
    return user


def _create_saved_view(
    db_session,
    *,
    owner_id: str,
    name: str,
    is_default: bool = False,
    created_at: datetime | None = None,
) -> SavedViewModel:
    saved_view = SavedViewModel(
        id=uuid4(),
        name=name,
        description=f"{name} description",
        filters={
            "statuses": ["new"],
            "adapter_keys": ["gene"],
            "profile_keys": [],
            "domain_keys": [],
            "curator_ids": [],
            "tags": [],
            "flow_run_id": None,
            "origin_session_id": "chat-session-7",
            "document_id": None,
            "search": None,
            "prepared_between": None,
            "last_worked_between": None,
            "saved_view_id": "stale-value",
        },
        sort_by=CurationSessionSortField.PREPARED_AT,
        sort_direction=CurationSortDirection.DESC,
        is_default=is_default,
        created_by_id=owner_id,
        created_at=created_at or _now(),
        updated_at=created_at or _now(),
    )
    db_session.add(saved_view)
    db_session.commit()
    return saved_view


def test_create_saved_view_persists_view_and_sanitizes_queue_state(db_session):
    _create_user(db_session, "user-1", name="Curator One")

    response = module.create_saved_view(
        db_session,
        CurationSavedViewCreateRequest(
            name="  My pending sessions  ",
            description="  Sessions assigned to me  ",
            filters={
                "statuses": ["in_progress"],
                "adapter_keys": ["gene"],
                "profile_keys": ["alpha"],
                "domain_keys": [],
                "curator_ids": ["user-1"],
                "tags": [],
                "flow_run_id": None,
                "origin_session_id": "chat-session-1",
                "document_id": None,
                "search": "pending",
                "prepared_between": None,
                "last_worked_between": None,
                "saved_view_id": "existing-view",
            },
            sort_by="prepared_at",
            sort_direction="desc",
            is_default=False,
        ),
        current_user_id="user-1",
    )

    saved_view = db_session.scalar(select(SavedViewModel))
    assert saved_view is not None
    assert saved_view.name == "My pending sessions"
    assert saved_view.description == "Sessions assigned to me"
    assert saved_view.created_by_id == "user-1"
    assert saved_view.filters["origin_session_id"] is None
    assert saved_view.filters["saved_view_id"] is None

    assert response.view.name == "My pending sessions"
    assert response.view.filters.origin_session_id is None
    assert response.view.filters.saved_view_id is None
    assert response.view.created_by is not None
    assert response.view.created_by.display_name == "Curator One"


def test_create_saved_view_sets_only_one_default_per_user(db_session):
    _create_user(db_session, "user-1", name="Curator One")
    existing_default = _create_saved_view(
        db_session,
        owner_id="user-1",
        name="Existing default",
        is_default=True,
    )

    response = module.create_saved_view(
        db_session,
        CurationSavedViewCreateRequest(
            name="Fresh default",
            filters={},
            sort_by="adapter",
            sort_direction="asc",
            is_default=True,
        ),
        current_user_id="user-1",
    )

    db_session.refresh(existing_default)
    assert existing_default.is_default is False
    assert response.view.is_default is True

    current_user_defaults = db_session.scalars(
        select(SavedViewModel).where(
            SavedViewModel.created_by_id == "user-1",
            SavedViewModel.is_default.is_(True),
        )
    ).all()
    assert [saved_view.name for saved_view in current_user_defaults] == ["Fresh default"]


def test_list_saved_views_returns_current_user_views_default_first(db_session):
    _create_user(db_session, "user-1", name="Curator One")
    _create_user(db_session, "user-2", name="Curator Two")
    _create_saved_view(
        db_session,
        owner_id="user-1",
        name="zeta queue",
        is_default=False,
        created_at=datetime(2026, 3, 22, 13, 0, tzinfo=timezone.utc),
    )
    _create_saved_view(
        db_session,
        owner_id="user-1",
        name="alpha queue",
        is_default=True,
        created_at=datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc),
    )
    _create_saved_view(
        db_session,
        owner_id="user-2",
        name="other user view",
        is_default=True,
    )

    response = module.list_saved_views(db_session, current_user_id="user-1")

    assert [view.name for view in response.views] == ["alpha queue", "zeta queue"]
    assert response.views[0].is_default is True
    assert all(view.created_by and view.created_by.actor_id == "user-1" for view in response.views)
    assert all(view.filters.origin_session_id is None for view in response.views)
    assert all(view.filters.saved_view_id is None for view in response.views)


def test_delete_saved_view_requires_ownership(db_session):
    _create_user(db_session, "user-1", name="Curator One")
    _create_user(db_session, "user-2", name="Curator Two")
    owned_view = _create_saved_view(db_session, owner_id="user-1", name="Owned view")
    other_view = _create_saved_view(db_session, owner_id="user-2", name="Other view")

    response = module.delete_saved_view(
        db_session,
        owned_view.id,
        current_user_id="user-1",
    )

    assert response.deleted_view_id == str(owned_view.id)
    assert db_session.get(SavedViewModel, owned_view.id) is None

    with pytest.raises(module.HTTPException) as exc:
        module.delete_saved_view(
            db_session,
            other_view.id,
            current_user_id="user-1",
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Saved view not found"
