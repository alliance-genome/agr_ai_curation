"""Exact targeted extraction and record lifecycle diagnostics, without verdicts."""
TOOLS = frozenset({"request_targeted_extraction", "select_extraction_contribution",
                   "validate_selected_extraction_source", "decide_record_retirement"})
FACTS = frozenset({"extraction_followup_request", "extraction_contribution", "extraction_source_selection",
                   "record_retirement_decision", "record_restoration", "record_retirement_response",
                   "record_restoration_response"})


def annotate(calls):
    for call in calls:
        if call.get("name") not in TOOLS:
            continue
        result = call.get("tool_result")
        parsed = result.get("parsed") if isinstance(result, dict) else None
        body = parsed.get("json_data") if isinstance(parsed, dict) else None
        call["extraction_followup"] = {"tool": call["name"], "arguments": call.get("input"),
                                      "result": body, "result_available": body is not None}
