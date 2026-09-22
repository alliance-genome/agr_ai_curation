"""Join compact decision references to observed catalogs, without inventing results."""

from copy import deepcopy
import json
from typing import Any


def _references(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            if key in {"record_ref", "lookup_ref"} and isinstance(child, str):
                yield key, child, child_path
            elif key == "lookup_refs" and isinstance(child, list):
                for index, reference in enumerate(child):
                    if isinstance(reference, str):
                        yield "lookup_ref", reference, f"{child_path}/{index}"
            else:
                yield from _references(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _references(child, f"{path}/{index}")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def annotate_compact_validator_calls(tool_calls: list[dict[str, Any]]) -> None:
    """Add diagnostic joins to time-ordered calls; retain raw input/output exactly.

    Records are scoped by both request and opaque reference. Missing or conflicting
    catalogs remain explicit diagnostic gaps, never canonical scientific findings.
    Later lookups cannot retrospectively ground an earlier decision.
    """
    catalogs: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for call in tool_calls:
        parsed = _mapping(_mapping(call.get("tool_result")).get("parsed"))
        payload = _mapping(parsed.get("json_data", call.get("output")))
        if isinstance(payload, dict):
            for collection, kind in (("validator_record_refs", "record_ref"),
                                     ("validator_lookup_refs", "lookup_ref")):
                rows = payload.get(collection)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    request, reference = row.get("request_id"), row.get(kind)
                    if not isinstance(request, str) or not isinstance(reference, str):
                        continue
                    key = (request, kind, reference)
                    source = {"catalog": deepcopy(row),
                              "source_call_id": call.get("call_id") or call.get("id")}
                    entries = catalogs.setdefault(key, [])
                    if source not in entries:
                        entries.append(source)
        arguments = _mapping(call.get("input"))
        decisions = arguments.get("results")
        if not isinstance(decisions, list):
            decisions = [arguments.get("result")]
        summaries = []
        for decision in decisions:
            if not isinstance(decision, dict) or not isinstance(decision.get("request_id"), str):
                continue
            references = []
            for kind, reference, path in _references(decision):
                sources = catalogs.get((decision["request_id"], kind, reference), [])
                # SDK generation and TOOL observations can expose the same
                # catalog under different observation IDs. Keep both sources,
                # but do not call identical facts conflicting.
                distinct = {json.dumps(source["catalog"], sort_keys=True) for source in sources}
                references.append({"kind": kind, "reference": reference, "decision_path": path,
                    "status": "matched" if len(distinct) == 1 else "conflicting" if sources else "unmatched",
                    "sources": deepcopy(sources)})
            if references:
                summaries.append({"request_id": decision["request_id"],
                    "decision_status": decision.get("status"), "references": references})
        if summaries:
            call["compact_validation"] = summaries
