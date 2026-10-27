"""Expose explicit review decisions and their field uncertainty without inference."""


def annotate_review_assessments(calls):
    for call in calls:
        if call.get("name") != "record_validation_review":
            continue
        arguments = call.get("input")
        result = call.get("tool_result")
        parsed = result.get("parsed") if isinstance(result, dict) else None
        body = parsed.get("json_data") if isinstance(parsed, dict) else None
        call["validation_review_decisions"] = {
            "arguments": arguments, "result": body,
            "result_available": body is not None,
            "note": "An accepted assessment can remain scientifically unresolved; only explicit unresolved_fields carry field uncertainty.",
        }
