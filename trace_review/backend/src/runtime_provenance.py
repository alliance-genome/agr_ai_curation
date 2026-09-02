"""Immutable runtime identity for TraceReview deployment verification."""

import hashlib
import os
from importlib.metadata import version
from pathlib import Path
from typing import Optional


TRACE_EXTRACTOR_PATH = Path(__file__).resolve().parent / "services" / "trace_extractor.py"


def _build_value(name: str) -> Optional[str]:
    value = os.getenv(name, "").strip()
    return value or None


def get_runtime_provenance(
    trace_extractor_path: Path = TRACE_EXTRACTOR_PATH,
) -> dict[str, Optional[str]]:
    """Return non-secret build and dependency identity for this runtime."""
    return {
        "build_ref": _build_value("TRACE_REVIEW_BUILD_REF"),
        "git_sha": _build_value("TRACE_REVIEW_GIT_SHA"),
        "langfuse_sdk_version": version("langfuse"),
        "trace_extractor_sha256": hashlib.sha256(
            trace_extractor_path.read_bytes()
        ).hexdigest(),
    }
