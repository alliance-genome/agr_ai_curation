"""Unit tests for evidence registry canonicalization helpers."""

from src.lib.openai_agents.evidence_summary import (
    build_record_evidence_summary_record,
    canonicalize_structured_result_payload,
    extract_evidence_records_from_structured_result,
    structured_result_evidence_reference_report,
    structured_result_missing_evidence_record_refs,
    structured_result_requires_evidence,
)
from src.lib.openai_agents.models import (
    AlleleExtractionResultEnvelope,
    GeneExtractionResultEnvelope,
)
from packages.alliance.agents.allele_extractor.schema import (
    AlleleExtractionResultEnvelope as AllianceAlleleExtractionResultEnvelope,
)


def _record(
    *,
    entity: str,
    quote: str = "Shared sentence from the paper.",
    chunk_id: str = "chunk-1",
    page: int = 3,
    section: str = "Results",
) -> dict[str, object]:
    return {
        "entity": entity,
        "verified_quote": quote,
        "page": page,
        "section": section,
        "chunk_id": chunk_id,
    }


def test_canonicalize_keeps_distinct_live_records_when_entities_share_same_locator():
    payload = {
        "items": [
            {
                "label": "Actin 5C",
                "entity_type": "gene",
                "source_mentions": ["Actin 5C"],
                "evidence_record_ids": ["evidence-live-a"],
            },
            {
                "label": "Actin 87E",
                "entity_type": "gene",
                "source_mentions": ["Actin 87E"],
                "evidence_record_ids": ["evidence-live-b"],
            },
        ],
        "evidence_records": [],
        "run_summary": {"kept_count": 2},
    }

    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=[
            {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"},
            {**_record(entity="Actin 87E"), "evidence_record_id": "evidence-live-b"},
        ],
    )

    assert canonical["evidence_records"] == [
        {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"},
        {**_record(entity="Actin 87E"), "evidence_record_id": "evidence-live-b"},
    ]


def test_canonicalize_preserves_structured_evidence_when_preferred_records_empty():
    payload = {
        "items": [
            {
                "label": "vtg1",
                "entity_type": "gene",
                "source_mentions": ["vtg1"],
                "evidence_record_ids": ["EV1"],
            }
        ],
        "evidence_records": [
            {**_record(entity="vtg1"), "evidence_record_id": "EV1"}
        ],
        "run_summary": {"kept_count": 1},
    }

    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=[],
    )

    assert canonical["evidence_records"] == [
        {**_record(entity="vtg1"), "evidence_record_id": "EV1"}
    ]
    assert extract_evidence_records_from_structured_result(canonical) == [
        {**_record(entity="vtg1"), "evidence_record_id": "EV1"}
    ]
    assert structured_result_evidence_reference_report(canonical)["evidence_record_count"] == 1


def test_canonicalize_merges_live_and_payload_evidence_records():
    payload = {
        "items": [
            {
                "label": "Actin 5C",
                "entity_type": "gene",
                "source_mentions": ["Actin 5C"],
                "evidence_record_ids": ["evidence-live-a"],
            },
            {
                "label": "vtg1",
                "entity_type": "gene",
                "source_mentions": ["vtg1"],
                "evidence_record_ids": ["EV1"],
            },
        ],
        "evidence_records": [
            {**_record(entity="vtg1"), "evidence_record_id": "EV1"}
        ],
        "run_summary": {"kept_count": 2},
    }

    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=[
            {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"}
        ],
    )

    assert canonical["evidence_records"] == [
        {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"},
        {**_record(entity="vtg1"), "evidence_record_id": "EV1"},
    ]


def test_canonicalize_remaps_payload_refs_when_live_record_id_is_preferred():
    payload = {
        "items": [
            {
                "label": "vtg1",
                "entity_type": "gene",
                "source_mentions": ["vtg1"],
                "evidence_record_ids": ["EV1"],
            }
        ],
        "evidence_records": [
            {**_record(entity="vtg1"), "evidence_record_id": "EV1"}
        ],
        "run_summary": {"kept_count": 1},
    }

    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=[
            {**_record(entity="vtg1"), "evidence_record_id": "evidence-live-a"}
        ],
    )

    assert canonical["items"][0]["evidence_record_ids"] == ["evidence-live-a"]
    assert canonical["evidence_records"] == [
        {**_record(entity="vtg1"), "evidence_record_id": "evidence-live-a"}
    ]


