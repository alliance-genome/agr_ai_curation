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


def owner_storage_entry(path: Path, storage_root: Path, owner_subject: str) -> Path:
    """The path to delete: its folder resolved and inside the owner's folder, its own name kept.

    For deleting, the last component is not followed: a stored path that is a link names
    the link, so deleting it never touches the link's target (review P2-1). Its folder is
    resolved (``..`` and folder links followed) and must lie inside the owner's folder.
    """

    if path.name in {"", ".", ".."}:
        raise StoredPathOutsideOwnerStorage("A stored path must name a file")
    return owner_storage_path(path.parent, storage_root, owner_subject) / path.name
