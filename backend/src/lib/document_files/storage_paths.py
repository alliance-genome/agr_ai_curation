"""Stored paths stay inside their owner's storage folder (KANBAN-1976 / ALL-1460, P10b).

One rule for every place that opens or deletes a curator's stored file: a path,
resolved (``..`` and links followed), must lie under ``<storage root>/<owner subject>``.
``api.documents.validate_user_file_path`` answers 403 from it for HTTP routes;
``remove_stored_files`` (``service.py``) uses it before deleting anything.
"""

from __future__ import annotations

from pathlib import Path


class StoredPathOutsideOwnerStorage(PermissionError):
    """A stored path resolves outside its owner's storage folder."""


def owner_storage_path(path: Path, storage_root: Path, owner_subject: str) -> Path:
    """The resolved path, or ``StoredPathOutsideOwnerStorage`` when it leaves the owner's folder."""

    if not owner_subject or "/" in owner_subject or owner_subject in {".", ".."}:
        raise StoredPathOutsideOwnerStorage("The owner's subject cannot name a storage folder")
    resolved = path.resolve()
    owner_root = (storage_root.resolve() / owner_subject).resolve()
    try:
        resolved.relative_to(owner_root)
    except ValueError:
        raise StoredPathOutsideOwnerStorage("A stored path resolves outside its owner's storage") from None
    return resolved
