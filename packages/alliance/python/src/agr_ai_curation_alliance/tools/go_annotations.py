"""Typed existing-GO annotation lookup backed by the GO Consortium API."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Literal
from urllib.parse import quote

import requests
from agents import function_tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

_GO_API_BASE = "https://api.geneontology.org/api"
_SUPPORTED_IDENTIFIERS: dict[str, re.Pattern[str]] = {
    "FB": re.compile(r"FBgn\d{7}"),
    "HGNC": re.compile(r"\d+"),
    "MGI": re.compile(r"\d+"),
    "RGD": re.compile(r"\d+"),
    "SGD": re.compile(r"S\d{9}"),
    "WB": re.compile(r"WBGene\d{8}"),
    "ZFIN": re.compile(r"ZDB-GENE-\d{6}-\d+"),
}
_CURIE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._:-]*")
_ASPECTS = {
    "molecular_function": "MF",
    "biological_process": "BP",
    "cellular_component": "CC",
}

ExistingGOAnnotationStatus = Literal[
    "ok",
    "not_found",
    "invalid_input",
    "unsupported_identifier",
    "upstream_error",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GOAnnotationRelation(_StrictModel):
    """Typed relation supplied by the source association."""

    id: str
    label: str | None = None


class GOAnnotationProvenance(_StrictModel):
    """Traceable source identity for one returned association."""

    source: Literal["Gene Ontology Consortium API"] = "Gene Ontology Consortium API"
    source_url: str
    source_record_id: str


class ExistingGOAnnotation(_StrictModel):
    """Provider-neutral existing annotation used for curator comparison."""

    gene_product_id: str
    go_id: str
    go_name: str | None = None
    aspect: Literal["MF", "BP", "CC"] | None = None
    evidence_code: str | None = None
    eco_id: str | None = None
    evidence_label: str | None = None
    references: list[str] = Field(default_factory=list)
    relation: GOAnnotationRelation | None = None
    with_from: list[str] = Field(default_factory=list)
    qualifiers: list[str] = Field(default_factory=list)
    negated: bool = False
    providers: list[str] = Field(default_factory=list)
    product_type: str | None = None
    provenance: GOAnnotationProvenance


class ExistingGOAnnotationsResult(_StrictModel):
    """Stable typed result for every existing-annotation lookup outcome."""

    status: ExistingGOAnnotationStatus
    gene_id: str | None = None
    gene_symbol: str | None = None
    annotations: list[ExistingGOAnnotation] = Field(default_factory=list)
    source: Literal["Gene Ontology Consortium API"] = "Gene Ontology Consortium API"
    source_url: str | None = None
    message: str | None = None


def _request_timeout_seconds() -> float:
    raw = os.getenv("GO_ANNOTATIONS_REQUEST_TIMEOUT_SECONDS", "30")
    try:
        return max(0.1, float(raw))
    except ValueError:
        logger.warning(
            "Invalid GO_ANNOTATIONS_REQUEST_TIMEOUT_SECONDS=%r; using 30 seconds",
            raw,
        )
        return 30.0


def _validate_gene_id(gene_id: object) -> ExistingGOAnnotationsResult | str:
    if not isinstance(gene_id, str) or not gene_id or gene_id != gene_id.strip():
        return ExistingGOAnnotationsResult(
            status="invalid_input",
            message="gene_id must be a non-empty CURIE without surrounding whitespace",
        )
    if not _CURIE_PATTERN.fullmatch(gene_id):
        return ExistingGOAnnotationsResult(
            status="invalid_input",
            gene_id=gene_id,
            message="gene_id must be a syntactically valid CURIE",
        )

    prefix, local_id = gene_id.split(":", 1)
    pattern = _SUPPORTED_IDENTIFIERS.get(prefix)
    if pattern is None:
        return ExistingGOAnnotationsResult(
            status="unsupported_identifier",
            gene_id=gene_id,
            message=f"The {prefix} identifier namespace is not supported by this source",
        )
    if not pattern.fullmatch(local_id):
        return ExistingGOAnnotationsResult(
            status="invalid_input",
            gene_id=gene_id,
            message=f"gene_id is not a valid {prefix} gene CURIE",
        )
    return gene_id


def _string_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return list(dict.fromkeys(value))


def _relation(value: object) -> GOAnnotationRelation | None:
    if value is None:
        return None
    if isinstance(value, str):
        return GOAnnotationRelation(id=value)
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        label = value.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("relation.label must be a string")
        return GOAnnotationRelation(id=value["id"], label=label)
    raise ValueError("relation must be a string or an object with an id")


def _normalize_association(
    association: object,
    *,
    source_url: str,
) -> tuple[ExistingGOAnnotation, str | None]:
    if not isinstance(association, dict):
        raise ValueError("association must be an object")
    subject = association.get("subject")
    term = association.get("object")
    if not isinstance(subject, dict) or not isinstance(subject.get("id"), str):
        raise ValueError("association.subject.id is required")
    if not isinstance(term, dict) or not isinstance(term.get("id"), str):
        raise ValueError("association.object.id is required")
    record_id = association.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("association.id is required for provenance")

    categories = _string_list(term.get("category"), field_name="object.category")
    aspect = _ASPECTS.get(categories[0]) if categories else None
    evidence_types = association.get("evidence_types")
    if evidence_types is None:
        evidence_types = []
    if not isinstance(evidence_types, list) or not all(
        isinstance(item, dict) for item in evidence_types
    ):
        raise ValueError("evidence_types must be a list of objects")
    first_evidence = evidence_types[0] if evidence_types else {}
    eco_id = association.get("evidence") or first_evidence.get("id")
    evidence_label = association.get("evidence_label") or first_evidence.get("label")
    if eco_id is not None and not isinstance(eco_id, str):
        raise ValueError("evidence must be a string")
    if evidence_label is not None and not isinstance(evidence_label, str):
        raise ValueError("evidence_label must be a string")

    annotation = ExistingGOAnnotation(
        gene_product_id=subject["id"],
        go_id=term["id"],
        go_name=term.get("label"),
        aspect=aspect,
        evidence_code=association.get("evidence_type"),
        eco_id=eco_id,
        evidence_label=evidence_label,
        references=_string_list(association.get("reference"), field_name="reference"),
        relation=_relation(association.get("relation")),
        with_from=_string_list(
            association.get("evidence_with"), field_name="evidence_with"
        ),
        qualifiers=_string_list(association.get("qualifiers"), field_name="qualifiers"),
        negated=association.get("negated", False),
        providers=_string_list(
            association.get("provided_by"), field_name="provided_by"
        ),
        product_type=association.get("type"),
        provenance=GOAnnotationProvenance(
            source_url=source_url,
            source_record_id=record_id,
        ),
    )
    return annotation, subject.get("label")


def lookup_existing_go_annotations(
    gene_id: object,
    *,
    requester: Callable[..., Any] = requests.get,
) -> ExistingGOAnnotationsResult:
    """Validate, fetch, and normalize existing GO annotations without source fallback."""

    validated = _validate_gene_id(gene_id)
    if isinstance(validated, ExistingGOAnnotationsResult):
        return validated

    source_url = f"{_GO_API_BASE}/bioentity/gene/{quote(validated, safe=':')}/function"
    try:
        response = requester(
            source_url,
            headers={"Accept": "application/json"},
            timeout=_request_timeout_seconds(),
        )
    except requests.RequestException as exc:
        return ExistingGOAnnotationsResult(
            status="upstream_error",
            gene_id=validated,
            source_url=source_url,
            message=f"GO Consortium API request failed: {exc}",
        )

    if response.status_code == 404:
        return ExistingGOAnnotationsResult(
            status="not_found", gene_id=validated, source_url=source_url
        )
    if not 200 <= response.status_code < 300:
        return ExistingGOAnnotationsResult(
            status="upstream_error",
            gene_id=validated,
            source_url=source_url,
            message=f"GO Consortium API returned HTTP {response.status_code}",
        )

    try:
        payload = response.json()
        associations = (
            payload.get("associations") if isinstance(payload, dict) else None
        )
        if not isinstance(associations, list):
            raise ValueError("response must contain an associations list")
        normalized = [
            _normalize_association(item, source_url=source_url) for item in associations
        ]
    except (ValueError, ValidationError) as exc:
        return ExistingGOAnnotationsResult(
            status="upstream_error",
            gene_id=validated,
            source_url=source_url,
            message=f"GO Consortium API returned an invalid annotation contract: {exc}",
        )

    if not normalized:
        return ExistingGOAnnotationsResult(
            status="not_found", gene_id=validated, source_url=source_url
        )
    gene_symbols = {item[1] for item in normalized if item[1]}
    return ExistingGOAnnotationsResult(
        status="ok",
        gene_id=validated,
        gene_symbol=next(iter(gene_symbols)) if len(gene_symbols) == 1 else None,
        annotations=[item[0] for item in normalized],
        source_url=source_url,
    )


@function_tool(
    name_override="go_api_call",
    description_override=(
        "Fetch typed existing GO annotations for one supported Alliance gene CURIE "
        "from the GO Consortium API, preserving evidence and provenance."
    ),
)
def go_api_call(gene_id: str) -> ExistingGOAnnotationsResult:
    """Return typed existing GO annotations for one validated Alliance gene CURIE."""

    return lookup_existing_go_annotations(gene_id)


__all__ = [
    "ExistingGOAnnotation",
    "ExistingGOAnnotationsResult",
    "GOAnnotationProvenance",
    "GOAnnotationRelation",
    "go_api_call",
    "lookup_existing_go_annotations",
]
