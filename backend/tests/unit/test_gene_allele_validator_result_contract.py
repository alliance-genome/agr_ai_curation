"""Gene and allele validator contract coverage for shared result outputs."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.lib.config import schema_discovery
from src.schemas.domain_validator import DomainValidatorResultBase


REPO_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_AGENTS_PATH = REPO_ROOT / "packages" / "alliance" / "agents"
REQUIRED_SHARED_FIELDS = {
    "status",
    "request_id",
    "validator_binding_id",
    "validator_agent",
    "target",
    "resolved_values",
    "resolved_objects",
    "missing_expected_fields",
    "candidates",
    "lookup_attempts",
    "curator_message",
    "explanation",
}
CHAT_ERA_FIELDS = {
    "findings",
    "gene_curies",
    "allele_curies",
    "results",
    "query_summary",
    "not_found",
}


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema_discovery.reset_cache()
    yield
    schema_discovery.reset_cache()


def _schemas() -> dict[str, type]:
    return schema_discovery.discover_agent_schemas(
        ALLIANCE_AGENTS_PATH, force_reload=True
    )


def _base_payload(status: str = "resolved") -> dict[str, object]:
    return {
        "status": status,
        "request_id": "validator-request:gene-1",
        "validator_binding_id": "gene.primary_id.lookup",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "gene_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.gene",
            "object_type": "gene_mention_evidence",
            "object_id": "gene-mention-1",
            "field_path": "primary_external_id",
            "expected_fields": ["primary_external_id", "symbol"],
            "input_values": {"symbol": "daf-16", "species": "Caenorhabditis elegans"},
        },
        "resolved_values": {
            "primary_external_id": "WB:WBGene00000912",
            "symbol": "daf-16",
        },
        "resolved_objects": [],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [],
        "curator_message": "Gene daf-16 resolved to WB:WBGene00000912.",
        "explanation": "The lookup returned one exact WB match for daf-16.",
    }


def test_gene_and_allele_validator_schemas_expose_shared_root_fields():
    schemas = _schemas()

    expected = {
        "GeneResultEnvelope": "gene_candidates",
        "AlleleResultEnvelope": "allele_candidates",
    }
    for schema_name, domain_candidate_field in expected.items():
        schema = schemas[schema_name]
        field_names = set(schema.model_fields)

        assert issubclass(schema, DomainValidatorResultBase)
        assert REQUIRED_SHARED_FIELDS.issubset(field_names)
        assert domain_candidate_field in field_names
        assert CHAT_ERA_FIELDS.isdisjoint(field_names)


def test_gene_validator_accepts_resolved_shared_contract_payload():
    schema = _schemas()["GeneResultEnvelope"]
    payload = _base_payload("resolved")
    payload["resolved_objects"] = [
        {
            "gene_id": "WB:WBGene00000912",
            "symbol": "daf-16",
            "species": "Caenorhabditis elegans",
            "data_provider": "WB",
        }
    ]
    payload["candidates"] = [
        {
            "value": "WB:WBGene00000912",
            "label": "daf-16",
            "object_type": "gene",
            "score": 1.0,
            "matched_fields": {"symbol": "daf-16"},
            "details": {"data_provider": "WB"},
        }
    ]
    payload["lookup_attempts"] = [
        {
            "provider": "agr_curation_query",
            "method": "search_genes",
            "query": {"gene_symbol": "daf-16", "data_provider": "WB"},
            "result_count": 1,
            "outcome": "success",
            "message": "Exact symbol match.",
        }
    ]
    payload["gene_candidates"] = [
        {
            "gene_id": "WB:WBGene00000912",
            "symbol": "daf-16",
            "species": "Caenorhabditis elegans",
            "data_provider": "WB",
            "name": "abnormal dauer formation-16",
            "gene_type": "protein_coding",
            "cross_references": [{"prefix": "WB", "id": "WBGene00000912"}],
            "synonyms": ["daf16"],
            "match_type": "exact",
        }
    ]

    result = schema.model_validate(payload)

    assert result.status == "resolved"
    assert result.lookup_attempts[0].outcome == "success"
    assert result.candidates[0].value == "WB:WBGene00000912"
    assert result.missing_expected_fields == []
    assert result.curator_message == "Gene daf-16 resolved to WB:WBGene00000912."
    assert result.gene_candidates[0].symbol == "daf-16"


def test_allele_validator_accepts_unresolved_payload_with_missing_fields_and_candidates():
    schema = _schemas()["AlleleResultEnvelope"]
    payload = _base_payload("unresolved")
    payload.update(
        {
            "request_id": "validator-request:allele-1",
            "validator_binding_id": "allele.primary_id.lookup",
            "validator_agent": {
                "package_id": "agr.alliance",
                "agent_id": "allele_validation",
            },
            "target": {
                "domain_pack_id": "agr.alliance.allele",
                "object_type": "allele_mention_evidence",
                "field_path": "allele_identifier",
                "expected_fields": ["allele_identifier", "symbol"],
                "input_values": {"symbol": "Ulk1<tm1Thsn>", "species": "Mus musculus"},
            },
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": ["allele_identifier", "symbol"],
            "curator_message": (
                "Allele lookup returned multiple Ulk1 candidates; "
                "no exact identity was selected."
            ),
            "explanation": "The validator preserved database candidates instead of guessing.",
        }
    )
    payload["lookup_attempts"] = [
        {
            "provider": "agr_curation_query",
            "method": "search_alleles",
            "query": {"allele_symbol": "Ulk1", "data_provider": "MGI"},
            "result_count": 2,
            "outcome": "ambiguous",
            "message": "Multiple Ulk1 alleles matched.",
        }
    ]
    payload["candidates"] = [
        {
            "value": "MGI:5579670",
            "label": "Ulk1<sup>tm1Thsn</sup>",
            "object_type": "allele",
            "matched_fields": {"associated_gene": "Ulk1"},
            "details": {"data_provider": "MGI"},
        }
    ]
    payload["allele_candidates"] = [
        {
            "allele_id": "MGI:5579670",
            "symbol": "Ulk1<sup>tm1Thsn</sup>",
            "species": "Mus musculus",
            "data_provider": "MGI",
            "associated_gene": "Ulk1",
            "is_obsolete": False,
            "is_extinct": False,
            "fullname_attribution": {
                "value": "Test Lab",
                "confidence": "probable",
                "source": "fullname_suffix",
            },
            "match_type": "contains",
        }
    ]

    result = schema.model_validate(payload)

    assert result.status == "unresolved"
    assert result.missing_expected_fields == ["allele_identifier", "symbol"]
    assert result.lookup_attempts[0].result_count == 2
    assert result.candidates[0].object_type == "allele"
    assert result.curator_message.startswith("Allele lookup returned multiple")
    assert result.allele_candidates[0].associated_gene == "Ulk1"


def test_gene_and_allele_schemas_reject_chat_era_summary_fields():
    for schema_name, old_field in [
        ("GeneResultEnvelope", "results"),
        ("AlleleResultEnvelope", "not_found"),
    ]:
        schema = _schemas()[schema_name]
        payload = _base_payload("resolved")
        payload[old_field] = []

        with pytest.raises(ValidationError):
            schema.model_validate(payload)


def test_gene_and_allele_prompts_describe_shared_validator_policy():
    prompt_paths = [
        ALLIANCE_AGENTS_PATH / "gene" / "prompt.yaml",
        ALLIANCE_AGENTS_PATH / "allele" / "prompt.yaml",
    ]

    for prompt_path in prompt_paths:
        prompt = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))["content"]

        assert '`status: "resolved"`' in prompt
        assert '`status: "unresolved"`' in prompt
        assert "`agr_curation_query`" in prompt
        for field_name in REQUIRED_SHARED_FIELDS - {"status"}:
            assert f"`{field_name}`" in prompt, f"{prompt_path} missing {field_name}"
        forbidden_fragments = [
            "under_development",
            "mark_under_development",
            "repair_action",
            "extractor_patch",
        ]
        for forbidden in forbidden_fragments:
            assert forbidden not in prompt


def test_gene_prompt_requires_shared_bulk_lookup_for_batch_requests():
    prompt = yaml.safe_load(
        (ALLIANCE_AGENTS_PATH / "gene" / "prompt.yaml").read_text(encoding="utf-8")
    )["content"]

    required_fragments = [
        'mode: "domain_validator_batch"',
        "group compatible requests",
        'method: "search_genes_bulk"',
        "gene_symbols: [...]",
        "Do not call",
        "separately for each request",
        "Map each returned bulk item back to the matching request",
    ]
    for fragment in required_fragments:
        assert fragment in prompt


def test_gene_prompt_uses_extractor_handoff_context_for_disambiguation():
    prompt = yaml.safe_load(
        (ALLIANCE_AGENTS_PATH / "gene" / "prompt.yaml").read_text(encoding="utf-8")
    )["content"]

    required_fragments = [
        "`identity_resolution_notes`",
        "you do not",
        "request-level `evidence` records",
        "more specific paper-supported search phrase",
        "paper context for focused follow-up lookups",
    ]
    for fragment in required_fragments:
        assert fragment in prompt

    for paper_specific_fragment in ("Actin 5C", "Opsin-1", "Crumbs (Crb)"):
        assert paper_specific_fragment not in prompt


def test_allele_prompt_keeps_evidence_quotes_out_of_symbol_queries():
    prompt = yaml.safe_load(
        (ALLIANCE_AGENTS_PATH / "allele" / "prompt.yaml").read_text(encoding="utf-8")
    )["content"]

    required_fragments = [
        "do not rewrite or normalize it before the first lookup",
        "Never pass a whole evidence sentence",
        "Evidence text is context for judging candidates",
        "Keep supporting evidence quotes out of the `allele_symbol` argument",
        "Do not use a full sentence or surrounding prose as the search string",
    ]
    for fragment in required_fragments:
        assert fragment in prompt

    forbidden_fragments = [
        "`N fa-g` -> search `N[fa-g]`",
        "after stripping genotype notation",
        "Automatically tries original",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in prompt
