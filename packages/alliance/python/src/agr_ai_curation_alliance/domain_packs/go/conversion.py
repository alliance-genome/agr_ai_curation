"""Materialize RGD GO paper-curation builder state into canonical output."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DefinitionState,
)
from src.schemas.models.base import EvidenceRecord
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

from .constants import (
    GO_MATERIALIZER_ID,
    GO_MODEL_ID,
    GO_OBJECT_ROLE,
    GO_OBJECT_TYPE,
)


_GO_ASPECTS = frozenset(
    {"molecular_function", "biological_process", "cellular_component"}
)
_CURIE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:[^\s:]+$")
_GO_CURIE_PATTERN = re.compile(r"^GO:\d{7}$")
_ECO_CURIE_PATTERN = re.compile(r"^ECO:\d{7}$")
_EVIDENCE_CODE_PATTERN = re.compile(r"^[A-Z]{2,4}$")
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
        "evidence_eco_curie",
        "reference_curie",
        "with_from",
        "qualifiers",
        "annotation_extensions",
        "negated",
        "rationale",
        "provider_context",
        "resolution_state",
        "blocking_reasons",
    }
)
_REQUIRED_PAYLOAD_PATHS = (
    "gene_product.mention",
    "gene_product.label",
    "gene_product.entity_type",
    "gene_product.taxon_curie",
    "go_term.curie",
    "go_term.label",
    "go_term.aspect",
    "evidence_code",
    "evidence_eco_curie",
    "reference_curie",
    "with_from",
    "qualifiers",
    "annotation_extensions",
    "negated",
    "rationale",
    "provider_context",
    "resolution_state",
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

        _validate_source_grounding(
            candidate,
            staged_fields,
            payload,
            resolver_entry_lookup=resolver_entry_lookup,
            candidate_id=candidate_id,
            issues=issues,
        )

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
        unresolved = payload["resolution_state"] == "unresolved" or bool(
            payload["blocking_reasons"]
        )
        objects.append(
            CuratableObjectEnvelope(
                object_type=GO_OBJECT_TYPE,
                object_role=GO_OBJECT_ROLE,
                pending_ref_id=pending_ref_id,
                model_ref=GO_MODEL_ID,
                status=(
                    CuratableObjectStatus.NEEDS_REVIEW
                    if unresolved
                    else CuratableObjectStatus.EXTRACTED
                ),
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
                        "Resolve gene-product identity before curator acceptance."
                    ),
                    "evidence_record_ids": list(obj.evidence_record_ids),
                }
                for obj in objects
                if obj.payload["resolution_state"] == "unresolved"
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
            "ambiguous_count": sum(
                obj.payload["resolution_state"] == "unresolved" for obj in objects
            ),
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
                    "missing_required_payload_field",
                    "GO candidate is missing a required contract field.",
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
    resolution_state = payload.get("resolution_state")
    if resolution_state not in {"resolved", "unresolved"}:
        issues.append(
            _issue(
                "payload.resolution_state",
                "invalid_resolution_state",
                "resolution_state must be resolved or unresolved.",
                candidate_id,
            )
        )
    gene_product = payload.get("gene_product")
    blockers = payload.get("blocking_reasons")
    if isinstance(gene_product, Mapping):
        has_curie = bool(str(gene_product.get("curie") or "").strip())
        if resolution_state == "resolved" and not has_curie:
            issues.append(
                _issue(
                    "payload.gene_product.curie",
                    "resolved_identity_missing_curie",
                    "Resolved gene-product identity requires a resolver-backed CURIE.",
                    candidate_id,
                )
            )
        if has_curie and not str(gene_product.get("curie")).startswith("RGD:"):
            issues.append(
                _issue(
                    "payload.gene_product.curie",
                    "invalid_rgd_gene_product_curie",
                    "Resolved RGD gene-product identity must use an RGD CURIE.",
                    candidate_id,
                )
            )
        if resolution_state == "unresolved" and has_curie:
            issues.append(
                _issue(
                    "payload.gene_product.curie",
                    "unresolved_identity_has_curie",
                    "Unresolved identity must not carry a guessed gene-product CURIE.",
                    candidate_id,
                )
            )
        if gene_product.get("taxon_curie") != "NCBITaxon:10116":
            issues.append(
                _issue(
                    "payload.gene_product.taxon_curie",
                    "invalid_rgd_taxon",
                    "RGD GO paper recommendations require NCBITaxon:10116.",
                    candidate_id,
                )
            )
    identifier_checks = (
        ("go_term.curie", _GO_CURIE_PATTERN, "invalid_go_curie"),
        ("evidence_eco_curie", _ECO_CURIE_PATTERN, "invalid_eco_curie"),
        ("reference_curie", _CURIE_PATTERN, "invalid_reference_curie"),
    )
    for field_path, pattern, reason in identifier_checks:
        value = _path_value(payload, field_path)
        if isinstance(value, str) and not pattern.fullmatch(value):
            issues.append(
                _issue(
                    f"payload.{field_path}",
                    reason,
                    "Identifier does not match the required CURIE contract.",
                    candidate_id,
                )
            )
    evidence_code = payload.get("evidence_code")
    if isinstance(evidence_code, str) and not _EVIDENCE_CODE_PATTERN.fullmatch(
        evidence_code
    ):
        issues.append(
            _issue(
                "payload.evidence_code",
                "invalid_evidence_code",
                "Evidence code must be a canonical uppercase GO evidence code token.",
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
    with_from = payload.get("with_from")
    if isinstance(with_from, list):
        for index, value in enumerate(with_from):
            if not isinstance(value, str) or not _CURIE_PATTERN.fullmatch(value):
                issues.append(
                    _issue(
                        f"payload.with_from[{index}]",
                        "invalid_with_from_curie",
                        "With/From values must be source-backed CURIEs.",
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
    if resolution_state == "unresolved" and not blockers:
        issues.append(
            _issue(
                "payload.blocking_reasons",
                "unresolved_identity_missing_blocker",
                "Unresolved identity requires an explicit blocking reason.",
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
    if isinstance(provider_context, Mapping):
        comparison = provider_context.get("existing_annotation_context")
        if not isinstance(comparison, Mapping) or comparison.get("status") not in {
            "available",
            "not_found",
            "unavailable",
        }:
            issues.append(
                _issue(
                    "payload.provider_context.existing_annotation_context.status",
                    "missing_existing_annotation_context",
                    "Existing annotations must be compared or marked unavailable.",
                    candidate_id,
                )
            )
        elif comparison.get("status") in {"available", "not_found"} and not isinstance(
            comparison.get("provenance"), Mapping
        ):
            issues.append(
                _issue(
                    "payload.provider_context.existing_annotation_context.provenance",
                    "missing_existing_annotation_provenance",
                    "Completed existing-annotation lookups must retain provenance.",
                    candidate_id,
                )
            )
        elif comparison.get("status") in {
            "available",
            "not_found",
        } and not comparison.get("provenance"):
            issues.append(
                _issue(
                    "payload.provider_context.existing_annotation_context.provenance",
                    "missing_existing_annotation_provenance",
                    "Completed existing-annotation lookups must retain provenance.",
                    candidate_id,
                )
            )
        elif (
            comparison.get("status") == "unavailable"
            and not str(comparison.get("note") or "").strip()
        ):
            issues.append(
                _issue(
                    "payload.provider_context.existing_annotation_context.note",
                    "missing_existing_annotation_unavailable_note",
                    "Unavailable existing annotations require an explicit status note.",
                    candidate_id,
                )
            )
        identity_resolution = provider_context.get("identity_resolution")
        if not isinstance(identity_resolution, Mapping) or not identity_resolution:
            issues.append(
                _issue(
                    "payload.provider_context.identity_resolution",
                    "missing_identity_resolution_provenance",
                    "Gene-product resolution output and provenance must be retained.",
                    candidate_id,
                )
            )


def _normalized_evidence_records(
    evidence_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_fields = set(EvidenceRecord.model_fields)
    for record in evidence_records:
        if not isinstance(record, Mapping):
            continue
        if (
            str(record.get("workspace_status") or record.get("status") or "").strip()
            == "discarded"
        ):
            continue
        payload = {
            key: value
            for key, value in record.items()
            if key in allowed_fields and value is not None
        }
        evidence_id = str(payload.get("evidence_record_id") or "").strip()
        if not evidence_id or evidence_id in seen:
            continue
        try:
            item = EvidenceRecord.model_validate(payload)
        except ValidationError:
            continue
        if not item.verified_quote:
            continue
        seen.add(evidence_id)
        normalized.append(item.model_dump(mode="json", exclude_none=True))
    return normalized


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


def _validate_source_grounding(
    candidate: Any,
    staged_fields: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    resolver_entry_lookup: Any,
    candidate_id: str,
    issues: list[dict[str, Any]],
) -> None:
    grounding = staged_fields.get("source_grounding")
    refs = list(getattr(candidate, "resolver_selection_refs", None) or [])
    if not isinstance(grounding, Mapping) or grounding.get("payload") != payload:
        issues.append(
            _issue(
                "source_grounding",
                "stale_or_missing_source_grounding",
                "Finalized GO payload values must match the tool-grounded staged snapshot.",
                candidate_id,
            )
        )
        return
    requirements = grounding.get("requirements")
    if not refs or not isinstance(requirements, list) or resolver_entry_lookup is None:
        issues.append(
            _issue(
                "source_grounding",
                "missing_tool_output_provenance",
                "Finalized GO recommendations require run-scoped tool-output provenance.",
                candidate_id,
            )
        )
        return
    entries = []
    for ref in refs:
        try:
            entries.append(resolver_entry_lookup(ref))
        except (KeyError, ValueError):
            continue
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        allowed_tools = set(requirement.get("tool_names") or [])
        value = requirement.get("value")
        if not any(
            getattr(entry, "tool_name", None) in allowed_tools
            and callable(getattr(entry, "contains", None))
            and entry.contains(value)
            for entry in entries
        ):
            issues.append(
                _issue(
                    str(requirement.get("field_path") or "source_grounding"),
                    "unobserved_tool_value",
                    "A controlled GO value no longer matches its run-scoped tool output.",
                    candidate_id,
                )
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
