"""Unit tests for active domain-pack validator dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope


def _pack_text(*, max_tool_calls: int | None = 3) -> str:
    max_tool_calls_yaml = (
        f"        max_tool_calls: {max_tool_calls}\n"
        if max_tool_calls is not None
        else ""
    )
    return f"""
pack_id: fixture.dispatch
display_name: Fixture Dispatch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    fields:
      - field_path: gene.identifier
        field_type: string
      - field_path: gene.symbol
        field_type: string
  - object_type: Gene
    display_name: Gene reference
    metadata:
      object_role: validated_reference
    fields:
      - field_path: identifier
        field_type: string
        required: true
      - field_path: symbol
        field_type: string
        required: true
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.identifier_lookup
        display_name: Identifier lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: identifier_validator
        applies_to:
          domain_pack_id: fixture.dispatch
          object_types:
            - GeneAssertion
          field_paths:
            - gene.identifier
        required: true
        blocking: true
{max_tool_calls_yaml}        input_fields:
          identifier:
            source: payload
            path: gene.identifier
          evidence_quote:
            source: evidence_record
            path: quote
            required: false
        expected_result_fields:
          identifier: gene.identifier
          symbol: gene.symbol
""".strip()


def _loaded_pack(tmp_path: Path, *, max_tool_calls: int | None = 3) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        _pack_text(max_tool_calls=max_tool_calls),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _envelope(
    *,
    identifier: str = "BAD:0001",
    evidence_records: list[dict[str, Any]] | None = None,
) -> DomainEnvelope:
    payload: dict[str, Any] = {
        "gene": {
            "identifier": identifier,
            "symbol": "ABC-1",
        }
    }
    if evidence_records is not None:
        payload["evidence_records"] = evidence_records
    return DomainEnvelope(
        envelope_id="dispatch-env",
        domain_pack_id="fixture.dispatch",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="object-1",
                payload=payload,
                evidence_record_ids=[
                    record["evidence_record_id"]
                    for record in evidence_records or []
                    if "evidence_record_id" in record
                ],
            )
        ],
    )


def _result_payload(
    request,
    *,
    status: str = "resolved",
    resolved_values: dict[str, Any] | None = None,
    missing_expected_fields: list[str] | None = None,
    outcome: str = "success",
) -> dict[str, Any]:
    resolved_values = resolved_values if resolved_values is not None else {
        "identifier": "AGR:0001",
        "symbol": "ABC-1",
    }
    return {
        "status": status,
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": resolved_values,
        "resolved_objects": [
            {
                "object_type": "Gene",
                "canonical_id": resolved_values.get("identifier"),
                "payload": dict(resolved_values),
            }
        ]
        if status == "resolved"
        else [],
        "missing_expected_fields": missing_expected_fields or [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "fixture_lookup",
                "method": "exact_identifier",
                "query": {"identifier": request.selected_inputs.get("identifier")},
                "result_count": 1 if outcome == "success" else 0,
                "outcome": outcome,
            }
        ],
        "curator_message": None,
        "explanation": "Fixture validator result.",
    }


def _single_result_finding(result):
    return next(
        finding
        for finding in result.envelope.validation_findings
        if finding.code in {
            "domain_pack.validator_resolved",
            "domain_pack.validator_unresolved",
        }
    )


def test_dispatch_active_binding_sends_typed_request_and_appends_resolved_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    captured = {}

    def _runner(request, *, binding):
        captured["request"] = request
        captured["binding"] = binding
        return _result_payload(request)

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
        source_envelope_revision=7,
    )

    request = captured["request"]
    assert captured["binding"].max_tool_calls == 3
    assert request.validator_binding_id == "fixture.identifier_lookup"
    assert request.validator_agent.package_id == "fixture.validators"
    assert request.selected_inputs == {"identifier": "BAD:0001"}
    assert request.target.input_values == request.selected_inputs
    assert "evidence_quote" in request.input_selectors

    finding = _single_result_finding(result)
    assert finding.status.value == "resolved"
    assert finding.code == "domain_pack.validator_resolved"
    assert finding.field_ref.field_path == "gene.identifier"
    assert finding.details["validation_metadata"]["source_envelope_revision"] == 7
    assert finding.details["validation_result"]["resolved_objects"][0]["object_type"] == "Gene"
    materialized_gene = next(
        domain_object
        for domain_object in result.envelope.objects
        if domain_object.object_type == "Gene"
    )
    assert materialized_gene.status.value == "validated"
    assert materialized_gene.payload == {
        "identifier": "AGR:0001",
        "symbol": "ABC-1",
    }
    assert result.envelope.objects[0].object_refs == [
        materialized_gene.to_object_ref()
    ]
    assert result.validator_results[0].status == "resolved"


def test_dispatch_active_binding_returns_unresolved_validator_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            missing_expected_fields=["identifier", "symbol"],
            outcome="not_found",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.status.value == "open"
    assert finding.severity.value == "blocker"
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.details["failure_classification"] == "missing_expected_result_field"
    assert finding.details["failure_classification"] != "under_development"
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "not_found"
    assert result.validator_results[0].status == "unresolved"


def test_invalid_validator_schema_becomes_controlled_unresolved_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return {"status": "resolved", "request_id": request.request_id}

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "incompatible output" in result.validator_results[0].explanation


def test_unknown_lookup_outcome_becomes_invalid_schema_result(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            outcome="timeout",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "incompatible output" in result.validator_results[0].explanation


def test_conflict_lookup_outcome_uses_explicit_blocked_status(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            outcome="conflict",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.details["failure_classification"] == "conflict"
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "blocked"


def test_unclassifiable_unresolved_output_becomes_invalid_schema_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        payload = _result_payload(
            request,
            status="unresolved",
            resolved_values={},
        )
        payload["lookup_attempts"] = []
        return payload

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "Unable to classify unresolved validator result" in (
        result.validator_results[0].explanation
    )


def test_resolved_validator_missing_expected_fields_is_unresolved(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            resolved_values={"identifier": "AGR:0001"},
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert result.validator_results[0].missing_expected_fields == ["symbol"]
    assert finding.details["failure_classification"] == "missing_expected_result_field"
    assert finding.details["missing_expected_fields"] == ["symbol"]


def test_runner_error_becomes_controlled_unresolved_result(tmp_path: Path):
    pack = _loaded_pack(tmp_path, max_tool_calls=1)
    captured = {}

    def _runner(request, *, binding):
        captured["max_tool_calls"] = binding.max_tool_calls
        raise RuntimeError("tool budget exhausted")

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert captured["max_tool_calls"] == 1
    assert result.validator_results[0].status == "unresolved"
    assert "tool budget exhausted" in result.validator_results[0].explanation
    assert finding.details["failure_classification"] == "transient"


def test_ambiguous_optional_selector_still_blocks_dispatch(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("optional ambiguity should not dispatch")

    result = dispatch_active_validator_bindings(
        _envelope(
            evidence_records=[
                {"evidence_record_id": "evidence-1", "quote": "A"},
                {"evidence_record_id": "evidence-2", "quote": "B"},
            ]
        ),
        pack,
        runner=_runner,
    )

    assert {finding.code for finding in result.envelope.validation_findings} == {
        "selector_ambiguous"
    }
    assert result.validator_results == ()
