"""Evidence that names its file, with or without a page, through every domain pack (ALL-1460, P8b3).

Since 0.12.0 record_evidence writes ``file_id`` and ``file_name`` on each record
and on each source fragment, and a quote from a converted Word or Excel file has
no page. Before P8b3 the canonical ``EvidenceSourceFragment`` forbade those keys,
so every pack's ``normalize_workspace_records`` dropped the whole record; and the
extractor schemas and conversions required a page. A record without ``file_id``
(saved before files were recorded) keeps today's rules exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.allele.conversion import (  # noqa: E402
    AlleleBuilderExtractionOutput,
    _normalized_evidence_records as allele_normalize,
    validate_allele_builder_objects,
)
from agr_ai_curation_alliance.domain_packs.allele.export import (  # noqa: E402
    build_allele_association_export,
)
from agr_ai_curation_alliance.domain_packs.disease.builder_conversion import (  # noqa: E402
    DiseaseBuilderExtractionOutput,
    _normalized_evidence_records as disease_normalize,
    validate_disease_builder_objects,
)
from agr_ai_curation_alliance.domain_packs.gene.conversion import (  # noqa: E402
    GeneBuilderExtractionOutput,
    _gene_normalized_evidence_records as gene_normalize,
    validate_gene_builder_objects,
)
from agr_ai_curation_alliance.domain_packs.gene.export import (  # noqa: E402
    build_gene_mention_evidence_export,
)
from agr_ai_curation_alliance.domain_packs.gene_expression.conversion import (  # noqa: E402
    _normalized_evidence_records as gene_expression_normalize,
)
from agr_ai_curation_alliance.domain_packs.generic.conversion import (  # noqa: E402
    _normalized_evidence_records as generic_normalize,
)
from agr_ai_curation_alliance.domain_packs.go.conversion import (  # noqa: E402
    _normalized_evidence_records as go_normalize,
)
from agr_ai_curation_alliance.domain_packs.phenotype.conversion import (  # noqa: E402
    PhenotypeBuilderExtractionOutput,
    _normalized_evidence_records as phenotype_normalize,
    validate_phenotype_builder_objects,
)
from src.schemas.domain_envelope import DomainEnvelope  # noqa: E402
from src.schemas.evidence_workspace import normalize_workspace_records  # noqa: E402

from tests.contract.alliance.domain_packs import (  # noqa: E402
    test_allele_builder_domain_pack as allele_tests,
    test_disease_builder_domain_pack as disease_tests,
    test_gene_domain_pack as gene_tests,
    test_gene_expression_domain_pack as gene_expression_tests,
    test_phenotype_builder_domain_pack as phenotype_tests,
)

ALL_PACKS = [
    pytest.param(generic_normalize, id="generic"),
    pytest.param(allele_normalize, id="allele"),
    pytest.param(gene_normalize, id="gene"),
    pytest.param(gene_expression_normalize, id="gene_expression"),
    pytest.param(go_normalize, id="go"),
    pytest.param(phenotype_normalize, id="phenotype"),
    pytest.param(disease_normalize, id="disease"),
    # The topic-tag pack calls the shared projection directly.
    pytest.param(normalize_workspace_records, id="topic_entity_tag"),
]

FILE_ID = "6f1c2a9e-5b4d-4c3e-9a8b-7d6e5f4a3b2c"
FILE_NAME = "Table_S1.xlsx"
CHUNK_ID = "4c1e2a9e-5b4d-4c3e-9a8b-7d6e5f4a3b2c"
SPAN_ID = f"{CHUNK_ID}:s0002:c0000-c0017:1a2b3c4d"


def _workspace_record_from_record_evidence(*, evidence_record_id: str, page: int | None = None,
                                           **overrides) -> dict:
    """What record_evidence stores for a row of a converted spreadsheet (made-up values)."""

    fragment = {
        "span_id": SPAN_ID,
        "chunk_id": CHUNK_ID,
        "document_id": "doc-1",
        "file_id": FILE_ID,
        "file_name": FILE_NAME,
        "text": "| abc-1 | 3 | 5 |",
        "char_start": 0,
        "char_end": 17,
        "text_hash": "1a2b3c4d",
        "span_index": 2,
        "span_type": "table_row",
        "spanizer_version": "pdf_sentence_v1",
        "section": "Sheet1",
    }
    record = {
        "status": "verified",
        "entity": "abc-1",
        "span_ids": [SPAN_ID],
        "source_span_ids": [SPAN_ID],
        "verified_quote": "| abc-1 | 3 | 5 |",
        "document_id": "doc-1",
        "file_id": FILE_ID,
        "file_name": FILE_NAME,
        "chunk_id": CHUNK_ID,
        "chunk_ids": [CHUNK_ID],
        "source_fragments": [fragment],
        "section": "Sheet1",
        "evidence_record_id": evidence_record_id,
        "updated_at": "2026-11-22T12:00:00+00:00",
    }
    if page is not None:
        record["page"] = page
        fragment["page"] = page
    record.update(overrides)
    return record


@pytest.mark.parametrize("normalize", ALL_PACKS)
def test_a_page_less_record_that_names_its_file_is_kept_by_every_pack(normalize):
    kept, = normalize([_workspace_record_from_record_evidence(evidence_record_id="evidence-1")])

    assert kept["file_id"] == FILE_ID
    assert kept["file_name"] == FILE_NAME
    assert "page" not in kept
    assert kept["source_fragments"][0]["file_id"] == FILE_ID
    assert kept["source_fragments"][0]["file_name"] == FILE_NAME


@pytest.mark.parametrize("normalize", ALL_PACKS)
def test_a_paged_record_that_names_its_file_is_kept_by_every_pack(normalize):
    kept, = normalize([_workspace_record_from_record_evidence(evidence_record_id="evidence-1", page=4)])

    assert (kept["file_id"], kept["page"]) == (FILE_ID, 4)


@pytest.mark.parametrize("normalize", ALL_PACKS)
def test_a_record_saved_before_files_were_recorded_projects_as_before(normalize):
    # A pre-0.12.0 workspace record: a page, no file. Expected = origin/main a88e06413's output.
    stored = {
        "status": "verified",
        "entity": "Adgrl1",
        "span_ids": ["span-1"],
        "source_span_ids": ["span-1"],
        "verified_quote": "The allele was generated by CRISPR mutagenesis.",
        "page": 3,
        "section": "Results",
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "source_fragments": [{"span_id": "span-1", "chunk_id": "chunk-1", "text": "The allele", "page": 3}],
        "evidence_record_id": "evidence-1",
        "updated_at": "2026-09-18T12:31:54+00:00",
    }

    assert normalize([stored]) == [
        {
            "evidence_record_id": "evidence-1",
            "entity": "Adgrl1",
            "verified_quote": "The allele was generated by CRISPR mutagenesis.",
            "page": 3,
            "section": "Results",
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "source_span_ids": ["span-1"],
            "source_fragments": [
                {"span_id": "span-1", "chunk_id": "chunk-1", "text": "The allele", "page": 3}
            ],
        }
    ]


@pytest.mark.parametrize(
    ("module", "output_model", "validate"),
    [
        pytest.param(gene_tests, GeneBuilderExtractionOutput, validate_gene_builder_objects, id="gene"),
        pytest.param(allele_tests, AlleleBuilderExtractionOutput, validate_allele_builder_objects, id="allele"),
        pytest.param(
            phenotype_tests, PhenotypeBuilderExtractionOutput, validate_phenotype_builder_objects,
            id="phenotype",
        ),
        pytest.param(disease_tests, DiseaseBuilderExtractionOutput, validate_disease_builder_objects, id="disease"),
    ],
)
@pytest.mark.parametrize("without_section", [False, True], ids=["with_section", "without_section"])
def test_page_less_file_evidence_reaches_the_builder_envelope(
    module, output_model, validate, without_section, monkeypatch
):
    evidence_record_id = module._evidence_records()[0]["evidence_record_id"]
    monkeypatch.setattr(
        module, "_evidence_records",
        lambda: _page_less_records_from(module, evidence_record_id, without_section=without_section),
    )

    result = module._materialize_one_candidate()

    assert result.ok, result.summary()
    output = output_model.model_validate(result.payload)
    assert validate(output) == ()
    record, = [
        record for record in result.payload["metadata"]["evidence_records"]
        if record["evidence_record_id"] == evidence_record_id
    ]
    assert (record["file_id"], record.get("page")) == (FILE_ID, None)
    assert record["source_fragments"][0]["file_id"] == FILE_ID
    assert ("section" in record) is not without_section


_ORIGINAL_RECORDS = {}


def _page_less_records_from(module, evidence_record_id, *, without_section=False):
    if module not in _ORIGINAL_RECORDS:
        raise AssertionError("original records not captured")
    original = _ORIGINAL_RECORDS[module]
    record = _workspace_record_from_record_evidence(evidence_record_id=evidence_record_id)
    for key in ("entity", "verified_quote", "section", "subsection", "chunk_id",
                "envelope_target", "envelope_targets"):
        if key in original:
            record[key] = original[key]
    record["source_fragments"][0]["text"] = original["verified_quote"]
    if without_section:
        # Text before a file's first heading: record_evidence omits section and subsection.
        for target in (record, record["source_fragments"][0]):
            target.pop("section", None)
            target.pop("subsection", None)
    return [record]


@pytest.fixture(autouse=True)
def _capture_original_records():
    for module in (gene_tests, allele_tests, phenotype_tests, disease_tests):
        _ORIGINAL_RECORDS[module] = module._evidence_records()[0]


def test_page_less_file_evidence_reaches_the_gene_expression_envelope(monkeypatch):
    original, = gene_expression_tests._gene_expression_builder_evidence_records()
    record = _workspace_record_from_record_evidence(evidence_record_id=original["evidence_record_id"])
    for key in ("entity", "verified_quote", "section", "chunk_id", "envelope_target", "envelope_targets"):
        if key in original:
            record[key] = original[key]
    monkeypatch.setattr(
        gene_expression_tests, "_gene_expression_builder_evidence_records", lambda: [record]
    )

    result = gene_expression_tests._materialize_gene_expression_candidate(
        gene_expression_tests._gene_expression_builder_staged_fields()
    )

    assert result.ok, result.summary()
    stored, = result.payload["metadata"]["evidence_records"]
    assert (stored["file_id"], stored.get("page")) == (FILE_ID, None)


def test_page_less_file_evidence_exports_from_the_gene_envelope(monkeypatch):
    evidence_record_id = gene_tests._evidence_records()[0]["evidence_record_id"]
    monkeypatch.setattr(
        gene_tests, "_evidence_records", lambda: _page_less_records_from(gene_tests, evidence_record_id)
    )
    result = gene_tests._materialize_one_candidate()
    envelope = DomainEnvelope(
        envelope_id="gene-file-evidence",
        domain_pack_id=gene_tests.GENE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=result.payload["metadata"],
    )

    record, = build_gene_mention_evidence_export(envelope)["records"]

    # The page key stays in the export, null for a file without page numbers.
    assert record["evidence"]["page"] is None
    assert record["evidence"]["evidence_record_id"] == evidence_record_id


def test_the_gene_export_still_refuses_a_page_less_record_without_a_file(monkeypatch):
    evidence_record_id = gene_tests._evidence_records()[0]["evidence_record_id"]
    monkeypatch.setattr(
        gene_tests, "_evidence_records", lambda: _page_less_records_from(gene_tests, evidence_record_id)
    )
    result = gene_tests._materialize_one_candidate()
    metadata = dict(result.payload["metadata"])
    metadata["evidence_records"] = [
        {key: value for key, value in record.items() if key != "file_id"}
        for record in metadata["evidence_records"]
    ]
    envelope = DomainEnvelope(
        envelope_id="gene-file-evidence",
        domain_pack_id=gene_tests.GENE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=metadata,
    )

    with pytest.raises(ValueError, match="missing page"):
        build_gene_mention_evidence_export(envelope)


def test_page_less_file_evidence_exports_from_the_allele_envelope(monkeypatch):
    evidence_record_id = allele_tests._evidence_records()[0]["evidence_record_id"]
    monkeypatch.setattr(
        allele_tests, "_evidence_records", lambda: _page_less_records_from(allele_tests, evidence_record_id)
    )
    result = allele_tests._materialize_one_candidate()
    envelope = DomainEnvelope(
        envelope_id="allele-file-evidence",
        domain_pack_id=allele_tests.ALLELE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=result.payload["metadata"],
    )

    export_payload = build_allele_association_export(envelope)

    assert export_payload["export_type"] == "alliance_allele_paper_evidence_association"


def test_gene_export_of_evidence_saved_before_files_were_recorded_is_unchanged():
    # The module's paged record, no file: the export's evidence is what origin/main produced.
    result = gene_tests._materialize_one_candidate()
    envelope = DomainEnvelope(
        envelope_id="gene-pre-release-evidence",
        domain_pack_id=gene_tests.GENE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=result.payload["metadata"],
    )

    record, = build_gene_mention_evidence_export(envelope)["records"]

    assert record["evidence"] == {
        "evidence_record_id": "evidence-daf16-1",
        "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
        "page": 4,
        "section": "Results",
        "chunk_id": "chunk-daf16-1",
        "subsection": "Stress response assay",
        "figure_reference": "Figure 2A",
    }
    assert list(record["evidence"]) == [
        "evidence_record_id", "verified_quote", "page", "section", "chunk_id",
        "subsection", "figure_reference",
    ]



def test_section_less_file_evidence_exports_from_the_gene_envelope(monkeypatch):
    evidence_record_id = gene_tests._evidence_records()[0]["evidence_record_id"]
    monkeypatch.setattr(
        gene_tests, "_evidence_records",
        lambda: _page_less_records_from(gene_tests, evidence_record_id, without_section=True),
    )
    result = gene_tests._materialize_one_candidate()
    envelope = DomainEnvelope(
        envelope_id="gene-file-evidence",
        domain_pack_id=gene_tests.GENE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=result.payload["metadata"],
    )

    record, = build_gene_mention_evidence_export(envelope)["records"]

    assert (record["evidence"]["page"], record["evidence"]["section"]) == (None, None)


_PACK_IDS = {
    "gene": lambda: gene_tests.GENE_DOMAIN_PACK_ID,
    "allele": lambda: allele_tests.ALLELE_DOMAIN_PACK_ID,
    "phenotype": lambda: phenotype_tests.PHENOTYPE_DOMAIN_PACK_ID,
    "disease": lambda: disease_tests.DISEASE_DOMAIN_PACK_ID,
}
_BUILDER_MODULES = {
    "gene": gene_tests, "allele": allele_tests, "phenotype": phenotype_tests, "disease": disease_tests,
}


def _structural_findings(pack: str, *, without_section: bool, drop_file_id: bool, monkeypatch):
    from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry
    from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks

    module = _BUILDER_MODULES[pack]
    evidence_record_id = module._evidence_records()[0]["evidence_record_id"]
    monkeypatch.setattr(
        module, "_evidence_records",
        lambda: _page_less_records_from(module, evidence_record_id, without_section=without_section),
    )
    result = module._materialize_one_candidate()
    assert result.ok, result.summary()
    metadata = dict(result.payload["metadata"])
    if drop_file_id:
        metadata["evidence_records"] = [
            {key: value for key, value in record.items() if key != "file_id"}
            for record in metadata["evidence_records"]
        ]
    envelope = DomainEnvelope(
        envelope_id=f"{pack}-structural", domain_pack_id=_PACK_IDS[pack](),
        extracted_objects=result.payload["curatable_objects"], metadata=metadata,
    )
    pack_metadata = load_alliance_domain_pack_registry().get_pack(_PACK_IDS[pack]())
    return run_domain_envelope_structural_checks(envelope, pack_metadata).appended_findings


@pytest.mark.parametrize("pack", ["gene", "allele", "phenotype", "disease"])
def test_structural_checks_let_a_file_identified_quote_lack_page_and_section(pack, monkeypatch):
    findings = _structural_findings(pack, without_section=True, drop_file_id=False, monkeypatch=monkeypatch)

    assert [
        finding.field_ref.field_path for finding in findings
        if finding.code == "domain_pack.required_field_missing"
        and finding.field_ref.field_path in ("page", "section")
    ] == []


def test_the_gene_page_and_section_finding_without_a_file_never_blocks_submission(monkeypatch):
    # Without a file id the gene pack's required page/section still give their
    # finding (today's rule), and it is an error finding that no readiness gate reads.
    from src.lib.curation_workspace.session_submission_service import _finding_blocks_readiness

    findings = _structural_findings("gene", without_section=True, drop_file_id=True, monkeypatch=monkeypatch)
    missing = {
        finding.field_ref.field_path: finding for finding in findings
        if finding.code == "domain_pack.required_field_missing"
    }

    assert set(missing) == {"page", "section"}
    assert all(finding.severity.value == "error" for finding in missing.values())
    assert not any(_finding_blocks_readiness(finding) for finding in missing.values())