def test_missing_evidence_record_refs_when_kept_count_positive_and_items_missing():
    assert structured_result_missing_evidence_record_refs(
        {
            "summary": "Retained a finding but dropped items.",
            "evidence_records": [
                {
                    **_record(entity="Actin 5C"),
                    "evidence_record_id": "evidence-live-a",
                }
            ],
            "run_summary": {"kept_count": 1},
        }
    ) is True


def test_schema_defined_retained_collection_satisfies_evidence_guard_without_items():
    payload = {
        "summary": "Retained one focal allele with verified evidence.",
        "curatable_objects": [
            {
                "object_type": "allele",
                "pending_ref_id": "allele-act5c",
                "payload": {
                    "mention": "Actin 5C",
                    "normalized_symbol": "Act5C",
                    "normalized_id": "FB:FBal0000001",
                    "associated_gene": "Act5C",
                    "confidence": "high",
                },
                "evidence_record_ids": ["evidence-live-a"],
            }
        ],
        "metadata": {
            "evidence_records": [
                {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"}
            ]
        },
        "run_summary": {"kept_count": 1},
    }

    assert structured_result_requires_evidence(
        payload,
        expected_output_type=AlleleExtractionResultEnvelope,
    ) is True
    assert structured_result_missing_evidence_record_refs(
        payload,
        expected_output_type=AlleleExtractionResultEnvelope,
    ) is False


def test_schema_defined_auxiliary_lists_do_not_satisfy_retained_evidence_guard():
    payload = {
        "summary": "Kept count drifted positive but only raw mentions survived.",
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "Actin 5C",
                    "entity_type": "allele",
                    "evidence_record_ids": ["evidence-live-a"],
                }
            ],
            "evidence_records": [
                {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"}
            ],
        },
        "run_summary": {"kept_count": 1},
    }

    assert structured_result_missing_evidence_record_refs(
        payload,
        expected_output_type=AlleleExtractionResultEnvelope,
    ) is True


def test_evidence_reference_report_names_retained_items_missing_refs():
    report = structured_result_evidence_reference_report(
        {
            "curatable_objects": [
                {
                    "object_type": "gene",
                    "pending_ref_id": "gene-crb",
                    "payload": {
                        "mention": "crb",
                        "normalized_symbol": "crb",
                        "normalized_id": "FB:FBgn0259685",
                        "species": "Drosophila melanogaster",
                        "confidence": "high",
                    },
                    "evidence_record_ids": ["evidence-live-a"],
                },
                {
                    "object_type": "gene",
                    "pending_ref_id": "gene-ninae",
                    "payload": {
                        "mention": "ninaE",
                        "normalized_symbol": "ninaE",
                        "normalized_id": "FB:FBgn0002940",
                        "species": "Drosophila melanogaster",
                        "confidence": "high",
                        "source_mentions": ["ninaE"],
                    },
                    "evidence_record_ids": [],
                },
            ],
            "metadata": {
                "evidence_records": [
                    {**_record(entity="crb"), "evidence_record_id": "evidence-live-a"}
                ]
            },
            "run_summary": {"kept_count": 2},
        },
        expected_output_type=GeneExtractionResultEnvelope,
    )

    assert report["retained_item_count"] == 2
    assert report["evidence_record_count"] == 1
    assert report["evidence_record_ids"] == ["evidence-live-a"]
    assert report["missing_record_refs"] == [
        {
            "collection": "curatable_objects",
            "index": 1,
            "label": "ninaE",
            "normalized_id": "FB:FBgn0002940",
            "entity_type": "gene",
            "source_mentions": ["ninaE"],
        },
    ]


