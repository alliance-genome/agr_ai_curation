"""Synthetic semantic chat-output fixture (ALL-1275 / ALL-1276).

Seven expression statements shaped like the Sep 22 production failure: one row
per distinct statement, curator columns Gene / Anatomy Terms / Life Stages /
Cellular Components / Method / PMID, resolved and unresolved paper-grounded
values mixed inside the same statement, missing values rendered as an em dash,
and reagent information present in the data but excluded from the output.

Each statement also carries nested validation diagnostics (lookup attempts and
candidate matches) large enough that serializing the bundle into model
instructions reproduces the original >1,422,809 character provider rejection.
All identifiers, labels and diagnostics are synthetic.

Consumers:
- ``build_semantic_output_step()`` returns a completed flow step whose
  canonical candidate payload builds a ``FlowOutputArtifactBundle``.
- ``build_semantic_output_bundle()`` builds that bundle.
- ``SEMANTIC_CHAT_PLAN`` is a projection plan (no row arrays) that renders the
  expected table; ``EXPECTED_HEADERS`` / ``EXPECTED_ROWS`` are the exact cells.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.lib.flows.output_projection import (
    FlowOutputArtifactBundle,
    build_flow_output_artifact_bundle,
)

EM_DASH = "—"
PMID = "PMID:39000001"
ORIGINAL_INSTRUCTIONS_CHARS = 1_422_809
PROVIDER_INSTRUCTIONS_LIMIT = 1_048_576
DEFAULT_MIN_DIAGNOSTIC_CHARS = 1_450_000
CURATOR_REQUEST = (
    "Show one row per distinct expression statement with the columns Gene, "
    "Anatomy Terms, Life Stages, Cellular Components, Method and PMID. Keep "
    "supplied labels and IDs, mark unresolved proposals, use an em dash for "
    "missing values, omit reagent information, and add a brief note about "
    "unresolved validation."
)
UNRESOLVED_CAVEAT = (
    "Values shown as UNRESOLVED are paper-grounded proposals that "
    "validation could not resolve to an ontology term; review them before submission."
)

# (label, id, status); status "unresolved_proposal" keeps a paper label with no ID.
_STATEMENTS: list[dict[str, Any]] = [
    {
        "gene": ("unc-54", "WB:WBGene90000001"),
        "anatomy": [("body wall musculature", "WBbt:9000001", "resolved"),
                    ("vulval muscle", None, "unresolved_proposal")],
        "stages": [("adult hermaphrodite", "WBls:9000001", "resolved")],
        "components": [],
        "method": ("reporter gene fusion", "MMO:9000001"),
        "reagent": "Pmyo-3::GFP transgene",
    },
    {
        "gene": ("myo-2", "WB:WBGene90000002"),
        "anatomy": [("pharyngeal muscle cell", "WBbt:9000002", "resolved")],
        "stages": [("L1 larva", "WBls:9000002", "resolved"),
                   ("dauer-like arrest", None, "unresolved_proposal")],
        "components": [("sarcomere", "GO:9000001", "resolved")],
        "method": ("in situ hybridization", "MMO:9000002"),
        "reagent": "digoxigenin probe set 7",
    },
    {
        "gene": ("egl-1", "WB:WBGene90000003"),
        "anatomy": [("HSN neuron", "WBbt:9000003", "resolved"),
                    ("tail ganglion cell é", None, "unresolved_proposal")],
        "stages": [],
        "components": [("nucleus", "GO:9000002", "resolved")],
        "method": ("antibody staining", "MMO:9000003"),
        "reagent": "anti-EGL-1 rabbit polyclonal",
    },
    {
        "gene": ("hlh-1", "WB:WBGene90000004"),
        "anatomy": [("body wall muscle cell", "WBbt:9000004", "resolved")],
        "stages": [("embryo ≥ 200-cell", "WBls:9000003", "resolved")],
        "components": [],
        "method": ("reporter gene fusion", "MMO:9000001"),
        "reagent": "hlh-1p::mCherry",
    },
    {
        "gene": ("lin-3", "WB:WBGene90000005"),
        "anatomy": [("anchor cell", "WBbt:9000005", "resolved"),
                    ("uterine seam cell", None, "unresolved_proposal"),
                    ("vulval precursor cell", "WBbt:9000006", "resolved")],
        "stages": [("L3 larva", "WBls:9000004", "resolved")],
        "components": [("plasma membrane", None, "unresolved_proposal")],
        "method": ("RNA-seq", "MMO:9000004"),
        "reagent": "strain PS1234",
    },
    {
        "gene": ("daf-16", "WB:WBGene90000006"),
        "anatomy": [("intestine", "WBbt:9000007", "resolved")],
        "stages": [("adult hermaphrodite", "WBls:9000001", "resolved"),
                   ("post-dauer adult", None, "unresolved_proposal")],
        "components": [("cytoplasm", "GO:9000003", "resolved"),
                       ("nucleus", "GO:9000002", "resolved")],
        "method": ("antibody staining", "MMO:9000003"),
        "reagent": "anti-DAF-16 monoclonal",
    },
    {
        "gene": ("ceh-36", "WB:WBGene90000007"),
        "anatomy": [("AWC neuron", "WBbt:9000008", "resolved")],
        "stages": [],
        "components": [],
        "method": ("single-molecule FISH", "MMO:9000005"),
        "reagent": "smFISH probe pool 3",
    },
]


def _render_values(values: list[tuple[str, str | None, str]]) -> str:
    # ALL-1283: the application reads a resolved value "label (ID)" and an
    # unresolved one UNRESOLVED; the paper wording is never in the cell.
    if not values:
        return EM_DASH
    return "; ".join(
        "UNRESOLVED" if status == "unresolved_proposal" else f"{label} ({curie})"
        for label, curie, status in values
    )


EXPECTED_HEADERS = ["Gene", "Anatomy Terms", "Life Stages", "Cellular Components", "Method", "PMID"]
EXPECTED_ROWS: list[dict[str, str]] = [
    {
        "Gene": f"{statement['gene'][0]} ({statement['gene'][1]})",
        "Anatomy Terms": _render_values(statement["anatomy"]),
        "Life Stages": _render_values(statement["stages"]),
        "Cellular Components": _render_values(statement["components"]),
        "Method": f"{statement['method'][0]} ({statement['method'][1]})",
        "PMID": PMID,
    }
    for statement in _STATEMENTS
]
EXCLUDED_REAGENT_VALUES = [statement["reagent"] for statement in _STATEMENTS]


def _element_transform(prefix: str) -> dict[str, Any]:
    return {"type": "join_list", "field_ref": f"object.attribute.{prefix}_terms", "separator": "; "}


SEMANTIC_CHAT_PLAN: dict[str, Any] = {
    "format": "chat",
    "row_source": "object",
    "missing_value": EM_DASH,
    "columns": [
        {
            "key": "gene",
            "header": "Gene",
            "transform": {
                "type": "format_elements",
                "field_refs": ["object.attribute.gene_symbol", "object.attribute.gene_id"],
                "default": "{1} ({2})",
            },
        },
        {"key": "anatomy_terms", "header": "Anatomy Terms", "transform": _element_transform("anatomy_term")},
        {"key": "life_stages", "header": "Life Stages", "transform": _element_transform("life_stage")},
        {
            "key": "cellular_components",
            "header": "Cellular Components",
            "transform": _element_transform("cellular_component"),
        },
        {
            "key": "method",
            "header": "Method",
            "transform": {
                "type": "format_elements",
                "field_refs": ["object.attribute.method_label", "object.attribute.method_id"],
                "default": "{1} ({2})",
            },
        },
        {"key": "pmid", "header": "PMID", "field_ref": "object.attribute.pmid"},
    ],
}


def _attributes(statement: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "gene_symbol": statement["gene"][0],
        "gene_id": statement["gene"][1],
        "method_label": statement["method"][0],
        "method_id": statement["method"][1],
        "pmid": PMID,
        "reagent": statement["reagent"],
    }
    for prefix, key in (
        ("anatomy_term", "anatomy"),
        ("life_stage", "stages"),
        ("cellular_component", "components"),
    ):
        # Each term is a resolvable value (ALL-1283): the paper wording plus the
        # validated identity and its state.
        attributes[f"{prefix}_terms"] = [
            {"mention": label, "name": label, "curie": curie, "resolution_state": "resolved",
             "lookup_outcome": "matched"}
            if status == "resolved"
            else {"mention": label, "name": None, "curie": None, "resolution_state": "unresolved",
                  "lookup_outcome": "not_found"}
            for label, curie, status in statement[key]
        ]
    return attributes


def _diagnostic_findings(
    object_id: str,
    statement: dict[str, Any],
    *,
    per_object_chars: int,
) -> list[dict[str, Any]]:
    """Validator findings whose nested diagnostics dominate the payload size."""

    findings: list[dict[str, Any]] = []
    values = [*statement["anatomy"], *statement["stages"], *statement["components"]]
    size = 0
    index = 0
    while size < per_object_chars:
        label, curie, status = values[index % len(values)]
        resolved = status == "resolved"
        candidates = [
            {
                "curie": f"WBbt:{8000000 + index * 40 + rank:07d}",
                "label": f"{label} candidate {rank} ({'close' if rank < 3 else 'distant'} lexical match)",
                "score": round(1.0 - rank * 0.02, 3),
                "synonyms": [f"{label} synonym {n}" for n in range(4)],
                "definition": f"Synthetic ontology definition text for {label} candidate {rank}. " * 3,
            }
            for rank in range(24)
        ]
        attempts = [
            {
                "method": method,
                "query": label,
                "lookup_status": "resolved" if resolved and method == "exact_label" else "ambiguous",
                "candidate_count": len(candidates),
                "request_payload": {"term": label, "ontology": "WBbt", "limit": 50, "attempt": attempt},
            }
            for attempt, method in enumerate(("exact_label", "synonym", "fuzzy_label", "lexical_expansion"))
        ]
        finding = {
            "finding_id": f"{object_id}-finding-{index}",
            "status": "resolved" if resolved else "open",
            "severity": "info" if resolved else "warning",
            "code": (
                "domain_pack.validator_resolved" if resolved else "domain_pack.validator_unresolved"
            ),
            "message": (
                f"Resolved '{label}' to {curie}." if resolved
                else f"Could not resolve '{label}' to one ontology term; kept as a paper-grounded proposal."
            ),
            "field_path": f"attributes.term[{index}]",
            "object_ref": {"object_id": object_id},
            "details": {
                "candidate_matches": candidates,
                "lookup_attempts": attempts,
                "validation_result": {
                    "request_id": f"{object_id}-request-{index}",
                    "target": {"object_id": object_id, "field_path": f"attributes.term[{index}]", "label": label},
                    "candidate_count": len(candidates),
                    "resolved_values": [curie] if resolved else [],
                },
            },
        }
        findings.append(finding)
        size += len(repr(finding))
        index += 1
    return findings


def build_semantic_output_step(
    *,
    min_diagnostic_chars: int = DEFAULT_MIN_DIAGNOSTIC_CHARS,
    node_id: str = "extractor",
    step_number: int = 1,
) -> dict[str, Any]:
    """Return one completed extraction step carrying all seven statements."""

    per_object_chars = max(1, min_diagnostic_chars // len(_STATEMENTS))
    objects = []
    for index, statement in enumerate(_STATEMENTS, start=1):
        object_id = f"expression-statement-{index}"
        objects.append(
            {
                "object_type": "generic_object",
                "object_id": object_id,
                "status": "needs_review",
                "payload": {
                    "class_key": "generic:expression_statement",
                    "label": f"{statement['gene'][0]} expression statement {index}",
                    "semantic_class": "expression_statement",
                    "attributes": _attributes(statement),
                },
                "evidence_record_ids": [f"evidence-{index}"],
                "validation_findings": _diagnostic_findings(
                    object_id, statement, per_object_chars=per_object_chars
                ),
            }
        )
    return {
        "step": step_number,
        "node_id": node_id,
        "extraction_result_id": "semantic-extraction-1",
        "agent_id": "pdf_extraction",
        "agent_name": "Expression Statement Extractor",
        "output_preview": "Extracted seven expression statements.",
        "candidate": SimpleNamespace(
            agent_key="pdf_extraction",
            adapter_key="generic",
            candidate_count=len(objects),
            conversation_summary="Extracted seven expression statements.",
            payload_json={
                "domain_pack_id": "generic",
                "envelope_id": "semantic-envelope-1",
                "extracted_objects": objects,
            },
        ),
    }


def build_semantic_output_bundle(
    *,
    min_diagnostic_chars: int = DEFAULT_MIN_DIAGNOSTIC_CHARS,
    output_format: str = "csv",
) -> FlowOutputArtifactBundle:
    """Build the flow output bundle for the synthetic semantic fixture."""

    return build_flow_output_artifact_bundle(
        completed_steps=[build_semantic_output_step(min_diagnostic_chars=min_diagnostic_chars)],
        flow_name="Synthetic expression flow",
        flow_run_id="semantic-flow-run",
        document_id="semantic-document",
        output_format=output_format,  # type: ignore[arg-type]
    )
