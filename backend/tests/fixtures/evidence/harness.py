"""Reusable helpers for evidence fixture-driven tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


FIXTURES_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE_NAME = "tool_verified_gene_paper"
NON_GENE_EVIDENCE_FIXTURE_NAMES = (
    "tool_verified_allele_paper",
    "tool_verified_disease_paper",
    "tool_verified_chemical_paper",
    "tool_verified_phenotype_paper",
    "tool_verified_gene_expression_paper",
)
ALL_EVIDENCE_FIXTURE_NAMES = (DEFAULT_FIXTURE_NAME, *NON_GENE_EVIDENCE_FIXTURE_NAMES)


def load_evidence_fixture(name: str = DEFAULT_FIXTURE_NAME) -> dict[str, Any]:
    fixture_path = FIXTURES_DIR / f"{name}.json"
    with fixture_path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def chunk_map(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(chunk["id"]): copy.deepcopy(chunk)
        for chunk in fixture.get("chunks", [])
        if isinstance(chunk, dict) and chunk.get("id")
    }


def tool_case_map(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(case["case_id"]): copy.deepcopy(case)
        for case in fixture.get("tool_cases", [])
        if isinstance(case, dict) and case.get("case_id")
    }


def build_verified_evidence_record(tool_case: dict[str, Any]) -> dict[str, Any]:
    tool_input = dict(tool_case.get("tool_input") or {})
    tool_result = dict(tool_case.get("expected_tool_result") or {})
    if str(tool_result.get("status") or "").strip().lower() != "verified":
        raise ValueError("Only verified tool cases can become evidence records.")

    record = {
        "entity": tool_input["entity"],
        "chunk_id": tool_input["chunk_id"],
        "verified_quote": tool_result["verified_quote"],
        "page": tool_result["page"],
        "section": tool_result["section"],
    }

    subsection = tool_result.get("subsection")
    if subsection:
        record["subsection"] = subsection

    figure_reference = tool_result.get("figure_reference")
    if figure_reference:
        record["figure_reference"] = figure_reference

    return record


def _normalized_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _first_non_empty_scope_value(values: Any) -> str | None:
    if not isinstance(values, list):
        return None

    for value in values:
        normalized = _normalized_optional_string(value)
        if normalized is not None:
            return normalized

    return None


def build_extraction_scope(source: dict[str, Any]) -> dict[str, str | None]:
    extraction = source.get("extraction") if isinstance(source.get("extraction"), dict) else source
    if not isinstance(extraction, dict):
        return {
            "adapter_key": None,
            "profile_key": None,
            "domain_key": None,
        }

    scope_confirmation = extraction.get("scope_confirmation") or {}
    if not isinstance(scope_confirmation, dict):
        scope_confirmation = {}

    # Fixtures are currently single-valued per scope dimension; this helper intentionally
    # resolves the first non-empty value and will need a contract update when multi-scope
    # fixtures are introduced.
    return {
        "adapter_key": _normalized_optional_string(extraction.get("adapter_key"))
        or _first_non_empty_scope_value(scope_confirmation.get("adapter_keys")),
        "profile_key": _normalized_optional_string(extraction.get("profile_key"))
        or _first_non_empty_scope_value(scope_confirmation.get("profile_keys")),
        "domain_key": _normalized_optional_string(extraction.get("domain_key"))
        or _first_non_empty_scope_value(scope_confirmation.get("domain_keys")),
    }


def build_extraction_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    extraction = copy.deepcopy(fixture.get("extraction") or {})
    case_lookup = tool_case_map(fixture)

    items: list[dict[str, Any]] = []
    for raw_item in extraction.get("items", []):
        item = dict(raw_item or {})
        evidence_case_ids = list(item.pop("evidence_case_ids", []))
        item["evidence"] = [
            build_verified_evidence_record(case_lookup[case_id])
            for case_id in evidence_case_ids
        ]
        items.append(item)

    top_level_case_ids = list(extraction.get("top_level_evidence_case_ids", []))
    extraction.pop("tool_name", None)
    extraction.pop("agent_key", None)
    extraction.pop("top_level_evidence_case_ids", None)

    extraction["items"] = items
    extraction["evidence_records"] = [
        build_verified_evidence_record(case_lookup[case_id])
        for case_id in top_level_case_ids
    ]
    extraction["run_summary"] = dict(extraction.get("run_summary") or {})
    return extraction


def build_expected_candidates(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    case_lookup = tool_case_map(fixture)
    candidates: list[dict[str, Any]] = []

    for raw_candidate in fixture.get("expected_candidates", []):
        candidate = copy.deepcopy(raw_candidate)
        evidence_case_ids = list(candidate.pop("evidence_case_ids", []))
        candidate["evidence"] = [
            build_verified_evidence_record(case_lookup[case_id])
            for case_id in evidence_case_ids
        ]
        candidates.append(candidate)

    return candidates


def build_expected_sse_records(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    extraction = fixture.get("extraction") or {}
    case_lookup = tool_case_map(fixture)
    return [
        build_verified_evidence_record(case_lookup[case_id])
        for case_id in extraction.get("top_level_evidence_case_ids", [])
    ]
