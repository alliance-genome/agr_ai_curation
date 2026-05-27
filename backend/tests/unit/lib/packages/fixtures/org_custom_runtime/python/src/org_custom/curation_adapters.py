from __future__ import annotations

import json
from pathlib import Path

from src.lib.curation_workspace.export_adapters.base import (
    DeterministicExportAdapter,
    ExportBundleArtifact,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.registry import LoadedDomainPack
from src.schemas.curation_workspace import CurationExportPayloadContext, SubmissionMode


DEMO_ADAPTER_KEY = "demo"
DEMO_DOMAIN_PACK_ID = "org.custom.demo_record"
DEMO_TARGET_KEY = "demo.records.archive"


class DemoRecordExportAdapter(DeterministicExportAdapter):
    def __init__(self) -> None:
        super().__init__(
            adapter_key=DEMO_ADAPTER_KEY,
            supported_target_keys=(DEMO_TARGET_KEY,),
        )

    def build_export_bundle(
        self,
        *,
        mode: SubmissionMode,
        target_key: str,
        export_context: CurationExportPayloadContext,
    ) -> ExportBundleArtifact:
        records = [
            {
                "candidate_id": candidate["candidate_id"],
                "record_id": candidate["payload"]["record"]["record_id"],
                "title": candidate["payload"]["record"]["title"],
                "review_status": candidate["payload"]["review"]["status"],
            }
            for candidate in export_context.domain_envelope_candidates
        ]
        payload = {
            "adapter_key": self.adapter_key,
            "mode": mode.value,
            "target_key": target_key,
            "domain_pack_id": DEMO_DOMAIN_PACK_ID,
            "records": records,
        }
        return ExportBundleArtifact(
            payload_json=payload,
            payload_text=json.dumps(payload, indent=2, sort_keys=True),
            content_type="application/json",
            filename=f"{self.adapter_key}-{export_context.session_id}-records.json",
        )


def validate_demo_record_envelope(_envelope: object) -> tuple:
    return ()


def register_curation_adapters(registry) -> None:
    domain_pack = _load_demo_domain_pack()
    registry.register_adapter(
        adapter_key=DEMO_ADAPTER_KEY,
        candidate_normalizer=object(),
        export_adapter=DemoRecordExportAdapter(),
        domain_pack=domain_pack,
        domain_envelope_validator=validate_demo_record_envelope,
        review_row_materializer=DomainPackMetadataReviewRowMaterializer(
            metadata=domain_pack.metadata,
        ),
    )


def _load_demo_domain_pack() -> LoadedDomainPack:
    package_root = Path(__file__).resolve().parents[3]
    pack_path = package_root / "domain_packs" / "demo_record"
    metadata_path = pack_path / "domain_pack.yaml"
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
        package_id="org.custom",
        package_display_name="Custom Organization Runtime",
        package_version="1.0.0",
    )
