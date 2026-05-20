"""Unit tests for active domain-pack validator dispatch."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.lib.config.agent_loader import AgentDefinition
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validator_dispatch import (
    dispatch_active_validator_bindings,
    run_package_scoped_validator_agent_batch,
    run_package_scoped_validator_agent,
    validator_result_from_agent_output,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.domain_validator import (
    DomainValidationRequest,
    ValidationTarget,
    ValidatorAgentRef,
)


def _pack_text(
    *,
    max_tool_calls: int | None = 3,
    batch_enabled: bool = False,
    second_binding: bool = False,
) -> str:
    max_tool_calls_yaml = (
        f"        max_tool_calls: {max_tool_calls}\n"
        if max_tool_calls is not None
        else ""
    )
    batch_yaml = (
        "        batch:\n"
        "          enabled: true\n"
        "          family: fixture_gene_reference\n"
        if batch_enabled
        else ""
    )
    second_binding_yaml = (
        """
      - binding_id: fixture.symbol_lookup
        display_name: Symbol lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: symbol_validator
        applies_to:
          domain_pack_id: fixture.dispatch
          object_types:
            - GeneAssertion
          field_paths:
            - gene.symbol
        required: true
        blocking: false
        batch:
          enabled: true
          family: fixture_symbol_reference
        input_fields:
          symbol:
            source: payload
            path: gene.symbol
        expected_result_fields:
          symbol: gene.symbol
"""
        if second_binding
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
{batch_yaml}{second_binding_yaml}
""".strip()


def _loaded_pack(
    tmp_path: Path,
    *,
    max_tool_calls: int | None = 3,
    batch_enabled: bool = False,
    second_binding: bool = False,
) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        _pack_text(
            max_tool_calls=max_tool_calls,
            batch_enabled=batch_enabled,
            second_binding=second_binding,
        ),
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


def _empty_dispatch_pack(tmp_path: Path) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.empty-dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.empty_dispatch
display_name: Fixture Empty Dispatch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: ThingPayload
    display_name: Thing payload
object_definitions:
  - object_type: Thing
    display_name: Thing
    model_ref: ThingPayload
    fields:
      - field_path: label
        field_type: string
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.structural_data_check
        display_name: Data check
        validator_agent:
          package_id: fixture.validators
          agent_id: thing_validator
        applies_to:
          domain_pack_id: fixture.empty_dispatch
          object_types:
            - Thing
        input_fields: {}
        expected_result_fields: {}
        required: true
        blocking: false
        allow_opt_out: true
        definition_state: in_development
