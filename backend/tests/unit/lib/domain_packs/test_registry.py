"""Unit tests for provider-agnostic domain-pack metadata loading."""

from pathlib import Path
from shutil import copytree

import pytest

from src.lib.domain_packs.loader import (
    DomainFixturePackError,
    DomainPackMetadataError,
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from src.lib.domain_packs.registry import (
    DomainPackRegistryValidationError,
    load_domain_pack_registry,
)
from src.schemas.domain_pack_metadata import DomainPackFieldType, DomainPackStatus


PROVIDER_AGNOSTIC_PACKS_DIR = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "domain_packs"
    / "provider_agnostic"
)

pytestmark = pytest.mark.provider_agnostic_domain_pack


def _write_domain_pack(root: Path, directory_name: str, metadata_text: str) -> Path:
    pack_dir = root / directory_name
    pack_dir.mkdir()
    (pack_dir / "domain_pack.yaml").write_text(metadata_text.strip(), encoding="utf-8")
    return pack_dir


def _valid_metadata_text(pack_id: str = "fixture.core") -> str:
    return f"""
pack_id: {pack_id}
display_name: Fixture Core Domain Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
enum_definitions:
  - enum_id: ConfidenceLevel
    display_name: Confidence level
    values:
      - value: high
      - value: medium
      - value: low
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    definition_state: in_development
    definition_notes:
      - Payload fields are intentionally minimal for fixture validation.
    fields:
      - field_path: gene.symbol
        field_type: string
        required: true
      - field_path: confidence
        field_type: enum
        enum_ref: ConfidenceLevel
      - field_path: evidence[0].snippet
        field_type: string
fixture_packs:
  - fixture_pack_id: smoke
    display_name: Smoke fixtures
    path: fixtures/smoke.yaml
    object_types:
      - GeneAssertion
""".strip()


def _copy_provider_agnostic_packs(tmp_path: Path) -> Path:
    packs_dir = tmp_path / "domain_packs"
    copytree(PROVIDER_AGNOSTIC_PACKS_DIR, packs_dir)
    return packs_dir


def test_load_domain_pack_metadata_parses_provider_agnostic_fixture_pack_ref(tmp_path: Path):
    pack_dir = _write_domain_pack(tmp_path, "fixture.core", _valid_metadata_text())

    metadata = load_domain_pack_metadata(pack_dir / "domain_pack.yaml")

    assert metadata.pack_id == "fixture.core"
    assert metadata.status is DomainPackStatus.ACTIVE
    assert metadata.object_definitions[0].fields[1].field_type is DomainPackFieldType.ENUM
    assert metadata.fixture_packs[0].path == "fixtures/smoke.yaml"


def test_registry_loads_provider_agnostic_fixture_pack_with_json_schema_refs():
    registry = load_domain_pack_registry(PROVIDER_AGNOSTIC_PACKS_DIR)

    pack = registry.get_pack("museum.catalog")
    assert pack is not None
    fixture_ref = registry.get_fixture_pack_ref("museum.catalog", "smoke")
    assert fixture_ref is not None

    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    fixture = fixture_pack.fixtures[0]

    assert pack.metadata.schema_refs[0].provider == "json-schema"
    assert pack.metadata.model_definitions[0].model_id == "ArtifactPayload"
    assert pack.metadata.model_definitions[0].schema_ref.provider == "json-schema"
    assert pack.metadata.object_definitions[0].model_ref == "ArtifactPayload"
    assert fixture_pack.domain_pack_id == "museum.catalog"
    assert fixture.envelope.schema_ref.provider == "json-schema"
    assert fixture.envelope.objects[0].object_type == "MuseumArtifact"
    assert fixture.envelope.objects[0].schema_ref.provider == "json-schema"
    assert fixture.envelope.objects[1].object_refs[0].pending_ref_id == "artifact-1"


def test_metadata_loader_fails_on_invalid_enum_reference(tmp_path: Path):
    metadata_text = _valid_metadata_text().replace(
        "enum_ref: ConfidenceLevel",
        "enum_ref: MissingConfidenceLevel",
    )
    pack_dir = _write_domain_pack(tmp_path, "fixture.core", metadata_text)

    with pytest.raises(DomainPackMetadataError) as exc_info:
        load_domain_pack_metadata(pack_dir / "domain_pack.yaml")

    message = str(exc_info.value)
    assert "enum_ref references unknown enum 'MissingConfidenceLevel'" in message
    assert "domain_pack.yaml" in message


def test_metadata_loader_fails_on_invalid_model_reference(tmp_path: Path):
    metadata_text = _valid_metadata_text().replace(
        "model_ref: GeneAssertionPayload",
        "model_ref: MissingPayload",
    )
    pack_dir = _write_domain_pack(tmp_path, "fixture.core", metadata_text)

    with pytest.raises(DomainPackMetadataError) as exc_info:
        load_domain_pack_metadata(pack_dir / "domain_pack.yaml")

    assert "model_ref references unknown model 'MissingPayload'" in str(exc_info.value)


