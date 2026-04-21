"""Helpers for normalizing permissions on writable storage directories."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

WRITABLE_STORAGE_MODE = 0o777


def ensure_writable_directory(path: Path, *, mode: int = WRITABLE_STORAGE_MODE) -> Path:
    """Create a storage directory and normalize its mode for mounted runtime state.

    Production uploads write into host-mounted directories whose ownership can drift
    between deploys. We explicitly normalize the mode so the app can keep writing
    even when the underlying host UID/GID changes.
    """
    path.mkdir(parents=True, exist_ok=True)

    try:
        current_mode = path.stat().st_mode & 0o777
    except OSError as exc:
        logger.warning("Failed to inspect writable storage directory %s: %s", path, exc)
        return path

    if current_mode != mode:
        try:
            path.chmod(mode)
        except OSError as exc:
            logger.warning(
                "Failed to normalize writable storage directory %s to %s: %s",
                path,
                oct(mode),
                exc,
            )

    return path
