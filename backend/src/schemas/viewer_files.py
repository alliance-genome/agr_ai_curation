"""Viewer contracts for a paper's files: one entry per file and a text file's display blocks."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_FILE_URL_PATTERN = r"^/api/pdf-viewer/documents/[0-9a-f-]+/files/[0-9a-f-]+/{route}$"


class ViewerFileRef(BaseModel):
    """One file of a paper as the viewer shows it, in tab order."""

    model_config = ConfigDict(extra="forbid")

    file_id: UUID = Field(description="The file's id (pdf_document_files.id)")
    role: Literal["main", "supplement"]
    display_name: str
    display_mode: Literal["local_pdf", "provider_pdf_proxy", "text"]
    status: Literal["pending", "ready", "failed"]
    tab_order: int = Field(ge=0)
    page_count: int | None = Field(
        ge=1,
        description="Pages of a ready PDF file; none for a text file or a file that is not ready",
    )
    content_url: str | None = Field(
        pattern=_FILE_URL_PATTERN.format(route="content"),
        description="The file's PDF bytes; only for a ready file shown as a PDF",
    )
    text_url: str | None = Field(
        pattern=_FILE_URL_PATTERN.format(route="text"),
        description="The file's display blocks; only for a ready text file",
    )


class DisplayTableRow(BaseModel):
    """One table row: its exact stored line and its cells."""

    model_config = ConfigDict(extra="forbid")

    line: str = Field(description="The row exactly as stored in the file's pieces")
    cells: list[str] = Field(description="The row's cells, escapes undone, empty cells kept")
    is_header: bool


class DisplayBlock(BaseModel):
    """One block of a text file, holding its text exactly as it was cut into pieces."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["heading", "paragraph", "list_item", "code", "table"]
    text: str = Field(
        description=(
            "The element's text as stored before cutting; for a table, its lines "
            "(separator line included) joined by line breaks"
        )
    )
    level: int | None = Field(ge=1, description="A heading's depth in its section path")
    section_path: list[str] = Field(description="The headings this block sits under")
    rows: list[DisplayTableRow] | None = Field(description="A table's rows; none otherwise")
