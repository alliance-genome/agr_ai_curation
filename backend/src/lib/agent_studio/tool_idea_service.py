"""Tool idea request service for Agent Workshop."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.models.sql.agent import ProjectMember
from src.models.sql.tool_idea_request import ToolIdeaRequest


VALID_TOOL_IDEA_STATUSES = {
    "submitted",
    "reviewed",
    "in_progress",
    "completed",
    "declined",
}


def get_primary_project_id_for_user(db: Session, user_id: int) -> UUID:
    """Resolve a user's primary project membership (v1 default project)."""
    row = (
        db.query(ProjectMember.project_id)
        .filter(ProjectMember.user_id == user_id)
        .order_by(ProjectMember.joined_at.asc())
        .first()
    )
    if not row:
        raise ValueError("User is not assigned to any project")
    return row[0]


def _normalize_title(title: str) -> str:
    normalized = str(title or "").strip()
    if not normalized:
        raise ValueError("title is required")
    if len(normalized) > 255:
        raise ValueError("title must be 255 characters or fewer")
    return normalized


def _normalize_description(description: str) -> str:
    normalized = str(description or "").strip()
    if not normalized:
        raise ValueError("description is required")
    return normalized


def _normalize_opus_conversation(opus_conversation: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if opus_conversation is None:
        return []
    if not isinstance(opus_conversation, list):
        raise ValueError("opus_conversation must be an array")

    normalized: List[Dict[str, Any]] = []
    for entry in opus_conversation:
        if not isinstance(entry, dict):
            raise ValueError("opus_conversation entries must be objects")
        role = str(entry.get("role", "")).strip()
        content = str(entry.get("content", "")).strip()
        if not role or not content:
            continue
        normalized.append(
            {
                "role": role,
                "content": content,
                "timestamp": str(entry.get("timestamp", "")).strip() or None,
            }
        )
    return normalized


def create_tool_idea_request(
    db: Session,
    user_id: int,
    title: str,
    description: str,
    project_id: Optional[UUID] = None,
    opus_conversation: Optional[List[Dict[str, Any]]] = None,
) -> ToolIdeaRequest:
    """Create a new tool idea request row."""
    record = ToolIdeaRequest(
        user_id=user_id,
        project_id=project_id,
        title=_normalize_title(title),
        description=_normalize_description(description),
        opus_conversation=_normalize_opus_conversation(opus_conversation),
        status="submitted",
    )
    db.add(record)
    db.flush()
    return record


def list_tool_idea_requests_for_user(
    db: Session,
    user_id: int,
) -> List[ToolIdeaRequest]:
    """List tool idea requests submitted by a user (most recent first)."""
    return (
        db.query(ToolIdeaRequest)
        .filter(ToolIdeaRequest.user_id == user_id)
        .order_by(ToolIdeaRequest.created_at.desc(), ToolIdeaRequest.updated_at.desc())
        .all()
    )


def tool_idea_request_to_dict(record: ToolIdeaRequest) -> Dict[str, Any]:
    """Serialize a tool idea request for API responses."""
    created_at = record.created_at
    updated_at = record.updated_at
    if isinstance(created_at, datetime) and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    return {
        "id": str(record.id),
        "user_id": record.user_id,
        "project_id": str(record.project_id) if record.project_id else None,
        "title": record.title,
        "description": record.description,
        "opus_conversation": list(record.opus_conversation or []),
        "status": record.status,
        "developer_notes": record.developer_notes,
        "resulting_tool_key": record.resulting_tool_key,
        "created_at": created_at,
        "updated_at": updated_at,
    }
