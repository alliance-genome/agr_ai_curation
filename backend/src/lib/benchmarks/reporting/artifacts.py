"""Canonical serialization with allowlisted schemas and secret rejection."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any, Protocol, TypeVar

from .models import (
    ArtifactBundle,
    ArtifactDescriptor,
    ArtifactManifest,
    BenchmarkReport,
)


class _RouteLike(Protocol):
    provider: str
    model: str


RouteT = TypeVar("RouteT", bound=_RouteLike)

_CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+"
)


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _reject_secrets(value: Any, patterns: tuple[re.Pattern[str], ...]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _reject_secrets(item, patterns)
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item, patterns)
    elif isinstance(value, str):
        if _CREDENTIAL_VALUE.search(value) or any(
            pattern.search(value) for pattern in patterns
        ):
            raise ValueError("artifact contains a configured or credential-like secret")


def canonical_json_bytes(
    artifact: BenchmarkReport | ArtifactManifest,
    *,
    secret_patterns: Iterable[str] = (),
) -> bytes:
    """Serialize only canonical artifact models and reject sensitive values."""

    patterns = tuple(re.compile(pattern) for pattern in secret_patterns if pattern)
    payload = artifact.model_dump(mode="json")
    _reject_secrets(payload, patterns)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _unique_routes(routes: Iterable[RouteT]) -> list[RouteT]:
    values = {(route.provider, route.model): route for route in routes}
    return [values[key] for key in sorted(values)]


def build_artifact_bundle(
    report: BenchmarkReport,
    *,
    secret_patterns: Iterable[str] | None = None,
) -> ArtifactBundle:
    """Create a deterministic report and immutable provenance manifest."""

    if secret_patterns is None:
        from src.lib.openai_agents.config import (
            get_benchmark_artifact_secret_patterns,
        )

        secret_patterns = get_benchmark_artifact_secret_patterns()
    patterns = tuple(secret_patterns)
    report_bytes = canonical_json_bytes(report, secret_patterns=patterns)
    scores = [score for case in report.cases for score in case.scores]
    manifest = ArtifactManifest(
        provenance=report.provenance,
        fixture_digests=sorted({case.fixture_digest for case in report.cases}),
        scorer_versions=sorted(
            {
                f"{score.deterministic.scorer_id}:"
                f"{score.deterministic.scoring_version}"
                for score in scores
            }
        ),
        adjudicator_versions=sorted(
            {
                f"rubric:{score.adjudication.rubric_version}:"
                f"prompt:{score.adjudication.prompt_id}:"
                f"model:{score.adjudication.model}"
                for score in scores
                if score.adjudication is not None
            }
        ),
        requested_routes=_unique_routes(case.requested_route for case in report.cases),
        actual_routes=_unique_routes(
            case.actual_route for case in report.cases if case.actual_route is not None
        ),
        artifacts=[
            ArtifactDescriptor(
                name="report.json",
                media_type="application/json",
                size_bytes=len(report_bytes),
                sha256=_digest(report_bytes),
            )
        ],
        storage_receipts=[],
    )
    manifest_bytes = canonical_json_bytes(manifest, secret_patterns=patterns)
    return ArtifactBundle(
        report=report,
        report_bytes=report_bytes,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )
