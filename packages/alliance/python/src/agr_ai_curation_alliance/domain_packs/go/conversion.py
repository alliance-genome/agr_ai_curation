"""Materialize RGD GO paper-curation builder state into canonical output."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from src.lib.domain_packs.resolvable_values import (
    ResolvableValueError,
    check_resolvable_list,
    check_resolvable_value,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DefinitionState,
)
from src.schemas.models.base import EvidenceRecord
from src.schemas.evidence_workspace import normalize_workspace_records
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

from src.lib.domain_packs.resolvable_values import (
    LOOKUP_OUTCOME_KEY,
    MENTION_KEY,
    OUTCOME_NOT_VALIDATED,
)

from .constants import (
    GO_MATERIALIZER_ID,
    GO_MODEL_ID,
    GO_OBJECT_ROLE,
    GO_OBJECT_TYPE,
)
from .values import (
    MAPPED_FIELDS,
    PROPOSAL_FIELDS,
    PROPOSED_CURIE_KEY,
    RESOLVABLE_LIST_FIELDS,
    RESOLVABLE_VALUE_FIELDS,
    evidence_code_value,
    qualifier_value,
)


_GO_ASPECTS = frozenset(
    {"molecular_function", "biological_process", "cellular_component"}
)
_CURIE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:[^\s:]+$")
_GO_CURIE_PATTERN = re.compile(r"^GO:\d{7}$")
_RGD_CURIE_PATTERN = re.compile(r"^RGD:\d+$")
# The pattern a paper-stated identifier (``proposed_curie``) of each value follows.
_PROPOSED_CURIE_PATTERNS = {
    "gene_product": _RGD_CURIE_PATTERN,
    "go_term": _GO_CURIE_PATTERN,
    "reference_curie": _CURIE_PATTERN,
    "with_from": _CURIE_PATTERN,
}
_EXCLUDED_EVIDENCE_SECTION_PATTERN = re.compile(
    r"\b(?:abstract|introduction|discussion|conclusions?)\b", re.IGNORECASE
)
_POSITIVE_EVIDENCE_SECTION_PATTERN = re.compile(
    r"\b(?:results?|methods?|materials\s+and\s+methods|figure|table|legend)\b",
    re.IGNORECASE,
)
_SUPPORTED_EVIDENCE_FIELD_ROOTS = frozenset(
    {
        "gene_product",
        "go_term",
        "evidence_code",
        "reference_curie",
        "with_from",
        "qualifiers",
        "annotation_extensions",
        "negated",
        "rationale",
        "provider_context",
        "blocking_reasons",
    }
)
_REQUIRED_PAYLOAD_PATHS = (
    "gene_product.mention",
    "gene_product.entity_type",
    "gene_product.taxon_curie",
    "go_term.mention",
    "go_term.aspect",
    "evidence_code.mention",
    "reference_curie.mention",
    "with_from",
    "qualifiers",
    "annotation_extensions",
    "negated",
    "rationale",
    "provider_context",
    "blocking_reasons",
)


class GOCuratorExtractionOutput(DomainEnvelopeExtractionResult):
    """Validated typed output for one RGD GO paper-curation pass."""


class GOMaterializationResult:
    """Shared-builder-compatible GO materialization outcome."""

    def __init__(
        self,
        *,
        payload: dict[str, Any] | None,
        issues: tuple[dict[str, Any], ...],
        source_candidate_ids: tuple[str, ...],
        evidence_record_ids: tuple[str, ...],
    ) -> None:
        self._payload = payload
        self._issues = issues
        self._source_candidate_ids = source_candidate_ids
        self._evidence_record_ids = evidence_record_ids

    @property
    def ok(self) -> bool:
        return self._payload is not None and not self._issues

    @property
    def payload(self) -> dict[str, Any] | None:
        return self._payload

    @property
    def issues(self) -> tuple[dict[str, Any], ...]:
        return self._issues

    @property
    def evidence_record_ids(self) -> tuple[str, ...]:
        return self._evidence_record_ids

    def summary(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "source_candidate_ids": list(self._source_candidate_ids),
            "evidence_record_ids": list(self._evidence_record_ids),
            "validation_issues": [dict(issue) for issue in self._issues],
        }


def materialize_go_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    resolver_entry_lookup: Any = None,
    produced_by: str = "rgd_go_paper_curator",
) -> GOMaterializationResult:
    """Build canonical ``DomainEnvelopeExtractionResult`` data from GO drafts."""

    normalized_candidate_ids = tuple(_unique_strings(candidate_ids))
    normalized_evidence = _normalized_evidence_records(evidence_records or ())
    evidence_by_id = {
        record["evidence_record_id"]: record for record in normalized_evidence
    }
    evidence_positions = {
        record["evidence_record_id"]: index
        for index, record in enumerate(normalized_evidence)
    }
    issues: list[dict[str, Any]] = []
    objects: list[CuratableObjectEnvelope] = []
    raw_mentions: list[dict[str, Any]] = []
    retained_evidence_ids: list[str] = []

    for index, candidate_id in enumerate(normalized_candidate_ids, start=1):
        try:
            candidate = workspace.get_candidate(candidate_id)
        except KeyError as exc:
            issues.append(
                _issue("candidate_ids", "unknown_candidate_id", str(exc), candidate_id)
            )
            continue

        staged_fields = copy.deepcopy(
            dict(getattr(candidate, "staged_fields", {}) or {})
        )
        payload = staged_fields.get("payload")
        if not isinstance(payload, Mapping):
            issues.append(
                _issue(
                    "payload",
                    "invalid_payload",
                    "GO candidates require a structured payload object.",
                    candidate_id,
                )
            )
            continue
        payload = copy.deepcopy(dict(payload))
        _validate_payload(payload, candidate_id=candidate_id, issues=issues)

        evidence_ids = _unique_strings(
            getattr(candidate, "evidence_record_ids", None)
            or staged_fields.get("evidence_record_ids")
        )
        if not evidence_ids:
            issues.append(
                _issue(
                    "evidence_record_ids",
                    "missing_evidence_record_ids",
                    "Finalized GO recommendations require verified document evidence.",
                    candidate_id,
                )
            )
        missing_evidence = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in evidence_by_id
        ]
        if missing_evidence:
            issues.append(
                {
                    **_issue(
                        "evidence_record_ids",
                        "unknown_evidence_record_id",
                        "Every GO evidence ID must resolve in the active evidence workspace.",
                        candidate_id,
                    ),
                    "evidence_record_ids": missing_evidence,
                }
            )
        candidate_evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        ]
        pending_ref_id = _pending_ref_id(candidate, staged_fields, index)
        for record in candidate_evidence:
            if not _evidence_attached_to_candidate(record, pending_ref_id):
                issues.append(
                    _issue(
                        "evidence_record_ids",
                        "unattached_candidate_evidence",
                        (
                            "Every GO evidence record must target the recommendation's "
                            "pending_ref_id and at least one supported GO payload field."
                        ),
                        candidate_id,
                    )
                )
        if candidate_evidence and not any(
            _positively_scoped_evidence(record) for record in candidate_evidence
        ):
            issues.append(
                _issue(
                    "evidence_record_ids",
                    "out_of_scope_evidence",
                    (
                        "GO recommendations require verified support from Results, "
                        "Methods, a figure legend, or a table."
                    ),
                    candidate_id,
                )
            )
        if any(issue.get("candidate_id") == candidate_id for issue in issues):
            continue

        gene_product = payload["gene_product"]
        mention = str(gene_product["mention"])
        metadata_refs = [
            {
                "metadata_path": f"raw_mentions[{len(raw_mentions)}]",
                "role": "source_mention",
            }
        ]
        for evidence_id in evidence_ids:
            metadata_refs.append(
                {
                    "metadata_path": f"evidence_records[{evidence_positions[evidence_id]}]",
                    "role": "verified_evidence",
                }
            )
        raw_mentions.append(
            {
                "mention": mention,
                "entity_type": str(gene_product["entity_type"]),
                "evidence_record_ids": list(evidence_ids),
            }
        )
        retained_evidence_ids.extend(evidence_ids)
        objects.append(
            CuratableObjectEnvelope(
                object_type=GO_OBJECT_TYPE,
                object_role=GO_OBJECT_ROLE,
                pending_ref_id=pending_ref_id,
                model_ref=GO_MODEL_ID,
                validation_guidance=staged_fields.get("validation_guidance"),
                # Every value awaits validation, so each proposal needs review.
                status=CuratableObjectStatus.NEEDS_REVIEW,
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Review-only GO proposal; submission and export are unsupported."
                ],
                payload=payload,
                evidence_record_ids=list(evidence_ids),
                metadata_refs=metadata_refs,
                metadata={
                    "object_role": GO_OBJECT_ROLE,
                    "reviewer_projection": "rgd",
                    "source_candidate_id": candidate_id,
                },
            )
        )

    output_payload = {
        "summary": "Finalized RGD GO paper-curation recommendations from builder state.",
        "curatable_objects": [
            obj.model_dump(mode="json", exclude_none=True) for obj in objects
        ],
        "metadata": {
            "raw_mentions": raw_mentions,
            "evidence_records": normalized_evidence,
            "normalization_notes": [
                "GO recommendations were assembled by canonical backend materialization."
            ],
            "exclusions": [],
            "ambiguities": [
                {
                    "mention": obj.payload["gene_product"]["mention"],
                    "why_ambiguous": "; ".join(obj.payload["blocking_reasons"]),
                    "recommended_followup": (
                        "Settle the blocking reasons before curator acceptance."
                    ),
                    "evidence_record_ids": list(obj.evidence_record_ids),
                }
                for obj in objects
                if obj.payload["blocking_reasons"]
            ],
            "notes": [],
            "provenance": {
                "source": GO_MATERIALIZER_ID,
                "produced_by": produced_by,
                "builder_run_id": getattr(workspace, "run_id", None),
                "source_candidate_ids": list(normalized_candidate_ids),
                "provider_key": "RGD",
            },
        },
        "run_summary": {
            "candidate_count": len(normalized_candidate_ids),
            "kept_count": len(objects),
            "excluded_count": 0,
            "ambiguous_count": sum(bool(obj.payload["blocking_reasons"]) for obj in objects),
            "warnings": [],
        },
    }
    if not issues:
        try:
            validated = GOCuratorExtractionOutput.model_validate(output_payload)
        except ValidationError as exc:
            issues.extend(_pydantic_issues(exc))
        else:
            output_payload = validated.model_dump(mode="json", exclude_none=True)

    return GOMaterializationResult(
        payload=None if issues else output_payload,
        issues=tuple(issues),
        source_candidate_ids=normalized_candidate_ids,
        evidence_record_ids=tuple(_unique_strings(retained_evidence_ids)),
    )


def _validate_payload(
    payload: Mapping[str, Any],
    *,
    candidate_id: str,
    issues: list[dict[str, Any]],
) -> None:
    for field_path in _REQUIRED_PAYLOAD_PATHS:
        value = _path_value(payload, field_path)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(
                _issue(
                    f"payload.{field_path}",
                    "missing_rationale"
                    if field_path == "rationale"
                    else "missing_required_payload_field",
                    (
                        "GO candidate is missing a rationale; patch the candidate "
                        "with a rationale saying why you selected it."
                    )
                    if field_path == "rationale"
                    else "GO candidate is missing a required contract field.",
                    candidate_id,
                )
            )
    aspect = _path_value(payload, "go_term.aspect")
    if aspect is not None and aspect not in _GO_ASPECTS:
        issues.append(
            _issue(
                "payload.go_term.aspect",
                "invalid_go_aspect",
                f"GO aspect must be one of {sorted(_GO_ASPECTS)}.",
                candidate_id,
            )
        )
    for field_path, identity_keys in RESOLVABLE_VALUE_FIELDS.items():
        value = payload.get(field_path)
        if value is None:
            continue
        try:
            check_resolvable_value(value, identity_keys=identity_keys)
        except ResolvableValueError as exc:
            issues.append(
                _issue(
                    f"payload.{field_path}",
                    "invalid_resolvable_value",
                    str(exc),
                    candidate_id,
                )
            )
    for field_path, identity_keys in RESOLVABLE_LIST_FIELDS.items():
        values = payload.get(field_path)
        if not isinstance(values, list):
            continue
        try:
            check_resolvable_list(values, identity_keys=identity_keys)
        except ResolvableValueError as exc:
            issues.append(
                _issue(
                    f"payload.{field_path}",
                    "invalid_resolvable_value",
                    str(exc),
                    candidate_id,
                )
            )
    for field_path in (*RESOLVABLE_VALUE_FIELDS, *RESOLVABLE_LIST_FIELDS):
        value = payload.get(field_path)
        for index, item in enumerate(value if isinstance(value, list) else [value]):
            path = f"payload.{field_path}[{index}]" if isinstance(value, list) else f"payload.{field_path}"
            if not isinstance(item, Mapping):
                continue
            if field_path in MAPPED_FIELDS:
                if item != _mapped_value(field_path, item, aspect):
                    issues.append(
                        _issue(
                            path,
                            "mapped_value_mismatch",
                            "The evidence code and qualifiers carry exactly the builder's "
                            "table mapping of the chosen term.",
                            candidate_id,
                        )
                    )
                continue
            if item.get(LOOKUP_OUTCOME_KEY) != OUTCOME_NOT_VALIDATED:
                issues.append(
                    _issue(
                        path,
                        "extraction_value_not_staged_for_validation",
                        "Extraction stages every searchable GO value unresolved and not yet validated.",
                        candidate_id,
                    )
                )
            proposed = item.get(PROPOSED_CURIE_KEY)
            if field_path in PROPOSAL_FIELDS and proposed is not None and not (
                isinstance(proposed, str) and _PROPOSED_CURIE_PATTERNS[field_path].fullmatch(proposed)
            ):
                issues.append(
                    _issue(
                        f"{path}.{PROPOSED_CURIE_KEY}",
                        "invalid_proposed_curie",
                        "A paper-stated identifier must be written as the paper prints it.",
                        candidate_id,
                    )
                )
    gene_product = payload.get("gene_product")
    if isinstance(gene_product, Mapping) and gene_product.get("taxon_curie") != "NCBITaxon:10116":
        issues.append(
            _issue(
                "payload.gene_product.taxon_curie",
                "invalid_rgd_taxon",
                "RGD GO paper recommendations require NCBITaxon:10116.",
                candidate_id,
            )
        )
    for field_path in (
        "with_from",
        "qualifiers",
        "annotation_extensions",
        "blocking_reasons",
    ):
        if not isinstance(payload.get(field_path), list):
            issues.append(
                _issue(
                    f"payload.{field_path}",
                    "invalid_array_field",
                    "GO contract array fields must be JSON arrays.",
                    candidate_id,
                )
            )
    if not isinstance(payload.get("negated"), bool):
        issues.append(
            _issue(
                "payload.negated",
                "invalid_negated_value",
                "negated must be a boolean.",
                candidate_id,
            )
        )
    provider_context = payload.get("provider_context")
    if (
        not isinstance(provider_context, Mapping)
        or provider_context.get("provider_key") != "RGD"
    ):
        issues.append(
            _issue(
                "payload.provider_context.provider_key",
                "invalid_provider_context",
                "RGD GO candidates require provider_context.provider_key=RGD.",
                candidate_id,
            )
        )


def _mapped_value(field_path: str, item: Mapping[str, Any], aspect: Any) -> Any:
    mention = item.get(MENTION_KEY)
    if not isinstance(mention, str) or not mention.strip():
        return None
    if field_path == "evidence_code":
        return evidence_code_value(mention)
    return qualifier_value(mention, aspect=aspect)


def _normalized_evidence_records(
    evidence_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project workspace evidence records into canonical provenance.

    Shared with every other domain pack and the validator write-back; see
    src/schemas/models/evidence_workspace.py. GO additionally requires a
    verified quote, so evidence with no quote is not admitted here.
    """
    return normalize_workspace_records(
        evidence_records,
        admit=lambda record: bool(record.verified_quote),
    )


def _excluded_evidence_section(value: Any) -> bool:
    return bool(_EXCLUDED_EVIDENCE_SECTION_PATTERN.search(str(value or "")))


def _positively_scoped_evidence(record: Mapping[str, Any]) -> bool:
    heading = " ".join(
        str(record.get(field) or "") for field in ("section", "subsection")
    )
    if _excluded_evidence_section(heading):
        return False
    if _POSITIVE_EVIDENCE_SECTION_PATTERN.search(heading):
        return True
    figure_reference = str(record.get("figure_reference") or "")
    return bool(
        re.search(r"\b(?:figure|fig\.?|table)\b", figure_reference, re.IGNORECASE)
    )


def _evidence_attached_to_candidate(
    record: Mapping[str, Any], pending_ref_id: str
) -> bool:
    targets: list[tuple[str, str]] = []
    direct_paths = record.get("field_paths") or []
    direct_field_path = str(record.get("field_path") or "")
    if direct_field_path:
        targets.append(
            (str(record.get("pending_ref_id") or ""), direct_field_path)
        )
    for field_path in direct_paths if isinstance(direct_paths, list) else []:
        targets.append((str(record.get("pending_ref_id") or ""), str(field_path)))
    for key in ("envelope_target",):
        target = record.get(key)
        if isinstance(target, Mapping):
            targets.append(
                (
                    str(target.get("pending_ref_id") or ""),
                    str(target.get("field_path") or ""),
                )
            )
    object_ref = record.get("object_ref")
    if isinstance(object_ref, Mapping):
        for field_path in direct_paths if isinstance(direct_paths, list) else []:
            targets.append(
                (str(object_ref.get("pending_ref_id") or ""), str(field_path))
            )
    envelope_targets = record.get("envelope_targets")
    for target in envelope_targets if isinstance(envelope_targets, list) else []:
        if isinstance(target, Mapping):
            targets.append(
                (
                    str(target.get("pending_ref_id") or ""),
                    str(target.get("field_path") or ""),
                )
            )
    return any(
        target_ref == pending_ref_id
        and field_path.split(".", 1)[0] in _SUPPORTED_EVIDENCE_FIELD_ROOTS
        for target_ref, field_path in targets
    )


def _pending_ref_id(
    candidate: Any, staged_fields: Mapping[str, Any], index: int
) -> str:
    direct = str(staged_fields.get("pending_ref_id") or "").strip()
    if direct:
        return direct
    pending_refs = getattr(candidate, "pending_ref_ids", None) or []
    if pending_refs:
        return str(pending_refs[0])
    return f"rgd-go-recommendation-{index}"


def _path_value(payload: Mapping[str, Any], field_path: str) -> Any:
    value: Any = payload
    for part in field_path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _issue(
    field_path: str,
    reason: str,
    message: str,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "field_path": field_path,
        "reason": reason,
        "message": message,
    }
    if candidate_id:
        issue["candidate_id"] = candidate_id
    return issue


def _pydantic_issues(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field_path": ".".join(str(part) for part in error.get("loc", ())),
            "reason": str(error.get("type") or "invalid"),
            "message": str(error.get("msg") or "Invalid value"),
        }
        for error in exc.errors()
    ]


__all__ = [
    "GOCuratorExtractionOutput",
    "GOMaterializationResult",
    "materialize_go_builder_state",
]
