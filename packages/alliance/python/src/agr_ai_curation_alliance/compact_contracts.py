"""Scientific extensions and deterministic parent fields for Alliance validators."""

from copy import deepcopy
from typing import Any, Mapping

from pydantic import Field, StrictStr, create_model

from src.lib.domain_packs.compact_decisions import CompactValidatorDecision, DecisionContract
from src.schemas.domain_validator import DomainValidatorBaseModel


_SCIENTIFIC = {
    "AgmValidationResult": ("unresolved_explanations",),
    "DataProviderValidationResult": ("mismatch_explanations",),
    "GOTermResultEnvelope": ("query_summary",),
}

_SLOT_FIELDS = {
    "GeneResultEnvelope": {
        "gene_id": ("gene_id", "curie", "primary_external_id"),
        "primary_external_id": ("primary_external_id", "curie", "gene_id"),
        "gene_symbol": ("symbol",),
    },
    "AlleleResultEnvelope": {
        "allele_id": ("allele_id", "curie", "primary_external_id"),
        "primary_external_id": ("primary_external_id", "curie", "allele_id"),
        "allele_symbol": ("symbol",),
    },
    "AgmValidationResult": {"agm_id": ("agm_id", "curie", "primary_external_id")},
    "OntologyTermValidationResult": {"term_curie": ("curie",), "term_name": ("name", "label")},
    "GOTermResultEnvelope": {"go_id": ("go_id", "id")},
    "ChemicalValidationResult": {"chebi_id": ("chebi_id", "id", "curie")},
    "DiseaseValidationResult": {
        "disease_id": ("curie", "id"), "label": ("label", "name"),
        "ontology_term_type": ("ontology_term_type", "ontology_type"),
    },
}


