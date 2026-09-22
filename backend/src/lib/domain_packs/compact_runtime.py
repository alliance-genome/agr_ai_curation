"""Shared tool boundary for compact validator decisions.

This layer knows invocation/request identity, not Alliance response formats.
Package adapters own lookup interpretation and canonical record projection.
"""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass
import json
import sys
import importlib
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import BaseModel
from pydantic import create_model

from src.lib.domain_packs.compact_decisions import (
    CanonicalValidatorRecord, DecisionContract, ValidatorDecisionWorkspace,
)
from src.schemas.domain_validator import DomainValidatorResultBase, ValidatorLookupAttempt


@dataclass(frozen=True)
class CapturedValidatorLookup:
    attempt: ValidatorLookupAttempt
    records: list[CanonicalValidatorRecord]


LookupAdapter = Callable[[DecisionContract, str, Mapping[str, Any], Mapping[str, Any]], CapturedValidatorLookup]


class CompactValidatorRuntime:
    """One runtime per invocation, shared by its lookup and finalization tools."""

    def __init__(self, contracts: list[DecisionContract], adapter: LookupAdapter):
        self.workspace = ValidatorDecisionWorkspace(contracts)
        self.contracts = {contract.request.request_id: contract for contract in contracts}
        self.adapter = adapter
        self.lookup_tool_names: frozenset[str] = frozenset()
        self.source_catalog: list[dict[str, Any]] = []
        self.standalone_evidence_records: list[dict[str, Any]] | None = None

    def wrap_lookup_tool(self, tool: Any) -> Any:
        """Capture raw facts before downstream presentation/trace compaction.

        Batch lookup callers explicitly name their request scope. The adapter
        receives each request separately and must project only its records.
        Never infer that every bulk result belongs to every batch request.
        """
        wrapped = copy(tool)
        schema = deepcopy(tool.params_json_schema)
        if "validator_request_ids" in schema.get("properties", {}):
            raise ValueError("Lookup tool already declares validator_request_ids")
        batch = len(self.contracts) != 1
        if batch:
            schema.setdefault("properties", {})["validator_request_ids"] = {
                "type": "array", "minItems": 1, "uniqueItems": True,
                "items": {"type": "string", "enum": list(self.contracts)},
                "description": "Only the validator requests this lookup serves.",
            }
            schema.setdefault("required", []).append("validator_request_ids")
        wrapped.params_json_schema = schema

        async def invoke(context, arguments):
            supplied = json.loads(arguments)
            if not isinstance(supplied, dict):
                raise ValueError("Lookup arguments must be an object")
            if batch:
                request_ids = supplied.pop("validator_request_ids", None)
                if (not isinstance(request_ids, list) or not request_ids
                        or any(not isinstance(item, str) or item not in self.contracts for item in request_ids)
                        or len(request_ids) != len(set(request_ids))):
                    raise ValueError("Lookup requires unique known validator_request_ids")
            else:
                if "validator_request_ids" in supplied:
                    raise ValueError("Single-request lookup scope is supplied by the runtime")
                request_ids = list(self.contracts)
            original = await tool.on_invoke_tool(context, json.dumps(supplied))
            if isinstance(original, BaseModel):
                payload = original.model_dump(mode="json")
            elif isinstance(original, str):
                payload = json.loads(original)
            else:
                payload = deepcopy(original)
            if not isinstance(payload, dict):
                raise ValueError("Validator lookup must return a structured object")
            if {"validator_record_refs", "validator_lookup_refs"}.intersection(payload):
                raise ValueError("Provider response uses reserved validator reference fields")
            # Tool-call IDs are runtime-owned. Test/direct invocation contexts
            # may not carry one; still allocate a unique concrete call identity.
            call_id = getattr(context, "tool_call_id", None) or f"lookup:{uuid4().hex}"
            captures = {
                request_id: self.adapter(self.contracts[request_id], tool.name,
                                         deepcopy(supplied), deepcopy(payload))
                for request_id in request_ids
            }
            catalog = []
            for request_id, captured in captures.items():
                refs = self.workspace.record_lookup(
                    request_id, call_id=call_id, attempt=captured.attempt,
                    records=captured.records, source_payload=payload,
                )
                for reference, record in zip(refs, captured.records):
                    catalog.append({
                        "request_id": request_id, "record_ref": reference,
                        "value": record.candidate.value, "label": record.candidate.label,
                        "source_path": record.source_path,
                        "available_fields": list(record.values),
                    })
            # No second copy of the rich records. The catalogue adds only the
            # runtime reference and the names usable for canonical field copies.
            return json.dumps({**payload, "validator_record_refs": catalog,
                               "validator_lookup_refs": [{"request_id": request_id, "lookup_ref": call_id}
                                                         for request_id in request_ids]})

        wrapped.on_invoke_tool = invoke
        return wrapped

    def assemble(self, raw_decision: Mapping[str, Any]) -> DomainValidatorResultBase:
        request_id = raw_decision.get("request_id")
        if not isinstance(request_id, str) or request_id not in self.contracts:
            raise ValueError("Unknown validator decision request")
        if self.standalone_evidence_records is not None:
            # Standalone document tools may register evidence after construction.
            # This list is runtime-owned, never part of the model's decision.
            self.contracts[request_id].request.evidence = deepcopy(self.standalone_evidence_records)
        decision = self.contracts[request_id].decision_schema.model_validate(raw_decision)
        return self.workspace.assemble(decision)

    def assemble_batch(self, raw_decisions: list[dict[str, Any]]) -> tuple[DomainValidatorResultBase, ...]:
        identifiers = [decision.get("request_id") for decision in raw_decisions]
        if (any(not isinstance(item, str) for item in identifiers)
                or len(identifiers) != len(set(identifiers))
                or set(identifiers) != set(self.contracts)):
            raise ValueError("Finalize exactly one decision for every validator request")
        # Do not publish accepted state until every decision passes assembly.
        assembled = {decision["request_id"]: self.assemble(decision) for decision in raw_decisions}
        return tuple(assembled[request_id] for request_id in self.contracts)


