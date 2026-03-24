"""Deterministic export adapter contracts for curation workspace bundles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.schemas.curation_workspace import (
    CurationExportPayloadContext,
    SubmissionMode,
    SubmissionPayloadContract,
    SubmissionTargetKey,
)


@dataclass(frozen=True)
class ExportBundleArtifact:
    """Adapter-owned export bundle body before submission envelope fields are applied."""

    payload_json: dict[str, Any] | list[Any] | None = None
    payload_text: str | None = None
    content_type: str | None = None
    filename: str | None = None
    warnings: tuple[str, ...] = ()


class DeterministicExportAdapter(ABC):
    """Base class for deterministic export adapters that satisfy the submission contract."""

    adapter_key: str
    supported_submission_modes: Sequence[SubmissionMode]
    supported_target_keys: Sequence[SubmissionTargetKey]

    def __init__(
        self,
        *,
        adapter_key: str,
        supported_target_keys: Sequence[SubmissionTargetKey],
        supported_submission_modes: Sequence[SubmissionMode] = (
            SubmissionMode.PREVIEW,
            SubmissionMode.EXPORT,
            SubmissionMode.DIRECT_SUBMIT,
        ),
    ) -> None:
        self.adapter_key = adapter_key
        self.supported_target_keys = tuple(supported_target_keys)
        self.supported_submission_modes = tuple(supported_submission_modes)

    def build_submission_payload(
        self,
        *,
        mode: SubmissionMode,
        target_key: SubmissionTargetKey,
        payload_context: Mapping[str, Any],
    ) -> SubmissionPayloadContract:
        """Build the shared submission envelope around an adapter-owned export bundle."""

        self._validate_submission_request(mode=mode, target_key=target_key)
        export_context = CurationExportPayloadContext.model_validate(payload_context)
        bundle = self.build_export_bundle(
            mode=mode,
            target_key=target_key,
            export_context=export_context,
        )

        return SubmissionPayloadContract(
            mode=mode,
            target_key=target_key,
            adapter_key=self.adapter_key,
            candidate_ids=list(export_context.candidate_ids),
            payload_json=bundle.payload_json,
            payload_text=bundle.payload_text,
            content_type=bundle.content_type,
            filename=bundle.filename,
            warnings=_dedupe_preserve_order(
                [*export_context.warnings, *bundle.warnings]
            ),
        )

    def _validate_submission_request(
        self,
        *,
        mode: SubmissionMode,
        target_key: SubmissionTargetKey,
    ) -> None:
        """Reject unsupported modes or target keys before building the payload."""

        if mode not in self.supported_submission_modes:
            raise ValueError(
                f"Export adapter '{self.adapter_key}' does not support mode '{mode.value}'"
            )
        if self.supported_target_keys and target_key not in self.supported_target_keys:
            supported_targets = ", ".join(self.supported_target_keys)
            raise ValueError(
                f"Export adapter '{self.adapter_key}' does not support target "
                f"'{target_key}'. Supported targets: {supported_targets}"
            )

    @abstractmethod
    def build_export_bundle(
        self,
        *,
        mode: SubmissionMode,
        target_key: SubmissionTargetKey,
        export_context: CurationExportPayloadContext,
    ) -> ExportBundleArtifact:
        """Build the adapter-owned export bundle content for one submission request."""


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