def test_domain_extraction_output_type_detects_curatable_object_evidence():
    payload = {
        "curatable_objects": [
            {
                "object_type": "gene",
                "pending_ref_id": "gene-crb",
                "payload": {"mention": "crb", "normalized_id": "FB:FBgn0259685"},
                "evidence_record_ids": ["evidence-live-a"],
            }
        ],
        "metadata": {
            "evidence_records": [
                {**_record(entity="crb"), "evidence_record_id": "evidence-live-a"}
            ]
        },
    }

    assert structured_result_requires_evidence(
        payload,
        expected_output_type=GeneExtractionResultEnvelope,
    ) is True
    assert structured_result_missing_evidence_record_refs(
        payload,
        expected_output_type=GeneExtractionResultEnvelope,
    ) is False


def test_record_evidence_summary_uses_resolved_output_chunk_id():
    resolved_chunk_id = "1b3651f8-7745-51a0-80f3-b3eafb70a558"

    record = build_record_evidence_summary_record(
        tool_name="record_evidence",
        tool_input={
            "entity": "Trp53 fl/fl ;Wwox fl/fl",
            "chunk_id": "stale-input-id",
            "claimed_quote": "The strains were bred in the source passage.",
        },
        tool_output={
            "status": "verified",
            "chunk_id": resolved_chunk_id,
            "verified_quote": "The strains were bred in the source passage.",
            "page": 11,
            "section": "Source Passage",
        },
    )

    assert record is not None
    assert record["chunk_id"] == resolved_chunk_id


def test_record_evidence_summary_ignores_terminal_unverified_retry_result():
    record = build_record_evidence_summary_record(
        tool_name="record_evidence",
        tool_input={
            "entity": "LSL-DTA",
            "chunk_id": "b247a1a2-a6fa-2176-46ff-b814431e61c8",
            "claimed_quote": "LSL-DTA (Strain NO. 009669) mice were kindly provided by Dr. Ming O Li.",
        },
        tool_output={
            "status": "not_found",
            "chunk_id": "b247a1a2-a6fa-2176-46ff-b814431e61c8",
            "message": "Exact quote not found in this chunk after repeated attempts.",
            "terminal": True,
            "retry_exhausted": True,
        },
    )

    assert record is None


def test_extract_evidence_records_from_domain_envelope_extraction_metadata():
    envelope = {
        "envelope_id": "gene-expression-envelope",
        "domain_pack_id": "gene_expression",
        "objects": [
            {
                "object_type": "GeneExpressionAnnotation",
                "pending_ref_id": "expression-flcn-brain",
                "payload": {
                    "gene": {"symbol": "flcn"},
                    "anatomy": {"label": "brain"},
                },
                "evidence_record_ids": ["evidence-flcn-brain"],
            }
        ],
        "metadata": {
            "extraction_metadata": {
                "evidence_records": [
                    {
                        **_record(
                            entity="flcn",
                            quote="flcn transcripts were detected in the embryonic brain.",
                            page=6,
                            section="Results",
                            chunk_id="chunk-flcn",
                        ),
                        "evidence_record_id": "evidence-flcn-brain",
                    }
                ]
            }
        },
    }

    assert extract_evidence_records_from_structured_result(envelope) == [
        {
            **_record(
                entity="flcn",
                quote="flcn transcripts were detected in the embryonic brain.",
                page=6,
                section="Results",
                chunk_id="chunk-flcn",
            ),
            "evidence_record_id": "evidence-flcn-brain",
        }
    ]


def test_extract_evidence_records_from_domain_envelope_object_payload():
    envelope = {
        "envelope_id": "gene-envelope",
        "domain_pack_id": "gene",
        "objects": [
            {
                "object_type": "gene_mention_evidence",
                "object_role": "validated_reference",
                "pending_ref_id": "gene-crb",
                "payload": {
                    "mention": "Crumbs",
                    "evidence_record_id": "evidence-crb",
                    "verified_quote": "Crumbs regulates R8 cell fate.",
                    "page": 3,
                    "section": "Results",
                    "chunk_id": "chunk-crb",
                },
                "evidence_record_ids": ["evidence-crb"],
            }
        ],
        "metadata": {},
    }

    assert extract_evidence_records_from_structured_result(envelope) == [
        {
            "entity": "Crumbs",
            "verified_quote": "Crumbs regulates R8 cell fate.",
            "page": 3,
            "section": "Results",
            "chunk_id": "chunk-crb",
            "evidence_record_id": "evidence-crb",
        }
    ]


