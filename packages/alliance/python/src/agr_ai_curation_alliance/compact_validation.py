"""Alliance field projections for runtime-owned validator records.

These mappings translate provider fields; they do not infer species, providers,
scientific identity, or absent annotations. Complete source records are retained
in generic candidate details even when a specialized row exposes fewer fields.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord
from src.schemas.domain_validator import ValidatorCandidate


# Ordered keys are documented provider representations of the same fact.
_IDENTITIES = ("curie", "primary_external_id", "primaryExternalId", "gene_id", "go_id", "chebi_id", "id", "internal_id")
_LABELS = ("symbol", "label", "name", "term_name", "display_name", "title")
_ROWS = {
    "GeneResultEnvelope": ("gene_candidates", "Gene", {"gene_id": _IDENTITIES}),
    "AlleleResultEnvelope": ("allele_candidates", "Allele", {"allele_id": _IDENTITIES}),
    "AgmValidationResult": ("agm_candidates", "AffectedGenomicModel", {"agm_id": _IDENTITIES, "label": _LABELS}),
    "SubjectEntityValidationResult": ("subject_candidates", "Subject", {}),
    "OntologyTermValidationResult": ("ontology_term_candidates", "OntologyTerm", {"curie": _IDENTITIES, "label": _LABELS}),
    "ControlledVocabularyValidationResult": ("controlled_vocabulary_candidates", "VocabularyTerm", {
        "internal_id": ("internal_id", "id"), "term_name": ("term_name", "name"),
    }),
    "DataProviderValidationResult": ("data_provider_candidates", "DataProvider", {
        "taxon_id": ("taxon_id", "taxon"),
    }),
    "GOTermResultEnvelope": ("results", "OntologyTerm", {
        "go_id": ("go_id", "id"), "is_obsolete": ("is_obsolete", "isObsolete"),
    }),
    "GOAnnotationsResult": ("annotations", "GOAnnotation", {}),
    "ReferenceValidationResult": ("candidate_references", "Reference", {}),
    "OrthologsResult": ("orthologs", "Gene", {}),
    "ChemicalValidationResult": (None, "Chemical", {}),
    "DiseaseValidationResult": (None, "Disease", {}),
}


def _first(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return deepcopy(record[key])
    return None


def canonical_record(record: Mapping[str, Any], result_schema: type, *, request=None, record_role=None) -> CanonicalValidatorRecord:
    """Build factual views once; selection and candidate assessment come later."""
    name = result_schema.__name__
    if name not in _ROWS:
        raise ValueError(f"Validator requires a specialized record adapter: {name}")
    row_field, object_type, aliases = _ROWS[name]
    if name == "OrthologsResult" and record_role == "query_gene":
        row_field = None
    raw = deepcopy(dict(record))
    if name == "GOTermResultEnvelope":
        record = _go_record(record)
    elif name == "ChemicalValidationResult":
        # ChEBI search wraps compound facts in Elasticsearch _source;
        # compound/hierarchy endpoints expose chebi_accession directly.
        record = deepcopy(dict(record.get("_source", record)))
        if "chebi_accession" in record:
            record["chebi_id"] = record["chebi_accession"]
        if "_score" in raw:
            record["raw_score"] = raw["_score"]
    elif name == "OrthologsResult" and "geneToGeneOrthologyGenerated" in record:
        record = _ortholog_record(record)
    elif name == "SubjectEntityValidationResult":
        subject_type = normalized_subject_type(request)
        if subject_type is None:
            raise ValueError("A subject record requires an explicit supported subject type")
        object_type = {"gene": "Gene", "allele": "Allele", "agm": "AffectedGenomicModel"}[subject_type]
        record = {**record, "subject_type": subject_type,
                  "subject_identifier": _first(record, _IDENTITIES), "subject_label": _first(record, _LABELS)}
    identity = _first(record, _IDENTITIES)
    if name == "GOAnnotationsResult":
        provenance = record.get("provenance")
        identity = provenance.get("source_record_id") if isinstance(provenance, Mapping) else None
    elif name == "DataProviderValidationResult":
        identity = record.get("abbreviation")
    if identity is None:
        raise ValueError(f"Returned {object_type} record has no authoritative identity")
    values = deepcopy(dict(record))
    rows = {}
    if row_field:
        row_type = result_schema.model_fields[row_field].annotation.__args__[0]
        if hasattr(row_type, "model_fields"):
            row = {}
            for key in row_type.model_fields:
                for source_key in aliases.get(key, (key,)):
                    if source_key in record:
                        # Explicit null is a provider fact, including when the
                        # destination field is required but nullable.
                        row[key] = deepcopy(record[source_key])
                        break
            # Validation supplies only schema-declared empty/unknown defaults.
            # Required missing provider facts remain an explicit error.
            if name != "GOTermResultEnvelope":
                row = row_type.model_validate(row).model_dump(mode="json")
        else:
            row = deepcopy(dict(record))
        rows[row_field] = row
        values.update(row)
    candidate = ValidatorCandidate(
        value=str(identity), label=_first(record, _LABELS), object_type=object_type,
        details={"source_record": raw, **({"record_role": record_role} if record_role else {})},
    )
    return CanonicalValidatorRecord(
        candidate=candidate, values=values, result_rows=rows,
        resolved_object={"object_type": object_type, **deepcopy(values)},
    )


def _go_record(record: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(record))
    definition = result.get("definition")
    if isinstance(definition, Mapping):
        result["definition"] = definition.get("text")
    if isinstance(result.get("synonyms"), list):
        result["synonyms"] = [
            value["name"] if isinstance(value, Mapping) else value
            for value in result["synonyms"]
        ]
    # Preserve ID-only hierarchy records; assembly joins labels from additional
    # lookups and the final schema rejects entries that remain incomplete.
    for relation in ("ancestors", "children"):
        if relation in result:
            rows = []
            for entry in result[relation]:
                if isinstance(entry, Mapping):
                    rows.append({
                        "go_id": _first(entry, ("go_id", "id")),
                        "name": entry.get("name"),
                        "relationship_type": _first(entry, ("relationship_type", "relation")),
                    })
                elif isinstance(entry, str):
                    rows.append({"go_id": entry, "name": None,
                                 "relationship_type": "ancestor" if relation == "ancestors" else "child"})
            result[relation] = rows
    return result


def _ortholog_record(record: Mapping[str, Any]) -> dict[str, Any]:
    relation = record["geneToGeneOrthologyGenerated"]
    gene = relation["objectGene"]
    if not isinstance(gene, Mapping):
        raise ValueError("Orthology relationship lacks an object gene record")
    result = _gene_record(gene)
    for target, source in (("confidence", "confidence"), ("is_best_score", "isBestScore")):
        value = relation.get(source)
        result[target] = value.get("name") if isinstance(value, Mapping) else value
    for target, source in (("methods_matched", "predictionMethodsMatched"),
                           ("methods_not_matched", "predictionMethodsNotMatched")):
        if source in relation:
            result[target] = deepcopy(relation[source])
    return result


def _gene_record(gene: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(gene))
    result["gene_id"] = _first(gene, _IDENTITIES)
    symbol = gene.get("geneSymbol")
    if "symbol" not in result and isinstance(symbol, Mapping):
        result["symbol"] = symbol.get("displayText")
    # Do not infer organism or provider from identifiers or query context.
    return result


def normalized_subject_type(request) -> str | None:
    if request is None:
        return None
    value = request.selected_inputs.get("subject_type", request.target.input_values.get("subject_type"))
    if not isinstance(value, str):
        return None
    return {
        "gene": "gene", "Gene": "gene", "GENE": "gene",
        "allele": "allele", "Allele": "allele", "ALLELE": "allele",
        "agm": "agm", "AGM": "agm", "affected_genomic_model": "agm", "Affected Genomic Model": "agm",
    }.get(value)


def source_records(tool_name: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract explicit provider collections without flattening scientific subrecords."""
    return [record for _path, record in source_record_entries(tool_name, payload)]


