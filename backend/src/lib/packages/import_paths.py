"""Import-path helpers for package-owned Python code."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def extend_sys_path_for_package(package: Any, *extra_paths: Path) -> None:
    """Make one loaded package importable in the current Python process."""
    python_package_root = (
        package.package_path / package.manifest.python_package_root
    ).expanduser().resolve(strict=False)
    for candidate in (
        python_package_root.parent,
        python_package_root,
        package.package_path,
        *extra_paths,
    ):
        candidate_text = str(candidate)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)
