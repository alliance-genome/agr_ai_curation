"""Canonical application-observable receipt for PDF processing jobs."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import time
from typing import Any, Iterator, Mapping

from src.lib.observability.sentry import (
    pdf_processing_stage_span,
    set_pdf_processing_span_outcome,
)


PDF_PROCESSING_RECEIPT_KEY = "pdf_processing_receipt"
PDF_PROCESSING_RECEIPT_VERSION = 2
PDF_PROCESSING_STAGES = (
    "external_request",
    "hierarchy",
    "chunking",
    "figure_locator",
    "embedding_storage",
    "total",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class PDFProcessingReceipt:
    """Build one bounded receipt from timestamps observed by this application."""

    def __init__(self, *, document_id: str) -> None:
        self.document_id = document_id
        self._started_at = _utc_now()
        self._started_monotonic = time.monotonic()
        self._receipt: dict[str, Any] = {
            "schema_version": PDF_PROCESSING_RECEIPT_VERSION,
            "started_at": _isoformat(self._started_at),
            "outcome": "running",
            "selection": {},
            "stages": {
                stage: {"status": "not_started"}
                for stage in PDF_PROCESSING_STAGES
            },
        }

    def set_selection(self, **selection: Any) -> None:
        """Record only known method, merge, variant, and cache choices."""
        selected = self._receipt["selection"]
        for key, value in selection.items():
            if value is not None:
                selected[key] = deepcopy(value)

    def record_stage(
        self,
        stage: str,
        *,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: float,
    ) -> None:
        if stage not in PDF_PROCESSING_STAGES:
            raise ValueError(f"Unknown PDF processing receipt stage: {stage}")
        self._receipt["stages"][stage] = {
            "status": status,
            "started_at": _isoformat(started_at),
            "completed_at": _isoformat(completed_at),
            "duration_ms": round(max(0.0, duration_ms), 1),
        }

    def record_external_observation(self, observation: Mapping[str, Any]) -> None:
        started_at = observation.get("started_at")
        completed_at = observation.get("completed_at")
        if not isinstance(started_at, datetime) or not isinstance(completed_at, datetime):
            raise ValueError("External request observation requires datetime boundaries")
        self.record_stage(
            "external_request",
            status=str(observation["status"]),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=float(observation["duration_ms"]),
        )
        external_stage = self._receipt["stages"]["external_request"]
        for key in (
            "process_id",
            "failure_category",
            "failure_boundary",
            "provider_status",
            "provider_error_code",
        ):
            value = observation.get(key)
            if isinstance(value, str) and value:
                external_stage[key] = value
        for key in (
            "submit_attempt_count",
            "poll_attempt_count",
            "timeout_seconds",
            "http_status",
        ):
            value = observation.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                external_stage[key] = value
        self.set_selection(
            extraction_methods=observation.get("extraction_methods"),
            merge_enabled=observation.get("merge_enabled"),
            download_variant=observation.get("download_variant"),
            cache_hit=observation.get("cache_hit"),
        )

    @contextmanager
    def observe_stage(self, stage: str) -> Iterator[None]:
        """Measure a local stage and emit the matching redacted Sentry span."""
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        with pdf_processing_stage_span(
            stage=stage,
            document_id=self.document_id,
            selection=self._receipt["selection"],
        ) as span:
            try:
                yield
            except BaseException:
                completed_at = _utc_now()
                duration_ms = (time.monotonic() - started_monotonic) * 1000
                self.record_stage(
                    stage,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                )
                set_pdf_processing_span_outcome(
                    span,
                    outcome="failed",
                    duration_ms=duration_ms,
                )
                raise
            else:
                completed_at = _utc_now()
                duration_ms = (time.monotonic() - started_monotonic) * 1000
                self.record_stage(
                    stage,
                    status="completed",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                )
                set_pdf_processing_span_outcome(
                    span,
                    outcome="completed",
                    duration_ms=duration_ms,
                )

    def finalize(self, outcome: str) -> dict[str, Any]:
        completed_at = _utc_now()
        duration_ms = (time.monotonic() - self._started_monotonic) * 1000
        self.record_stage(
            "total",
            status=outcome,
            started_at=self._started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        self._receipt["completed_at"] = _isoformat(completed_at)
        self._receipt["outcome"] = outcome
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._receipt)


def minimal_terminal_receipt(
    *,
    started_at: datetime,
    completed_at: datetime,
    outcome: str,
) -> dict[str, Any]:
    """Build a receipt for terminal paths that ended before pipeline instrumentation."""
    duration_ms = max(0.0, (completed_at - started_at).total_seconds() * 1000)
    receipt: dict[str, Any] = {
        "schema_version": PDF_PROCESSING_RECEIPT_VERSION,
        "started_at": _isoformat(started_at),
        "completed_at": _isoformat(completed_at),
        "outcome": outcome,
        "selection": {},
        "stages": {
            stage: {"status": "not_started"}
            for stage in PDF_PROCESSING_STAGES
        },
    }
    receipt["stages"]["total"] = {
        "status": outcome,
        "started_at": _isoformat(started_at),
        "completed_at": _isoformat(completed_at),
        "duration_ms": round(duration_ms, 1),
    }
    return receipt
