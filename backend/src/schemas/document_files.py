"""Request shapes for adding provider files to a paper (KANBAN-1976 / ALL-1460, P7c1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChosenFileRequest(BaseModel):
    """One file the curator chose, as the paper's file listing showed it."""

    model_config = ConfigDict(extra="forbid")

    # The original's provider file id (the listing's ``provider_file_id``).
    provider_file_id: str = Field(min_length=1, max_length=64)
    # The original's MD5 and the converted text's MD5, as listed; a change since is refused per file.
    md5: str | None = Field(default=None, max_length=64)
    converted_md5: str = Field(min_length=1, max_length=64)


class AddFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[ChosenFileRequest] = Field(min_length=1)