def source_record_entries(tool_name: str, payload: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Keep exact response locations so duplicate identities remain distinguishable."""
    def collection(values, path):
        return [(f"{path}/{index}", row) for index, row in enumerate(_record_list(values, path))]

    if tool_name == "agr_literature_reference_lookup":
        return collection(payload.get("candidate_references"), "/candidate_references")
    if tool_name == "go_api_call":
        return collection(payload.get("annotations"), "/annotations")
    data = payload.get("data")
    if isinstance(data, list):
        return collection(data, "/data")
    if data is None:
        return []
    if not isinstance(data, dict):
        raise ValueError("Lookup data is not a record or a record collection")
    if isinstance(data.get("items"), list):
        rows = []
        for index, item in enumerate(data["items"]):
            if not isinstance(item, dict) or "results" not in item:
                raise ValueError("Bulk lookup group lacks its result collection")
            rows.extend(collection(item["results"], f"/data/items/{index}/results"))
        return rows
    for name in ("results", "candidates", "matches", "orthologs"):
        if isinstance(data.get(name), list):
            return collection(data[name], f"/data/{name}")
    if any(key in data for key in (*_IDENTITIES, "abbreviation", "chebi_accession")):
        return [("/data", deepcopy(data))]
    if not data:
        return []
    raise ValueError("Lookup response has no supported authoritative record collection")


def _record_list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Lookup {field} must be a list of record objects")
    return deepcopy(value)