""".strip(),
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


def _alliance_gene_pack() -> LoadedDomainPack:
    repo_root = Path(__file__).resolve().parents[5]
    pack_path = repo_root / "packages" / "alliance" / "domain_packs" / "gene"
    metadata_path = pack_path / "domain_pack.yaml"
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


def _multi_object_envelope(
    identifiers: list[str],
    *,
    evidence_quotes: list[str] | None = None,
) -> DomainEnvelope:
    objects = []
    for index, identifier in enumerate(identifiers, start=1):
        payload: dict[str, Any] = {
            "gene": {
                "identifier": identifier,
                "symbol": f"ABC-{index}",
            }
        }
        if evidence_quotes is not None:
            evidence_record_id = f"evidence-{index}"
            payload["evidence_records"] = [
                {
                    "evidence_record_id": evidence_record_id,
                    "quote": evidence_quotes[index - 1],
                }
            ]
            evidence_record_ids = [evidence_record_id]
        else:
            evidence_record_ids = []
        objects.append(
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id=f"object-{index}",
                payload=payload,
                evidence_record_ids=evidence_record_ids,
            )
        )
    return DomainEnvelope(
        envelope_id="dispatch-env",
        domain_pack_id="fixture.dispatch",
        objects=objects,
    )


def _gene_mentions_envelope(mentions: list[str]) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="gene-env",
        domain_pack_id="gene",
        objects=[
            CuratableObjectEnvelope(
                object_type="gene_mention_evidence",
                pending_ref_id=f"gene-mention-{index}",
                object_role="validated_reference",
                payload={
                    "mention": mention,
                    "species": "Drosophila melanogaster",
                    "taxon_hint": "NCBITaxon:7227",
                    "data_provider_hint": "FlyBase",
                    "verified_quote": f"{mention} was discussed in the paper.",
                },
            )
            for index, mention in enumerate(mentions, start=1)
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


def _validation_request() -> DomainValidationRequest:
    return DomainValidationRequest(
        request_id="domain-validation:test",
        validator_binding_id="fixture.identifier_lookup",
        validator_agent=ValidatorAgentRef(
            package_id="fixture.validators",
            agent_id="identifier_validator",
        ),
        target=ValidationTarget(
            domain_pack_id="fixture.dispatch",
            object_type="GeneAssertion",
            field_path="gene.identifier",
        ),
        selected_inputs={"identifier": "BAD:0001"},
        expected_result_fields={"identifier": "gene.identifier"},
    )


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


def test_dispatch_default_runner_uses_worker_thread_from_running_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack = _loaded_pack(tmp_path)
    event_loop_thread_id = threading.get_ident()
    captured = {}

    def _fake_package_validator(request, *, binding):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        captured["thread_id"] = threading.get_ident()
        captured["binding"] = binding
        return _result_payload(request)

    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.run_package_scoped_validator_agent",
        _fake_package_validator,
    )

    async def _dispatch_inside_event_loop():
        return dispatch_active_validator_bindings(
            _envelope(),
            pack,
            source_envelope_revision=3,
        )

    result = asyncio.run(_dispatch_inside_event_loop())

    assert captured["thread_id"] != event_loop_thread_id
    assert captured["binding"].binding_id == "fixture.identifier_lookup"
    finding = _single_result_finding(result)
    assert finding.status.value == "resolved"


def test_dispatch_skips_active_binding_without_inputs_or_expected_results(
    tmp_path: Path,
):
    pack = _empty_dispatch_pack(tmp_path)
    envelope = DomainEnvelope(
        envelope_id="empty-dispatch-env",
        domain_pack_id="fixture.empty_dispatch",
        objects=[
            CuratableObjectEnvelope(
                object_type="Thing",
                pending_ref_id="thing-1",
                payload={"label": "empty dispatch"},
            )
        ],
    )

    def _runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("empty structural binding should not dispatch")

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=_runner,
    )

    assert [match.binding.binding_id for match in result.matched_bindings] == [
        "fixture.structural_data_check"
    ]
    assert result.validator_results == ()
    assert result.appended_findings == ()


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
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "not_found"
    assert result.validator_results[0].status == "unresolved"


def test_dispatch_deduplicates_equivalent_identity_requests_before_validation(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    calls = []

    def _runner(request, *, binding):
        calls.append(request.request_id)
        return _result_payload(request)

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(
            ["BAD:0001", "BAD:0001"],
            evidence_quotes=["First paper quote.", "Second paper quote."],
        ),
        pack,
        runner=_runner,
    )

    assert len(calls) == 1
    assert len(result.validator_results) == 2
    assert {item.status for item in result.validator_results} == {"resolved"}
    assert len(
        [
            finding
            for finding in result.envelope.validation_findings
            if finding.code == "domain_pack.validator_resolved"
        ]
    ) == 2
    materialized_gene = next(
        domain_object
        for domain_object in result.envelope.objects
        if domain_object.object_type == "Gene"
    )
    assert all(
        materialized_gene.to_object_ref() in domain_object.object_refs
        for domain_object in result.envelope.objects
        if domain_object.object_type == "GeneAssertion"
    )


def test_dispatch_runs_unique_validator_requests_in_parallel(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    barrier = threading.Barrier(2)
    seen_thread_ids: set[int] = set()

    def _runner(request, *, binding):
        seen_thread_ids.add(threading.get_ident())
        barrier.wait(timeout=1)
        return _result_payload(request)

    started_at = time.monotonic()
    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        runner=_runner,
        max_parallel_validators=2,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.9
    assert len(seen_thread_ids) == 2
    assert len(result.validator_results) == 2
    assert {item.status for item in result.validator_results} == {"resolved"}


def test_dispatch_batches_opted_in_validator_requests(tmp_path: Path):
    pack = _loaded_pack(tmp_path, batch_enabled=True)
    batch_calls: list[list[str]] = []

    def _single_runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("batch-enabled binding should use the batch runner")

    def _batch_runner(jobs, *, binding):
        batch_calls.append([job.request.selected_inputs["identifier"] for job in jobs])
        return [
            _result_payload(
                job.request,
                resolved_values={
                    "identifier": f"AGR:{index:04d}",
                    "symbol": f"ABC-{index}",
                },
            )
            for index, job in enumerate(jobs, start=1)
        ]

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        runner=_single_runner,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert batch_calls == [["BAD:0001", "BAD:0002"]]
    assert result.validator_agent_run_count == 1
    assert result.batch_validator_run_count == 1
    assert len(result.validator_results) == 2
    assert [item.status for item in result.validator_results] == [
        "resolved",
        "resolved",
    ]


def test_dispatch_batches_after_dedupe_and_remaps_to_original_requests(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True)
    batch_request_ids: list[list[str]] = []

    def _batch_runner(jobs, *, binding):
        batch_request_ids.append([job.request.request_id for job in jobs])
        return [_result_payload(job.request) for job in jobs]

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(
            ["BAD:0001", "BAD:0001", "BAD:0002"],
            evidence_quotes=[
                "First paper quote.",
                "Second paper quote.",
                "Third paper quote.",
            ],
        ),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert len(batch_request_ids) == 1
    assert len(batch_request_ids[0]) == 2
    assert result.validator_agent_run_count == 1
    assert result.batch_validator_run_count == 1
    assert len(result.validator_results) == 3
    assert result.validator_results[0].request_id != result.validator_results[1].request_id
    assert {item.status for item in result.validator_results} == {"resolved"}


def test_dispatch_batches_mixed_validator_agents_separately_and_preserves_order(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True, second_binding=True)
    batch_groups: list[tuple[str, list[str]]] = []

    def _batch_runner(jobs, *, binding):
        batch_groups.append(
            (
                binding.validator_agent.agent_id,
                [job.request.validator_binding_id for job in jobs],
            )
        )
        if binding.binding_id == "fixture.symbol_lookup":
            return [
                _result_payload(
                    job.request,
                    resolved_values={"symbol": job.request.selected_inputs["symbol"]},
                )
                for job in jobs
            ]
        return [_result_payload(job.request) for job in jobs]

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert batch_groups == [
        (
            "identifier_validator",
            ["fixture.identifier_lookup", "fixture.identifier_lookup"],
        ),
        ("symbol_validator", ["fixture.symbol_lookup", "fixture.symbol_lookup"]),
    ]
    assert [item.validator_binding_id for item in result.validator_results] == [
        "fixture.identifier_lookup",
        "fixture.identifier_lookup",
        "fixture.symbol_lookup",
        "fixture.symbol_lookup",
    ]
    assert result.validator_agent_run_count == 2
    assert result.batch_validator_run_count == 2


def test_bad_batch_result_identity_becomes_controlled_unresolved_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True)

    def _batch_runner(jobs, *, binding):
        payloads = [_result_payload(job.request) for job in jobs]
        payloads[0]["target"]["object_type"] = "stale_object"
        return {"results": payloads}

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert result.validator_results[0].status == "unresolved"
    assert result.validator_results[0].lookup_attempts[0].method == "invalid_schema"
    assert result.validator_results[1].status == "resolved"
    findings = [
        finding
        for finding in result.envelope.validation_findings
        if finding.code
        in {"domain_pack.validator_resolved", "domain_pack.validator_unresolved"}
    ]
    assert [finding.code for finding in findings] == [
        "domain_pack.validator_unresolved",
        "domain_pack.validator_resolved",
    ]


def test_bad_batch_extra_request_id_becomes_controlled_unresolved_results(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True)

    def _batch_runner(jobs, *, binding):
        payloads = [_result_payload(job.request) for job in jobs]
        extra_payload = _result_payload(jobs[0].request)
        extra_payload["request_id"] = "domain-validation:unexpected"
        return {"results": [*payloads, extra_payload]}

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert [item.status for item in result.validator_results] == [
        "unresolved",
        "unresolved",
    ]
    assert {
        item.lookup_attempts[0].method for item in result.validator_results
    } == {"invalid_schema"}
    assert all(
        "unexpected request IDs" in item.explanation
        for item in result.validator_results
    )


def test_alliance_gene_pack_batches_gene_mentions_through_batch_path():
    pack = _alliance_gene_pack()
    mentions = ["crumbs", "crb", "ninaE", "Actin"]
    captured_mentions: list[str] = []

    def _single_runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("gene_validation should use the batch runner")

    def _batch_runner(jobs, *, binding):
        captured_mentions.extend(
            str(job.request.selected_inputs["mention"]) for job in jobs
        )
        results = []
        for job in jobs:
            request = job.request
            mention = str(request.selected_inputs["mention"])
            if mention == "Actin":
                results.append(
                    {
                        "status": "unresolved",
                        "request_id": request.request_id,
                        "validator_binding_id": request.validator_binding_id,
                        "validator_agent": request.validator_agent.model_dump(mode="json"),
                        "target": request.target.model_dump(mode="json"),
                        "resolved_values": {},
                        "resolved_objects": [],
                        "missing_expected_fields": ["curie", "symbol", "taxon"],
                        "candidates": [
                            {
                                "value": "Actin",
                                "label": "Actin",
                                "object_type": "Gene",
                                "matched_fields": {"mention": "Actin"},
                            }
                        ],
                        "lookup_attempts": [
                            {
                                "provider": "fixture_gene_lookup",
                                "method": "search_genes_bulk",
                                "query": {
                                    "gene_symbols": mentions,
                                    "data_provider": "FlyBase",
                                },
                                "result_count": 4,
                                "outcome": "ambiguous",
                            }
                        ],
                        "curator_message": "Actin remains ambiguous.",
                        "explanation": "Bulk lookup returned ambiguous Actin candidates.",
                    }
                )
                continue

            results.append(
                {
                    "status": "resolved",
                    "request_id": request.request_id,
                    "validator_binding_id": request.validator_binding_id,
                    "validator_agent": request.validator_agent.model_dump(mode="json"),
                    "target": request.target.model_dump(mode="json"),
                    "resolved_values": {
                        "curie": f"AGR:{len(results) + 1:07d}",
                        "symbol": "crb" if mention in {"crumbs", "crb"} else mention,
                        "taxon": "NCBITaxon:7227",
                    },
                    "resolved_objects": [],
                    "missing_expected_fields": [],
                    "candidates": [],
                    "lookup_attempts": [
                        {
                            "provider": "fixture_gene_lookup",
                            "method": "search_genes_bulk",
                            "query": {
                                "gene_symbols": mentions,
                                "data_provider": "FlyBase",
                            },
                            "result_count": 4,
                            "outcome": "success",
                        }
                    ],
                    "curator_message": f"{mention} resolved through bulk lookup.",
                    "explanation": "Bulk lookup resolved this gene mention.",
                }
            )
        return {"results": results}

    result = dispatch_active_validator_bindings(
        _gene_mentions_envelope(mentions),
        pack,
        runner=_single_runner,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert captured_mentions == mentions
    assert result.validator_agent_run_count == 1
    assert result.batch_validator_run_count == 1
    assert [item.status for item in result.validator_results] == [
        "resolved",
        "resolved",
        "resolved",
        "unresolved",
    ]
    assert result.validator_results[0].lookup_attempts[0].method == "search_genes_bulk"
    assert [item.payload.get("gene_symbol") for item in result.envelope.objects] == [
        "crb",
        "crb",
        "ninaE",
        None,
    ]


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


def test_concrete_validator_envelope_projects_to_shared_result_contract():
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    request = _validation_request()
    payload = _result_payload(request)
    payload["gene_candidates"] = [
        {
            "gene_id": "AGR:0001",
            "symbol": "ABC-1",
            "data_provider": "FIXTURE",
        }
    ]
    concrete_result = GeneResultEnvelope.model_validate(payload)

    result = validator_result_from_agent_output(
        SimpleNamespace(final_output=concrete_result),
        request=request,
    )

    assert result.status == "resolved"
    assert result.resolved_values == {
        "identifier": "AGR:0001",
        "symbol": "ABC-1",
    }
    assert not hasattr(result, "gene_candidates")


def test_validator_result_identity_mismatch_becomes_invalid_schema_result():
    request = _validation_request()
    payload = _result_payload(request)
    payload.update(
        {
            "request_id": "domain-validation:stale",
            "validator_binding_id": "stale.binding",
            "validator_agent": {
                "package_id": "stale.package",
                "agent_id": "stale_agent",
            },
            "target": {
                "domain_pack_id": "stale.pack",
                "object_type": "stale_object",
            },
        }
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "unresolved"
    assert result.request_id == request.request_id
    assert result.validator_binding_id == request.validator_binding_id
    assert result.validator_agent == request.validator_agent
    assert result.target == request.target
    assert result.resolved_values == {}
    assert result.lookup_attempts[0].method == "invalid_schema"
    assert "different request" in result.explanation


def test_validator_result_allows_target_input_value_context_drift():
    request = _validation_request()
    request = request.model_copy(
        update={
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quote": "Molar abundance was 1.54 \u00b1 0.34 fmole/eye.",
            },
            "target": request.target.model_copy(
                update={
                    "object_id": "object-1",
                    "input_values": {
                        "identifier": "BAD:0001",
                        "evidence_quote": (
                            "Molar abundance was 1.54 \u00b1 0.34 fmole/eye."
                        ),
                    },
                }
            ),
        }
    )
    payload = _result_payload(request)
    payload["target"]["input_values"]["evidence_quote"] = (
        "Molar abundance was 1.54 \u0000b1 0.34 fmole/eye."
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "resolved"
    assert result.target == request.target
    assert result.target.input_values["evidence_quote"].endswith(
        "\u00b1 0.34 fmole/eye."
    )


def test_dispatch_rejects_identity_mismatch_without_materializing(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        payload = _result_payload(request)
        payload["request_id"] = "domain-validation:stale"
        return payload

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "different request" in result.validator_results[0].explanation
    assert not any(
        domain_object.object_type == "Gene"
        for domain_object in result.envelope.objects
    )


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


def test_resolved_validator_without_lookup_evidence_becomes_invalid_schema_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        payload = _result_payload(request)
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
    assert "successful lookup_attempt" in result.validator_results[0].explanation
    assert not any(
        domain_object.object_type == "Gene"
        for domain_object in result.envelope.objects
    )


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


def test_blocked_lookup_outcome_uses_explicit_blocked_status(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            outcome="blocked",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.details["failure_classification"] == "blocked"
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


def test_package_scoped_validator_agent_relaxes_domain_validator_output_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    from agents import AgentOutputSchema
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    source_agent = SimpleNamespace(output_type=GeneResultEnvelope)
    captured = {}

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        captured["kwargs"] = kwargs
        return {"status": "resolved"}

    monkeypatch.setattr("agents.Runner.run_sync", _fake_run_sync)

    binding = SimpleNamespace(max_tool_calls=4)
    run_package_scoped_validator_agent(_validation_request(), binding=binding)

    runtime_agent = captured["agent"]
    assert runtime_agent is not source_agent
    assert isinstance(runtime_agent.output_type, AgentOutputSchema)
    assert runtime_agent.output_type.output_type is GeneResultEnvelope
    assert runtime_agent.output_type.is_strict_json_schema() is False
    assert captured["kwargs"]["max_turns"] == 4


def test_package_scoped_validator_batch_agent_uses_batch_output_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    from agents import AgentOutputSchema
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    request = _validation_request()
    source_agent = SimpleNamespace(output_type=GeneResultEnvelope)
    captured = {}

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        captured["kwargs"] = kwargs
        return {"results": [_result_payload(request)]}

    monkeypatch.setattr("agents.Runner.run_sync", _fake_run_sync)

    binding = SimpleNamespace(max_tool_calls=4)
    run_package_scoped_validator_agent_batch(
        [SimpleNamespace(request=request)],
        binding=binding,
    )

    runtime_agent = captured["agent"]
    assert runtime_agent is not source_agent
    assert isinstance(runtime_agent.output_type, AgentOutputSchema)
    assert runtime_agent.output_type.output_type.__name__ == "GeneResultEnvelopeBatchEnvelope"
    assert runtime_agent.output_type.is_strict_json_schema() is False
    payload = json.loads(captured["kwargs"]["input"])
    assert payload["mode"] == "domain_validator_batch"
    assert "one bulk lookup tool call per compatible shared lookup group" in payload[
        "instructions"
    ]
    assert "gene_symbols" in payload["instructions"]
    assert payload["requests"][0]["request_id"] == request.request_id
    assert captured["kwargs"]["max_turns"] == 4
