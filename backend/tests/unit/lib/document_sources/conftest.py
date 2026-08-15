"""Import support for package-owned document-source provider tests."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PACKAGE_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PACKAGE_SRC))
