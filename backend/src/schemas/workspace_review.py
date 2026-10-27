"""Curator commands: authenticated actor and budget grants are server-owned."""

from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from src.lib.validation_review.decisions import ReviewEntry


class WorkspaceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    candidate_ids: list[UUID] = Field(default_factory=list)
    guidance: str | None = None


class WorkspaceReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    episode_id: UUID
    entries: list[ReviewEntry]


class WorkspaceRecordRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    target_id: UUID
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1)
