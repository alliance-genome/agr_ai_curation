"""Reference JSON export adapter for curator-approved draft bundles."""

from __future__ import annotations

import json

from src.lib.curation_workspace.export_adapters.base import (
    DeterministicExportAdapter,
    ExportBundleArtifact,
)
from src.schemas.curation_workspace import (
    CurationExportPayloadContext,
    SubmissionMode,
    SubmissionTargetKey,
)


DEFAULT_JSON_BUNDLE_TARGET_KEY = "review_export_bundle"
JSON_BUNDLE_SCHEMA_VERSION = 1


class JsonBundleExportAdapter(DeterministicExportAdapter):
    """Serialize approved candidates, drafts, and evidence anchors into one JSON bundle."""

    def __init__(
        self,
        *,
        adapter_key: str,
        target_key: SubmissionTargetKey = DEFAULT_JSON_BUNDLE_TARGET_KEY,
    ) -> None:
        super().__init__(
            adapter_key=adapter_key,
            supported_target_keys=(target_key,),
        )

    def build_export_bundle(
        self,
        *,
        mode: SubmissionMode,
        target_key: SubmissionTargetKey,
        export_context: CurationExportPayloadContext,
    ) -> ExportBundleArtifact:
        """Build a canonical JSON payload suitable for preview or export handoff."""

        payload_json = _canonicalize_json_payload(
            {
                "schema_version": JSON_BUNDLE_SCHEMA_VERSION,
                "bundle_type": "curation_export_bundle",
                "adapter_key": self.adapter_key,
                "mode": mode.value,
                "target_key": target_key,
                **export_context.model_dump(mode="json"),
            }
        )
        payload_text = json.dumps(payload_json, indent=2, sort_keys=True)

        return ExportBundleArtifact(
            payload_json=payload_json,
            payload_text=payload_text,
            content_type="application/json",
            filename=f"{self.adapter_key}-{export_context.session_id}-export-bundle.json",
        )


def _canonicalize_json_payload(
    payload: dict[str, object],
) -> dict[str, object]:
    return json.loads(json.dumps(payload, sort_keys=True))