def simple_decision_contract(request, result_schema, *, profile_mapped=False, scientific_slots=None):
    """Reuse existing scientific field types; factual parent fields never come from the model."""
    from .compact_validation import _ROWS

    name = result_schema.__name__
    if name not in _ROWS:
        raise ValueError(f"Validator requires a specialized decision contract: {name}")
    fields = _SCIENTIFIC.get(name, ())
    decision_schema = CompactValidatorDecision
    if fields:
        scientific_fields: dict[str, Any] = {field: (result_schema.model_fields[field].annotation,
                                     deepcopy(result_schema.model_fields[field])) for field in fields}
        if name == "GOTermResultEnvelope":
            scientific_fields["not_found_inputs"] = (list[StrictStr], Field(default_factory=list,
                description="JSON pointers into selected_inputs for inputs left unresolved by lookup; do not copy their values."))
        science = create_model(
            name + "ScientificJudgment", __base__=DomainValidatorBaseModel,
            **scientific_fields,
        )
        decision_schema = create_model(name + "CompactDecision", __base__=CompactValidatorDecision,
                                       scientific=(science, ...))

    def assemble_domain(payload, decision, workspace):
        additions = decision.scientific.model_dump() if fields else {}
        selected = [workspace.record(request.request_id, item.record_ref)
                    for item in decision.candidates if item.disposition == "selected"]
        sources = workspace.source_payloads(request.request_id)
        attempts = workspace.lookup_attempts(request.request_id)
        if decision.status == "resolved":
            if not attempts:
                raise ValueError("Resolved lookup decisions require an actual lookup")
            if not selected:
                confirmed_empty_collection = (
                    name in {"GOAnnotationsResult", "OrthologsResult"}
                    and any(attempt.result_count == 0 and attempt.outcome in {"success", "not_found"}
                            for attempt in attempts)
                )
                if not confirmed_empty_collection:
                    raise ValueError("Resolved identity decisions require a selected authoritative record")
        if name == "OntologyTermValidationResult" and "terms" in request.expected_result_fields:
            values = deepcopy(payload["resolved_values"])
            if selected:
                values["terms"] = [{"curie": record.candidate.value,
                                    "name": record.values.get("name")} for record in selected]
            additions["resolved_values"] = values
        if name == "ReferenceValidationResult":
            if len(selected) > 1 and decision.status == "resolved":
                raise ValueError("A reference decision must select exactly one reference")
            if len(selected) == 1:
                record = selected[0].values
                for field in ("reference_id", "curie", "title", "short_citation", "cross_references", "source", "match_type"):
                    if field in record:
                        additions[field] = deepcopy(record[field])
                additions["confidence"] = next(item.score for item in decision.candidates if item.disposition == "selected")
            # Ambiguity/no-match/failure diagnostics describe a concrete source
            # response, not a fact to infer from the number of retained rows.
            if len(sources) == 1:
                source = next(iter(sources.values()))
                for field in ("ambiguity", "no_match", "failure_classification"):
                    if field in source:
                        additions[field] = deepcopy(source[field])
        elif name == "GOTermResultEnvelope":
            additions["not_found"] = [_input_value(request.selected_inputs, path)
                                      for path in additions.pop("not_found_inputs")]
            available = workspace.records(request.request_id)
            completed = []
            for selected_record in selected:
                row = deepcopy(selected_record.result_rows["results"])
                for supplement in available:
                    if supplement.candidate.value != selected_record.candidate.value:
                        continue
                    extra = supplement.result_rows.get("results", {})
                    for key, value in extra.items():
                        if row.get(key) is None or row.get(key) == []:
                            row[key] = deepcopy(value)
                for collection in ("children", "ancestors"):
                    for entry in row.get(collection, []):
                        if entry.get("name") is None:
                            labels = [record.candidate.label for record in available
                                      if record.candidate.value == entry["go_id"] and record.candidate.label is not None]
                            if labels and all(label == labels[0] for label in labels):
                                entry["name"] = labels[0]
                completed.append(row)
            additions["results"] = completed
        elif name == "GOAnnotationsResult":
            for field in ("gene_id", "gene_symbol", "source", "source_url"):
                values = [source[field] for source in sources.values() if source.get(field) is not None]
                if values:
                    if any(value != values[0] for value in values):
                        raise ValueError(f"Annotation lookup responses disagree on {field}")
                    additions[field] = deepcopy(values[0])
        elif name == "OrthologsResult":
            rows = payload.get("orthologs", [])
            additions["high_confidence_count"] = sum(row.get("confidence") == "high" for row in rows)
            additions["species_represented"] = list(dict.fromkeys(
                row["species"] for row in rows if isinstance(row.get("species"), str)
            ))
            query_genes = []
            for record in workspace.records(request.request_id):
                if record.candidate.details.get("record_role") == "query_gene":
                    query_genes.append(record.candidate.details["source_record"])
            for record in selected:
                raw = record.candidate.details.get("source_record", {})
                relation = raw.get("geneToGeneOrthologyGenerated", {})
                if isinstance(relation.get("subjectGene"), Mapping):
                    query_genes.append(relation["subjectGene"])
            if query_genes:
                from .compact_validation import _gene_record
                normalized = [_gene_record(gene) for gene in query_genes]
                if any(gene.get("gene_id") != normalized[0].get("gene_id") for gene in normalized):
                    raise ValueError("Orthology lookup responses disagree on the query gene")
                combined = {}
                for gene in reversed(normalized):
                    combined.update({key: value for key, value in gene.items() if value is not None})
                additions["query_gene"] = combined
            else:
                # A confirmed empty relationship set still has a factual query
                # identifier. This describes the query, not a verified gene record.
                from urllib.parse import unquote, urlsplit
                identifiers = []
                for attempt in attempts:
                    path = unquote(urlsplit(str(attempt.query.get("url", ""))).path).rstrip("/")
                    prefix = "/api/gene/"
                    if path.startswith(prefix) and path.endswith("/orthologs"):
                        identifiers.append(path[len(prefix):-len("/orthologs")])
                if identifiers and all(value == identifiers[0] for value in identifiers):
                    additions["query_gene"] = {"gene_id": identifiers[0]}
        return additions

    return DecisionContract(
        request=request, result_schema=result_schema, profile_mapped=profile_mapped,
        scientific_slots=scientific_slots or {}, decision_schema=decision_schema,
        record_slot_fields=_SLOT_FIELDS.get(name, {}),
        selected_result_fields=frozenset({"results", "annotations", "orthologs"}),
        domain_contract=({"not_found_inputs":
            "GO not_found_inputs are JSON pointers into that request's selected_inputs, not copied terms."}
            if name == "GOTermResultEnvelope" else {}),
        assemble_domain=assemble_domain,
    )


def _input_value(inputs, pointer):
    if not pointer.startswith("/"):
        raise ValueError("Unresolved input must use a selected_inputs JSON pointer")
    value = inputs
    try:
        for part in pointer[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Unresolved input reference is outside selected_inputs") from exc
    if not isinstance(value, str):
        raise ValueError("Unresolved input reference must identify a string")
    return value
