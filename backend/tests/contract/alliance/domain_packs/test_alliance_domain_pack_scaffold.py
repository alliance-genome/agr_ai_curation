"""Contract tests for Alliance domain-pack scaffold LinkML grounding."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.schemas.domain_pack_metadata import DomainPackFieldType


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_BASE_DOMAIN_PACK_ID,
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    ALLIANCE_LINKML_REPOSITORY,
    ALLIANCE_LINKML_ROOT_SCHEMA_PATH,
    ALLIANCE_LINKML_SCHEMA_DIR,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
    REQUIRED_OBJECT_ROLES,
    get_alliance_domain_pack_metadata_path,
    get_alliance_domain_packs_dir,
    load_alliance_domain_pack_registry,
)


BUILTIN_LINKML_RANGES = {
    "boolean",
    "date",
    "datetime",
    "decimal",
    "double",
    "float",
    "integer",
    "string",
    "time",
    "uri",
    "uriorcurie",
}
LINKML_REF_KEYS = frozenset({"class", "slot", "range"})


def _repo_root() -> Path:
    return REPO_ROOT


def _schema_cache_script_path() -> Path:
    return _repo_root() / "scripts" / "testing" / "cache_agr_curation_schema.sh"


def _parse_env_assignments(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        key, _, raw_value = raw_line.partition("=")
        parsed[key] = shlex.split(raw_value)[0]
    return parsed


def _cache_schema(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    script_path = _schema_cache_script_path()
    result = subprocess.run(
        [str(script_path), "--cache-root", str(tmp_path)],
        check=True,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "AGR_CURATION_SCHEMA_REPO_URL": ALLIANCE_LINKML_REPOSITORY,
            "AGR_CURATION_SCHEMA_COMMIT": ALLIANCE_LINKML_COMMIT,
        },
    )
    env_values = _parse_env_assignments(result.stdout)
    return Path(env_values["AGR_CURATION_SCHEMA_CACHE_DIR"]), env_values


def _load_linkml_index(
    schema_cache_dir: Path,
) -> dict[str, dict[str, tuple[str, Mapping[str, Any]]]]:
    index: dict[str, dict[str, tuple[str, Mapping[str, Any]]]] = {
        "classes": {},
        "slots": {},
        "enums": {},
        "types": {},
    }
    schema_dir = schema_cache_dir / ALLIANCE_LINKML_SCHEMA_DIR
    for schema_file in sorted(schema_dir.glob("*.yaml")):
        schema_data = yaml.safe_load(schema_file.read_text(encoding="utf-8")) or {}
        relative_schema_file = schema_file.relative_to(schema_cache_dir).as_posix()
        for section in index:
            for name, definition in (schema_data.get(section) or {}).items():
                index[section][name] = (relative_schema_file, definition or {})
    return index


def _metadata_provider_ref(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    provider_refs = metadata.get(PROVIDER_REFS_METADATA_KEY)
    if not isinstance(provider_refs, Mapping):
        return None
    provider_ref = provider_refs.get(ALLIANCE_LINKML_PROVIDER_KEY)
    if not isinstance(provider_ref, Mapping):
        return None
    return provider_ref


def _iter_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mappings(child)


def _iter_linkml_provider_refs(metadata: Any) -> Iterator[Mapping[str, Any]]:
    for mapping in _iter_mappings(metadata.model_dump(mode="python")):
        provider_ref = _metadata_provider_ref(mapping)
        if provider_ref is not None and LINKML_REF_KEYS.intersection(provider_ref):
            yield provider_ref


def _assert_source_file_matches(
    *,
    provider_ref: Mapping[str, Any],
    actual_file: str,
    ref_kind: str,
    ref_name: str,
) -> None:
    expected_file = provider_ref.get("source_file")
    if expected_file is not None:
        assert expected_file == actual_file, (
            f"{ref_kind} {ref_name} expected source_file {expected_file}, "
            f"but LinkML index found {actual_file}"
        )


def _assert_range_exists(
    index: Mapping[str, Mapping[str, Any]],
    provider_ref: Mapping[str, Any],
) -> None:
    range_name = provider_ref.get("range")
    if range_name is None:
        return

    assert (
        range_name in index["classes"]
        or range_name in index["enums"]
        or range_name in index["types"]
        or range_name in BUILTIN_LINKML_RANGES
    ), f"LinkML range {range_name} is missing from the pinned schema index"


def test_alliance_loader_hooks_load_bundled_domain_pack():
    registry = load_alliance_domain_pack_registry()

    assert registry.failed_packs == ()
    assert registry.packs_dir == get_alliance_domain_packs_dir()
    assert registry.get_pack(ALLIANCE_BASE_DOMAIN_PACK_ID) is not None
    assert get_alliance_domain_pack_metadata_path().is_file()


def test_alliance_schema_refs_are_nested_in_provider_metadata():
    metadata = load_domain_pack_metadata(get_alliance_domain_pack_metadata_path())
    assert metadata.pack_id == ALLIANCE_BASE_DOMAIN_PACK_ID

    pack_provider_ref = _metadata_provider_ref(metadata.metadata)
    assert pack_provider_ref is not None
    assert pack_provider_ref["repository"] == ALLIANCE_LINKML_REPOSITORY
    assert pack_provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
    assert pack_provider_ref["root_schema"] == ALLIANCE_LINKML_ROOT_SCHEMA_PATH

    for schema_ref in metadata.schema_refs:
        provider_ref = _metadata_provider_ref(schema_ref.metadata)
        assert provider_ref is not None
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert schema_ref.provider == ALLIANCE_LINKML_PROVIDER_KEY

    for provider_ref in _iter_linkml_provider_refs(metadata):
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert provider_ref["schema_ref"] == "alliance.linkml"


def test_alliance_base_scaffold_declares_required_object_roles():
    metadata = load_domain_pack_metadata(get_alliance_domain_pack_metadata_path())
    roles_by_object_type = {
        object_definition.object_type: object_definition.metadata[OBJECT_ROLE_METADATA_KEY]
        for object_definition in metadata.object_definitions
    }

    assert set(REQUIRED_OBJECT_ROLES).issubset(set(roles_by_object_type.values()))
    assert roles_by_object_type["GeneExpressionAnnotation"] == "curatable_unit"
    assert roles_by_object_type["Gene"] == "validated_reference"
    assert roles_by_object_type["Reference"] == "validated_reference"
    assert roles_by_object_type["ExpressionPatternContext"] == "metadata_only"

    curatable_unit = next(
        item
        for item in metadata.object_definitions
        if item.object_type == "GeneExpressionAnnotation"
    )
    object_ref_fields = {
        field.field_path: field.object_type_ref
        for field in curatable_unit.fields
        if field.field_type is DomainPackFieldType.OBJECT_REF
    }
    assert object_ref_fields == {
        "gene": "Gene",
        "reference": "Reference",
        "expression_pattern": "ExpressionPatternContext",
    }


def test_cache_script_materializes_pinned_schema_checkout(tmp_path: Path):
    first_cache_dir, first_env = _cache_schema(tmp_path)
    second_cache_dir, second_env = _cache_schema(tmp_path)

    assert first_cache_dir == second_cache_dir
    assert first_env["AGR_CURATION_SCHEMA_CACHE_STATUS"] == "created"
    assert second_env["AGR_CURATION_SCHEMA_CACHE_STATUS"] == "reused"
    assert (first_cache_dir / ALLIANCE_LINKML_ROOT_SCHEMA_PATH).is_file()

    actual_commit = subprocess.run(
        ["git", "-C", str(first_cache_dir), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert actual_commit == ALLIANCE_LINKML_COMMIT


def test_pinned_linkml_class_slot_and_range_refs_exist(tmp_path: Path):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = load_domain_pack_metadata(get_alliance_domain_pack_metadata_path())

    provider_refs = tuple(_iter_linkml_provider_refs(metadata))
    assert provider_refs

    class_refs = {
        provider_ref["class"] for provider_ref in provider_refs if "class" in provider_ref
    }
    slot_refs = {
        provider_ref["slot"] for provider_ref in provider_refs if "slot" in provider_ref
    }
    range_refs = {
        provider_ref["range"] for provider_ref in provider_refs if "range" in provider_ref
    }
    assert class_refs
    assert slot_refs
    assert range_refs

    for provider_ref in provider_refs:
        class_name = provider_ref.get("class")
        if class_name is not None:
            assert class_name in index["classes"], (
                f"LinkML class {class_name} is missing from pinned schema"
            )
            actual_file, _definition = index["classes"][class_name]
            if "slot" not in provider_ref:
                _assert_source_file_matches(
                    provider_ref=provider_ref,
                    actual_file=actual_file,
                    ref_kind="class",
                    ref_name=class_name,
                )

        slot_name = provider_ref.get("slot")
        if slot_name is not None:
            assert slot_name in index["slots"], (
                f"LinkML slot {slot_name} is missing from pinned schema"
            )
            actual_file, _definition = index["slots"][slot_name]
            _assert_source_file_matches(
                provider_ref=provider_ref,
                actual_file=actual_file,
                ref_kind="slot",
                ref_name=slot_name,
            )

        _assert_range_exists(index, provider_ref)
