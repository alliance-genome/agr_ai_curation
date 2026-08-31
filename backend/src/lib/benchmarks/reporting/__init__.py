"""Safe benchmark report generation and private artifact persistence."""

from .artifacts import build_artifact_bundle, canonical_json_bytes
from .models import (
    ArtifactBundle,
    ArtifactManifest,
    BenchmarkReport,
    BenchmarkScoreRecord,
    ReportProvenance,
    StoredArtifactReceipt,
)
from .report import build_benchmark_report
from .storage import (
    ArtifactStorageError,
    DuplicateLogicalRunError,
    S3ArtifactStore,
    create_configured_s3_artifact_store,
)

__all__ = [
    "ArtifactBundle",
    "ArtifactManifest",
    "ArtifactStorageError",
    "BenchmarkReport",
    "BenchmarkScoreRecord",
    "DuplicateLogicalRunError",
    "ReportProvenance",
    "S3ArtifactStore",
    "StoredArtifactReceipt",
    "build_artifact_bundle",
    "build_benchmark_report",
    "canonical_json_bytes",
    "create_configured_s3_artifact_store",
]
