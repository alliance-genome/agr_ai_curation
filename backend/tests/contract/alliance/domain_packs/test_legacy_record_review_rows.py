"""Records stored before ALL-1283 read once through the legacy rule in review rows.

The fixtures under ``tests/fixtures/domain_packs/legacy_795439c34`` are every
Alliance pack fixture as it stood at 795439c34, before the extracted-vs-validated
contract: stand-ins for records already in production. Each is materialized
through its pack's registered review-row materializer; no value may read as a
broken record, and no paper wording may carry a suffix twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.materialization import _registered_materializer_for
from src.lib.domain_packs.resolvable_values import INVALID_RECORD_SUFFIX, LEGACY_UNVERIFIED_SUFFIX

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

LEGACY_FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures" / "domain_packs" / "legacy_795439c34"


def _readings(row):
    for group in (row.metadata.get("workspace_fields", []), [field.model_dump(mode="json") for field in row.summary_fields]):
        for field in group:
            resolution = field.get("resolution") or {}
            yield from resolution.get("values", [])


@pytest.mark.parametrize("fixture_path", sorted(LEGACY_FIXTURES.glob("*.yaml")), ids=lambda path: path.stem)
def test_a_legacy_record_reads_once_through_the_legacy_rule(fixture_path):
    fixtures = load_domain_fixture_pack(fixture_path)
    materializer = _registered_materializer_for(fixtures.domain_pack_id)
    readings = []
    for fixture in fixtures.fixtures:
        for row in materializer.materialize(fixture.envelope, envelope_revision=1):
            readings.extend(_readings(row))
            assert row.display_label.count(LEGACY_UNVERIFIED_SUFFIX) <= 1, row.display_label

    assert readings, "the fixture has declared resolvable values to read"
    assert [reading for reading in readings if reading["lookup_outcome"] == "invalid_schema"] == []
    for reading in readings:
        mention = reading.get("mention") or ""
        assert INVALID_RECORD_SUFFIX not in mention, reading
        assert mention.count(LEGACY_UNVERIFIED_SUFFIX) <= 1, reading
        assert reading.get("issue") is None, reading


def test_legacy_disease_subject_and_term_references_show_only_unverified_wording():
    """Fix-wave re-review S4: an old DiseaseAnnotationSubject or DOTerm reference stored the
    extractor's subject id or DOID; its row shows that text labelled "(legacy, unverified)",
    never as an identity, and exports read it the same way."""

    from agr_ai_curation_alliance.domain_packs.disease.legacy import legacy_display_payload

    references = 0
    for fixture_path in sorted(LEGACY_FIXTURES.glob("disease__*.yaml")):
        fixtures = load_domain_fixture_pack(fixture_path)
        materializer = _registered_materializer_for(fixtures.domain_pack_id)
        for fixture in fixtures.fixtures:
            references += _check_disease_references(materializer, fixture.envelope, legacy_display_payload)
    assert references, "the legacy disease fixtures store subject and term references"


def _check_disease_references(materializer, envelope, legacy_display_payload) -> int:
    stored = {obj.object_id or obj.pending_ref_id: obj.payload for obj in envelope.extracted_objects}
    references = 0
    for row in materializer.materialize(envelope, envelope_revision=1):
        if row.object_type not in ("DiseaseAnnotationSubject", "DOTerm"):
            continue
        references += 1
        fields = {field.field_path: field.value for field in row.summary_fields}
        assert {"curie", "name", "subject_identifier", "subject_label", "resolution_state"}.isdisjoint(fields)
        assert fields["mention"].endswith(LEGACY_UNVERIFIED_SUFFIX), fields
        assert legacy_display_payload(row.object_type, stored[row.object_id])["mention"] == fields["mention"]
    return references


def test_a_current_disease_reference_reads_as_stored():
    from agr_ai_curation_alliance.domain_packs.disease.legacy import legacy_display_payload

    current = {"mention": "Alzheimer's disease", "source_mentions": ["a fly model of Alzheimer's disease"]}

    assert legacy_display_payload("DOTerm", current) is current
    assert legacy_display_payload("DiseaseAnnotationSubject", {"mention": "Appl"}) == {"mention": "Appl"}
