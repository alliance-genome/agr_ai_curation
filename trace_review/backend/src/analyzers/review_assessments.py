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
            "note": "An accepted assessment can remain scientifically unresolved; explicit unresolved_fields or absence_assessment=unresolved retain scientific uncertainty, including configured unsupported lookups that did not execute. Reject records an opinion and leaves an extraction-only record pending; retire is the reviewer's own decision that a whole record does not answer the question, which removes it from current results and keeps it in history for a curator to restore.",
        }
        applied = body.get("applied") if isinstance(body, dict) else None
        if isinstance(arguments, dict) and arguments.get("packet_id") and isinstance(applied, list):
            # Packet saves name short handles; the save result resolves each one.
            call["validation_review_decisions"]["packet_handles"] = {
                item["handle"]: {key: item.get(key) for key in ("decision_unit_id", "target_id", "action")}
                for item in applied if isinstance(item, dict) and "handle" in item}
        if isinstance(applied, list):
            # Reviewer-authored retirements name their durable decision fact.
            retired = [{key: item.get(key) for key in ("handle", "target_id", "retirement_id")}
                       for item in applied if isinstance(item, dict) and item.get("action") == "retire"]
            if retired:
                call["validation_review_decisions"]["retired_records"] = retired
        if isinstance(body, dict) and isinstance(body.get("blocking"), list):
            # What the save reported as still blocking readiness, exactly as returned.
            call["validation_review_decisions"]["blocking"] = body["blocking"]
