"""One model-facing result inspection schema across chat and flow runtimes."""
import json
from typing import Any, Callable, List, Mapping
from agents import function_tool
from src.lib.openai_agents.inspect_results import inspect_results

INSPECTION_GUIDANCE = """
Use inspect_results to search/list results available in this context, then inspect
objects, exact fields, explanations, evidence or validator details. In a flow,
only successfully completed steps and explicitly bound follow-up results are
available. Inspection is optional, read-only, and never advances a flow step.
Use result_view=original for extractor output; use result_view=post_validation
for the saved validation findings and data supplied to the formatter. These are
explicit versions of one result, not interchangeable summaries. Follow returned
read/next_call arguments, including envelope_revision and result_sha256. Missing
or superseded versions are unavailable; do not silently substitute another version
or rerun extraction to inspect a saved result.
"""


def make_result_inspection_tool(
    *,
    scope_provider: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
    on_inspected: Callable[[str, str | None, str], None] | None = None,
):
    @function_tool(
        name_override="inspect_results",
        description_override=(
            "Select result_view=original for extractor output or post_validation for the saved "
            "validation checkpoint used by the formatter. Follow returned envelope_revision and "
            "read calls; unavailable versions are never replaced with newer data. Search/list "
            "only cover the server-authorized scope. Inspection is optional and never advances "
            "flow steps. Explore persisted canonical extraction results for this chat without "
            "rerunning extraction. Use next_call to continue explicitly selected "
            "row pages; exact detail reads return complete selected content. Start with "
            "action=\"summary\" (counts by object type, status and validation state), "
            "then action=\"objects\" filtered by object_type, status, validation_state "
            "(open|resolved|none|any), severity, query (field text) or field_path, with "
            "fields to select YAML manifest fields; action=\"object\" or \"field\" "
            "for one object or one exact field value; action=\"validation\" (filters "
            "plus finding_ref for one finding) and action=\"validator_results\" "
            "(validator_result_key for one) for validation; action=\"evidence\" with "
            "object_ref for evidence text; action=\"details\" with object_ref for saved "
            "generic/custom attributes; action=\"list\" and action=\"search\" with "
            "query/target to choose among results. Values shown withheld are read "
            "exactly with their read call. Continue row pages with next_call; optional "
            "detail cursors select exact suffixes and result_sha256 verifies the source. Address a specific "
            "result with result_ref in extraction-result:<uuid> form. Direct validators use "
            "validator-result:<uuid>; action=\"validator_runs\" lists this chat's runs. "
            "For either kind, validator_results plus validator_result_key and "
            "detail_path=\"original.result\" reads the complete saved validator output. "
            "Historical projections explicitly report original_unavailable. This tool does not "
            "export, prepare for curation, inspect files or review sessions, or debug traces."
        ),
    )
    async def inspect_results_tool(
        action: str = "help",
        query: str | None = None,
        result_ref: str | None = None,
        target: str = "latest",
        object_ref: str | None = None,
        field_path: str | None = None,
        adapter_keys: List[str] | None = None,
        flow_run_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        object_type: str | None = None,
        status: str | None = None,
        validation_state: str | None = None,
        severity: str | None = None,
        fields: List[str] | None = None,
        finding_ref: str | None = None,
        validator_result_key: str | None = None,
        detail_path: str | None = None,
        result_sha256: str | None = None,
        result_view: str = "original",
        envelope_revision: int | None = None,
    ) -> str:
        """Inspect bounded persisted extraction results for the active chat."""

        scope = scope_provider() if scope_provider is not None else None
        if scope is not None and result_ref and result_ref not in scope:
            return json.dumps({"status": "error", "error_code": "unauthorized_bound_ref",
                               "message": "Result is not available in this execution."})
        output = await inspect_results(
            result_view=result_view,
            envelope_revision=envelope_revision,
            authorized_refs=scope,
            action=action,
            query=query,
            result_ref=result_ref,
            target=target,
            object_ref=object_ref,
            field_path=field_path,
            adapter_keys=adapter_keys,
            flow_run_id=flow_run_id,
            limit=limit,
            cursor=cursor,
            object_type=object_type,
            status=status,
            validation_state=validation_state,
            severity=severity,
            fields=fields,
            finding_ref=finding_ref,
            validator_result_key=validator_result_key,
            detail_path=detail_path,
            result_sha256=result_sha256,
        )

        if on_inspected is not None:
            on_inspected(action, result_ref, output)
        return output

    return inspect_results_tool
