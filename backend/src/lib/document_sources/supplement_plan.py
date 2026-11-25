"""What an import adds after its main paper: the chosen supplements and the login (interface I6).

Plain data, kept apart from the supplement stage so the execution requests can
carry it without importing the stage (KANBAN-1976 / ALL-1460, P7b).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.lib.document_sources.access import DocumentSourceRequestContext


@dataclass(frozen=True, slots=True)
class ChosenSupplement:
    """One supplement the curator ticked, as the listing showed it."""

    provider_file_id: str
    md5: str | None
    # ``None`` for a file the provider had not converted when it was listed.
    converted_md5: str | None
    # Human-readable display name from the listing; ``None`` when unnamed (never the raw id).
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class SupplementPlan:
    """The supplements an import adds after its main paper, and the login to add them with."""

    chosen: tuple[ChosenSupplement, ...]
    request_context: DocumentSourceRequestContext
    # The curator's login expiry (epoch seconds), from the request's validated
    # claims; ``None`` for the development machine reader, which has no curator login.
    login_expires_at: float | None
    owner_user_id: int
