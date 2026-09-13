"""Identity guards for deployment-owned dependencies we do not snapshot.

No credentials, tool instances, or external database contents are captured.
Package versions and binding metadata do not prove immutable Python code or
provider/data revisions; those residual limits are explicit in provenance.
"""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .frozen_flow import freeze_json, thaw_json


class BenchmarkDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    runtime_catalog_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    domain_pack_digests: Mapping[str, str]
    execution_settings: Mapping[str, Any]
    planned_cell_order: Literal["case_configuration_repetition"] = "case_configuration_repetition"
    randomized: Literal[False] = False
    actual_dispatch_order: Literal["worker_scheduling_not_randomized"] = "worker_scheduling_not_randomized"
    external_data: Literal["live_not_snapshotted"] = "live_not_snapshotted"
    provider_revision: Literal["not_pinned"] = "not_pinned"
    tool_implementation: Literal["deployment_owned_not_snapshotted"] = "deployment_owned_not_snapshotted"

    @field_validator("domain_pack_digests", "execution_settings")
    @classmethod
    def freeze_maps(cls, value):
        return freeze_json(value)

    @field_serializer("domain_pack_digests", "execution_settings")
    def serialize_maps(self, value):
        return thaw_json(value)


def execution_settings() -> dict[str, Any]:
    """Read named non-secret controls through their normal environment getters."""
    from src.lib.openai_agents import config

    names = (
        "benchmark_environment_id", "benchmark_worker_concurrency", "benchmark_cell_timeout_seconds",
        "max_turns", "max_parallel_validators", "validator_batch_max_size", "validator_max_tool_calls",
        "structured_finalization_max_attempts", "structured_finalization_hard_max_attempts",
        "structured_finalization_retry_max_turns", "flow_supervisor_parallel_tool_calls_enabled",
        "flow_selected_fields_direct_export", "flow_step_output_preview_chars",
        "flow_step_evidence_preview_limit", "flow_memory_max_visible_output_chars",
    )
    return {name: getattr(config, "get_" + name)() for name in names}


def runtime_catalog_digest() -> str:
    """Hash the same cached registry normal tool construction consumes.

    This is deliberately a deployment-registry identity, not a claim to pin
    transitive Python implementation bytes. No tool factory is invoked here.
    """
    from src.lib.agent_studio.catalog_service import _load_package_tool_registry
    from .suites import _digest

    registry = _load_package_tool_registry()
    return _digest({
        "runtime_version": registry.package_registry.runtime_version,
        "packages": {package.package_id: package.manifest.model_dump(mode="json")
                     for package in registry.package_registry.loaded_packages},
        "bindings": {binding.tool_id: {
            "kind": binding.binding_kind.value, "import_path": binding.import_path,
            "attribute_kind": binding.import_attribute_kind, "context": list(binding.required_context),
            "metadata": binding.metadata, "adapters": binding.provider_adapters,
            "package": binding.source.package_id, "version": binding.source.package_version,
        } for binding in registry.bindings},
    })


def domain_pack_digest(pack_id: str) -> str:
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from .suites import _digest

    pack = resolve_curation_domain_pack_by_id(pack_id)
    if pack is None:
        raise ValueError("Benchmark domain package is unavailable")
    return _digest({"metadata": pack.metadata.model_dump(mode="json"),
                    "package_id": pack.package_id, "package_version": pack.package_version})


def capture_dependencies(session, curator, target) -> BenchmarkDependencies:
    from src.lib.agent_studio.execution_revision_service import get_execution_revision

    pack_ids = set()
    curations = [source.curation_metadata for source in target.system_agent_snapshots.values()]
    for receipt in target.source_execution_receipts.values():
        _, source = get_execution_revision(
            session, receipt.agent_id, receipt.agent_revision_id, curator.db_user_id,
            active_group_ids=list(curator.active_groups),
        )
        curations.append(source.curation)
    for curation in curations:
        if curation and curation.get("domain_pack_id"):
            pack_ids.add(curation["domain_pack_id"])
    return BenchmarkDependencies(
        runtime_catalog_digest=runtime_catalog_digest(),
        domain_pack_digests={key: domain_pack_digest(key) for key in sorted(pack_ids)},
        execution_settings=execution_settings(),
    )


def require_current_dependencies(snapshot: BenchmarkDependencies | None) -> None:
    if snapshot is None:
        # Historical plans remain historical, never backfilled as certified.
        return
    if (snapshot.runtime_catalog_digest != runtime_catalog_digest()
            or snapshot.execution_settings != execution_settings()
            or any(domain_pack_digest(key) != digest for key, digest in snapshot.domain_pack_digests.items())):
        raise ValueError("Benchmark execution dependencies changed; prepare a new experiment revision")