@pytest.mark.parametrize(
    ("original", "replacement", "expected_message"),
    [
        (
            "enum_ref: ReviewStatus",
            "enum_ref: MissingReviewStatus",
            "enum_ref references unknown enum 'MissingReviewStatus'",
        ),
        (
            "model_ref: ArtifactPayload",
            "model_ref: MissingArtifactPayload",
            "model_ref references unknown model 'MissingArtifactPayload'",
        ),
    ],
)
def test_registry_rejects_provider_agnostic_metadata_with_invalid_refs(
    tmp_path: Path,
    original: str,
    replacement: str,
    expected_message: str,
):
    packs_dir = _copy_provider_agnostic_packs(tmp_path)
    metadata_path = packs_dir / "museum.catalog" / "domain_pack.yaml"
    metadata_text = metadata_path.read_text(encoding="utf-8")
    assert original in metadata_text
    metadata_path.write_text(
        metadata_text.replace(original, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(DomainPackRegistryValidationError) as exc_info:
        load_domain_pack_registry(packs_dir)

    message = str(exc_info.value)
    assert "Failed to load domain pack 'museum.catalog' metadata" in message
    assert expected_message in message


def test_registry_can_collect_invalid_metadata_diagnostics_without_raising(
    tmp_path: Path,
):
    packs_dir = _copy_provider_agnostic_packs(tmp_path)
    metadata_path = packs_dir / "museum.catalog" / "domain_pack.yaml"
    metadata_path.write_text(
        metadata_path.read_text(encoding="utf-8").replace(
            "enum_ref: ReviewStatus",
            "enum_ref: MissingReviewStatus",
            1,
        ),
        encoding="utf-8",
    )

    registry = load_domain_pack_registry(packs_dir, fail_on_validation_error=False)

    assert registry.loaded_packs == ()
    assert len(registry.failed_packs) == 1
    assert "MissingReviewStatus" in registry.failed_packs[0].reason
    assert "MissingReviewStatus" in registry.validation_errors[0]


def test_registry_loads_domain_pack_and_fixture_metadata_without_linkml_fields(tmp_path: Path):
    pack_dir = _write_domain_pack(tmp_path, "fixture.core", _valid_metadata_text())
    fixtures_dir = pack_dir / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "smoke.yaml").write_text(
        """
fixture_pack_id: smoke
domain_pack_id: fixture.core
fixtures_api_version: 1.0.0
display_name: Smoke fixtures
fixtures:
  - name: minimal_gene_assertion
    envelope:
      envelope_id: fixture-env-1
      domain_pack_id: fixture.core
      objects:
        - object_type: GeneAssertion
          pending_ref_id: pending-gene-1
          payload:
            gene:
              symbol: abc-1
            confidence: high
            evidence:
              - snippet: abc-1 is expressed in neurons.
      validation_findings:
        - severity: info
          message: Smoke fixture field ref validates through the core envelope.
          field_ref:
            object_ref:
              pending_ref_id: pending-gene-1
            field_path: gene.symbol
""".strip(),
        encoding="utf-8",
    )

    registry = load_domain_pack_registry(tmp_path)
    fixture_ref = registry.get_fixture_pack_ref("fixture.core", "smoke")
    fixture_pack = load_domain_fixture_pack(pack_dir / fixture_ref.path)

    assert registry.get_pack("fixture.core").metadata.fixture_packs[0].fixture_pack_id == "smoke"
    assert fixture_pack.fixtures[0].envelope.objects[0].pending_ref_id == "pending-gene-1"


def test_fixture_pack_loader_rejects_envelope_domain_pack_mismatch(tmp_path: Path):
    fixture_path = tmp_path / "bad-fixtures.yaml"
    fixture_path.write_text(
        """
fixture_pack_id: smoke
domain_pack_id: fixture.core
fixtures_api_version: 1.0.0
display_name: Smoke fixtures
fixtures:
  - name: mismatch
    envelope:
      envelope_id: fixture-env-1
      domain_pack_id: other.pack
      objects:
        - object_type: GeneAssertion
          pending_ref_id: pending-gene-1
          payload:
            gene:
              symbol: abc-1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(DomainFixturePackError) as exc_info:
        load_domain_fixture_pack(fixture_path)

    assert "does not match fixture pack domain_pack_id 'fixture.core'" in str(exc_info.value)


def test_registry_fails_on_duplicate_pack_ids(tmp_path: Path):
    _write_domain_pack(tmp_path, "fixture-a", _valid_metadata_text("fixture.core"))
    _write_domain_pack(tmp_path, "fixture-b", _valid_metadata_text("fixture.core"))

    with pytest.raises(DomainPackRegistryValidationError) as exc_info:
        load_domain_pack_registry(tmp_path)

    assert "Duplicate pack_id 'fixture.core'" in str(exc_info.value)