def test_canonicalize_prunes_unreferenced_live_evidence_from_domain_envelope():
    crb_record = {
        **_record(
            entity="crumbs",
            quote="Crumbs protein acts as a positional cue for rhabdomere.",
            page=13,
            section="Results",
            chunk_id="chunk-crb",
        ),
        "evidence_record_id": "evidence-crb",
    }
    ninae_record = {
        **_record(
            entity="ninaE",
            quote="Decreased levels of Rh1 induced by mutating the ninaE gene.",
            page=14,
            section="Results",
            chunk_id="chunk-ninae",
        ),
        "evidence_record_id": "evidence-ninae",
    }
    opsin_record = {
        **_record(
            entity="Opsin",
            quote="Crumbs abundance was lower than the abundance of Opsin.",
            page=13,
            section="Results",
            chunk_id="chunk-opsin",
        ),
        "evidence_record_id": "evidence-opsin",
    }
    envelope = {
        "envelope_id": "gene-envelope",
        "domain_pack_id": "gene",
        "objects": [
            {
                "object_type": "gene_mention_evidence",
                "pending_ref_id": "gene-crb",
                "payload": {
                    "mention": "crumbs",
                    "evidence_record_id": "evidence-crb",
                },
                "evidence_record_ids": ["evidence-crb"],
            },
            {
                "object_type": "gene_mention_evidence",
                "pending_ref_id": "gene-ninae",
                "payload": {
                    "mention": "ninaE",
                    "evidence_record_id": "evidence-ninae",
                },
                "evidence_record_ids": ["evidence-ninae"],
            },
        ],
        "metadata": {"evidence_records": [crb_record, ninae_record]},
    }

    canonical = canonicalize_structured_result_payload(
        envelope,
        preferred_evidence_records=[crb_record, opsin_record, ninae_record],
    )

    assert [
        record["evidence_record_id"]
        for record in canonical["metadata"]["evidence_records"]
    ] == ["evidence-crb", "evidence-ninae"]
    assert extract_evidence_records_from_structured_result(canonical) == [
        crb_record,
        ninae_record,
    ]


def test_canonicalize_copies_payload_evidence_ids_to_curatable_objects():
    payload = {
        "curatable_objects": [
            {
                "object_type": "EvidenceQuote",
                "object_role": "metadata_only",
                "pending_ref_id": "quote-1",
                "payload": {
                    "evidence_record_id": "evidence-quote-1",
                    "verified_quote": "Notch mutant clones showed smooth eye facets.",
                    "page": 4,
                    "section": "Results",
                    "chunk_id": "chunk-notch",
                },
            },
            {
                "object_type": "Reference",
                "object_role": "validated_reference",
                "pending_ref_id": "reference-1",
                "payload": {"title": "Notch Controls Cell Adhesion in the Drosophila Eye"},
            },
            {
                "object_type": "AllelePaperEvidenceAssociation",
                "object_role": "curatable_unit",
                "pending_ref_id": "association-1",
                "payload": {
                    "association_kind": "allele_paper_evidence",
                    "evidence_record_ids": ["evidence-quote-1"],
                },
                "evidence_record_ids": ["evidence-quote-1"],
            },
        ],
        "metadata": {
            "evidence_records": [
                {
                    **_record(
                        entity="Notch",
                        quote="Notch mutant clones showed smooth eye facets.",
                        page=4,
                        section="Results",
                        chunk_id="chunk-notch",
                    ),
                    "evidence_record_id": "evidence-quote-1",
                }
            ]
        },
        "run_summary": {"kept_count": 1},
    }

    canonical = canonicalize_structured_result_payload(payload)
    report = structured_result_evidence_reference_report(canonical)
    schema_report = structured_result_evidence_reference_report(
        canonical,
        expected_output_type=AllianceAlleleExtractionResultEnvelope,
    )

    assert canonical["curatable_objects"][0]["evidence_record_ids"] == [
        "evidence-quote-1"
    ]
    assert report["missing_record_refs"] == []
    assert schema_report["missing_record_refs"] == []
    assert structured_result_missing_evidence_record_refs(canonical) is False
    assert (
        structured_result_missing_evidence_record_refs(
            canonical,
            expected_output_type=AllianceAlleleExtractionResultEnvelope,
        )
        is False
    )