def runtime_for_schema(requests, *, result_schema, profile_request_ids=(), input_text=None, evidence=()):
    """Resolve the package-owned factory exported alongside the result schema."""
    module = sys.modules.get(getattr(result_schema, "__module__", ""))
    declaration = getattr(module, "COMPACT_VALIDATOR_RUNTIME", None)
    if declaration is None:
        return None
    from src.lib.packages.registry import load_package_registry
    from src.lib.packages.import_paths import extend_sys_path_for_package
    package_id, import_path = declaration
    package = load_package_registry().get_package(package_id)
    if package is None:
        raise ValueError("Compact validator schema must belong to a loaded runtime package")
    extend_sys_path_for_package(package)
    module_name, attribute = import_path.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    return factory(requests, result_schema=result_schema, profile_request_ids=profile_request_ids,
                   input_text=input_text, evidence=evidence)


def prepare_compact_tools(agent, runtime):
    agent.tools = [runtime.wrap_lookup_tool(tool) if getattr(tool, "name", None) in runtime.lookup_tool_names else tool
                   for tool in getattr(agent, "tools", [])]
    # Canonical output is the accepted server-side assembly, never another
    # model-authored copy after the finalization tool returns.
    agent.output_type = None


def compact_finalization_schema(tool, runtime, *, batch=False):
    decision_type = next(iter(runtime.contracts.values())).decision_schema
    fields: dict[str, Any] = {
        "results" if batch else "result": (list[decision_type] if batch else decision_type, ...),
    }
    model = create_model("CompactValidatorFinalizationInput", **fields)
    if hasattr(tool, "params_json_schema"):
        tool.params_json_schema = model.model_json_schema()
    return tool


def compact_finalization_instruction(runtime, *, tool_name, batch=False):
    contracts = [{"request_id": identifier,
                  "expected_slots": list(contract.request.expected_result_fields),
                  "record_slot_fields": dict(contract.record_slot_fields),
                  "scientific_slots": list(contract.scientific_slots)}
                 for identifier, contract in runtime.contracts.items()]
    return (
        "Runtime compact-decision contract: this replaces prior instructions to author a complete "
        "validator result or copy provider facts, identity metadata, or lookup_attempts. "
        "Make the scientific judgment, assess candidates with the returned validator_record_refs, "
        "and select authoritative fields for requested slots. Preserve ambiguity, explanations, "
        "and evidence references. Component/policy scientific fields retain their existing meaning. "
        "The program copies source facts, request identity and actual lookup counts into the canonical result. "
        f"Call {tool_name} with {'results containing exactly one compact decision per request' if batch else 'result containing one compact decision'} "
        "using the tool's declared schema. Repair rejected decisions; stop after acceptance. "
        "Do not output or reconstruct the complete canonical result. "
        "For batch lookups, validator_request_ids must identify only the requests served by that call. "
        "Each reference is valid only for its named request and this invocation. "
        "source_path is a JSON pointer into the lookup response, distinguishing records with identical IDs or labels. "
        "GO not_found_inputs are JSON pointers into that request's selected_inputs, not copied terms. "
        "Slot contracts: " + json.dumps(contracts)
        + " Supplied-context/scientific-option references (not database verification): " + json.dumps(runtime.source_catalog)
    )
