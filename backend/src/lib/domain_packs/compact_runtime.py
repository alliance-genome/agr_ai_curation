"""Shared tool boundary for compact validator decisions.

This layer knows invocation/request identity, not Alliance response formats.
Package adapters own lookup interpretation and canonical record projection.
The model-facing view applies only the package-neutral agr_lookup envelope view.
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

from agr_ai_curation_runtime.agr_lookup import lookup_model_view
from src.lib.domain_packs.compact_decisions import (
    CanonicalValidatorRecord, DecisionContract, ValidatorDecisionWorkspace,
)
from src.lib.openai_agents.tool_result_bounds import (
    ToolResultBudgetError, bounded_json_result, budget_failure, full_tool_results_requested,
    json_pointer_for_row, report_budget_failure_result, result_view_schema_properties,
    serialized_size, tool_result_budget,
)
from src.schemas.domain_validator import DomainValidatorResultBase, ValidatorLookupAttempt

# Page/detail arguments a validator lookup accepts; continuation always names
# the stored lookup instead of re-running it, so refs stay call-scoped.
_LOOKUP_VIEW_ARGUMENTS = ("lookup_ref", "result_offset", "detail_path", "detail_cursor")


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
        # Model views of captured lookups keyed by lookup_ref (the concrete call
        # id), scoped to this invocation; the model pages them without a re-run.
        # The complete responses stay in the workspace ledger.
        self._lookup_views: dict[str, dict[str, Any]] = {}

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
        if "lookup_ref" in schema.get("properties", {}):
            raise ValueError("Lookup tool already declares lookup_ref")
        properties = schema.setdefault("properties", {})
        # Stateless re-query continuation is replaced by stored-lookup paging.
        properties.pop("result_sha256", None)
        if "required" in schema:
            schema["required"] = [name for name in schema["required"] if name != "result_sha256"]
        view_properties = result_view_schema_properties()
        properties.update({
            "lookup_ref": {
                "type": ["string", "null"],
                "description": (
                    "Only to continue a lookup whose result was paged: its lookup_ref. "
                    "The stored result is paged; the lookup is not run again."
                ),
            },
            **{name: view_properties[name] for name in ("result_offset", "detail_path", "detail_cursor")},
        })
        if getattr(tool, "strict_json_schema", False):
            schema["required"] = sorted({*schema.get("required", []), *_LOOKUP_VIEW_ARGUMENTS})
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
            if supplied.get("result_sha256") is not None:
                raise ValueError("Validator lookups continue with lookup_ref, not result_sha256")
            supplied.pop("result_sha256", None)
            view = {name: supplied.pop(name) for name in _LOOKUP_VIEW_ARGUMENTS if name in supplied}
            view = {name: value for name, value in view.items() if value is not None}
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
            if "lookup_ref" in view:
                stored = self._lookup_views.get(view.pop("lookup_ref"))
                if stored is None:
                    raise ValueError("Unknown lookup_ref for this validator run")
                return json.dumps(self._bounded_lookup_view(tool.name, stored, view))
            if view:
                raise ValueError("Continue a paged lookup with the lookup_ref from its result")
            with full_tool_results_requested():
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
            # The workspace keeps the complete response; the model pages a view
            # that shows each returned row once (derived restatements dropped).
            stored = {
                "call_id": call_id,
                "view": lookup_model_view(payload),
                "catalog": catalog,
                "lookup_refs": [{"request_id": request_id, "lookup_ref": call_id}
                                for request_id in request_ids],
            }
            self._lookup_views[call_id] = stored
            return json.dumps(self._bounded_lookup_view(tool.name, stored, {}))

        wrapped.on_invoke_tool = invoke
        return wrapped

    def _bounded_lookup_view(self, tool_name: str, stored: Mapping[str, Any],
                             view: Mapping[str, Any]) -> dict[str, Any]:
        """Serve a captured lookup's model view whole when it fits, else as bounded pages.

        Capture already holds the complete provider response application-side;
        the model sees each page's rows with exactly the record refs for those
        rows, and reads withheld values through exact detail chunks.
        """
        model_view, catalog = stored["view"], stored["catalog"]
        complete = {**model_view, "validator_record_refs": catalog,
                    "validator_lookup_refs": stored["lookup_refs"]}
        budget = tool_result_budget()
        if not view and serialized_size(complete) <= budget:
            return complete

        def refs_for_page(keys, mode, start, returned):
            if keys is None or mode != "items":
                return {"validator_record_refs": catalog if start == 0 else []}
            prefixes = [json_pointer_for_row(keys, index) for index in range(start, start + returned)]
            root = json_pointer_for_row(keys, 0).rsplit("/", 1)[0]
            rows = [entry for entry in catalog
                    if any((entry.get("source_path") or "") == prefix
                           or (entry.get("source_path") or "").startswith(prefix + "/")
                           for prefix in prefixes)]
            if start == 0:
                # Records located outside the paged rows travel with the first page.
                rows.extend(entry for entry in catalog
                            if not (entry.get("source_path") or "").startswith(root + "/"))
            return {"validator_record_refs": rows}

        try:
            return bounded_json_result(
                {**model_view, "validator_lookup_refs": stored["lookup_refs"]},
                budget=budget,
                offset=view.get("result_offset", 0),
                detail_path=view.get("detail_path"),
                detail_cursor=view.get("detail_cursor"),
                continuation_args={"lookup_ref": stored["call_id"]},
                stateless=False,
                page_extras=refs_for_page,
            )
        except ToolResultBudgetError as exc:
            failure = budget_failure(tool_name=tool_name, measured=exc.measured, limit=exc.limit,
                                     field=view.get("detail_path"))
            report_budget_failure_result(failure, tool_name=tool_name,
                                         component="validator_lookup_capture")
            return failure

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
    from src.lib.config.package_default_sources import resolve_packages_dir
    from src.lib.packages.registry import load_package_registry
    from src.lib.packages.import_paths import extend_sys_path_for_package
    package_id, import_path = declaration
    package = load_package_registry(resolve_packages_dir(None)).get_package(package_id)
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
                  "scientific_slots": list(contract.scientific_slots),
                  "domain_contract": deepcopy(dict(contract.domain_contract))}
                 for identifier, contract in runtime.contracts.items()]
    return (
        "Runtime compact-decision contract: this replaces prior instructions to author a complete "
        "validator result or copy provider facts, identity metadata, or lookup_attempts. "
        "Make the scientific judgment, assess candidates with the returned validator_record_refs, "
        "and select authoritative fields for requested slots. Preserve ambiguity, explanations, "
        "and evidence references. Follow each request's domain_contract for its package-specific "
        "decision shape; component slots are distinct from root slots. "
        "The program copies source facts, request identity and actual lookup counts into the canonical result. "
        f"Call {tool_name} with {'results containing exactly one compact decision per request' if batch else 'result containing one compact decision'} "
        "using the tool's declared schema. Repair rejected decisions; stop after acceptance. "
        "Do not output or reconstruct the complete canonical result. "
        "For batch lookups, validator_request_ids must identify only the requests served by that call. "
        "Each reference is valid only for its named request and this invocation. "
        "source_path is a JSON pointer into the lookup response, distinguishing records with identical IDs or labels. "
        "Derived restatements of returned rows are omitted; the program keeps the complete response. "
        "GO not_found_inputs are JSON pointers into that request's selected_inputs, not copied terms. "
        "Slot contracts: " + json.dumps(contracts)
        + " Supplied-context/scientific-option references (not database verification): " + json.dumps(runtime.source_catalog)
    )
