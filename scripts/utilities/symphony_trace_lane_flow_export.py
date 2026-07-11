#!/usr/bin/env python3
"""Export Symphony issue lane flows from raw trace logs.

The weekly flow export needs lane reconstruction from explicit trace signals:
run-start issue state, Linear state helper output, and deterministic scripted
lane completion events. It must not infer lanes from arbitrary agent prose.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from collections.abc import Iterable
from typing import Any


LINEAR_STATE_RE = re.compile(r"^LINEAR_STATE_(STATUS|FROM|TO|TARGET_ID|ERROR)=(.*)$", re.MULTILINE)


class TraceInputError(RuntimeError):
    """Raised when trace input is present but unsafe to reconstruct from."""


def main() -> int:
    args = parse_args()
    trace_root = args.trace_root.expanduser().resolve()

    if not trace_root.exists():
        print(f"trace root does not exist: {trace_root}", file=sys.stderr)
        return 2

    issue_filter = set(args.issue or [])
    try:
        issue_flows = [
            flow
            for run_dir in iter_run_dirs(trace_root, issue_filter)
            if (flow := build_issue_flow(run_dir)) is not None
        ]
    except TraceInputError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "trace_root": str(trace_root),
        "issue_flows": issue_flows,
    }

    encoded = json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-root",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".symphony/persistent/agr_ai_curation/trace_logs",
        help="Root containing issue trace logs; accepts either the trace root or its issues/ directory.",
    )
    parser.add_argument(
        "--issue",
        action="append",
        help="Issue identifier to include. May be supplied multiple times.",
    )
    parser.add_argument("--output", type=pathlib.Path, help="Write JSON to this path instead of stdout.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args()


def iter_run_dirs(trace_root: pathlib.Path, issue_filter: set[str]) -> Iterable[pathlib.Path]:
    issues_root = trace_root if trace_root.name == "issues" else trace_root / "issues"
    if not issues_root.is_dir():
        return []

    run_dirs: list[pathlib.Path] = []
    for issue_dir in sorted(path for path in issues_root.iterdir() if path.is_dir()):
        if issue_filter and issue_dir.name not in issue_filter:
            continue

        run_dirs.extend(
            sorted(
                path
                for path in issue_dir.iterdir()
                if path.is_dir() and (path / "trace.ndjson").is_file()
            )
        )

    return run_dirs


def build_issue_flow(run_dir: pathlib.Path) -> dict[str, Any] | None:
    trace_file = run_dir / "trace.ndjson"
    events = sorted(read_ndjson(trace_file), key=lambda event: string_value(event.get("timestamp")) or "")
    if not events:
        return None

    session_meta = read_json_object(run_dir / "session-meta.json")
    issue_identifier = (
        string_value(session_meta.get("issue_identifier"))
        or first_string(events, "issue_identifier")
        # The trace directory is the final deterministic source when trace payloads omit identity.
        or run_dir.parent.name
    )

    return {
        "issue_identifier": issue_identifier,
        "run_id": run_dir.name,
        "started_at": string_value(session_meta.get("started_at")),
        "finished_at": string_value(session_meta.get("finished_at")),
        "lane_flow": build_lane_flow(events),
    }


def read_ndjson(path: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceInputError(
                    f"invalid JSON in {path}:{line_number}:{exc.colno}: {exc.msg}"
                ) from exc

            if isinstance(record, dict):
                records.append(record)
            else:
                raise TraceInputError(f"expected JSON object in {path}:{line_number}")

    return records


def read_json_object(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TraceInputError(f"invalid JSON in {path}:{exc.lineno}:{exc.colno}: {exc.msg}") from exc

    if not isinstance(value, dict):
        raise TraceInputError(f"expected JSON object in {path}")

    return value


def build_lane_flow(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flow: list[dict[str, Any]] = []
    seen_command_transitions: set[tuple[str, str | None, str | None, str | None, str | None]] = set()

    for event in events:
        if not flow and (lane := string_value(event.get("issue_state"))):
            flow.append(
                {
                    "type": "run_start",
                    "lane": lane,
                    "timestamp": string_value(event.get("timestamp")),
                    "source": "trace_issue_state",
                }
            )

        for transition in linear_transition_events(event):
            key = command_transition_dedupe_key(transition)
            if key:
                if key in seen_command_transitions:
                    continue
                seen_command_transitions.add(key)

            flow.append(transition)

        flow.extend(scripted_lane_events(event))

    return flow


def linear_transition_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []

    for source, source_id, text in transition_texts(event):
        transition = parse_linear_state_transition(text)
        if not transition:
            continue

        transition_event: dict[str, Any] = drop_none(
            {
                **transition,
                "type": "linear_transition",
                "timestamp": string_value(event.get("timestamp")),
                "source": source,
                "source_id": source_id,
            }
        )
        transitions.append(transition_event)

    return transitions


def scripted_lane_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    payload = mapping_value(event.get("payload"))
    method = string_value(get_in(payload, ("method",)))
    phases = {
        "symphony/scripted_lane/started": "started",
        "symphony/scripted_lane/completed": "completed",
        "symphony/scripted_lane/failed": "failed",
    }
    phase = phases.get(method or "")
    if phase is None:
        return []

    params = mapping_value(get_in(payload, ("params",)))
    transition = mapping_value(params.get("transition"))
    if not transition:
        transition = parse_linear_state_transition(string_value(params.get("output"))) or {}

    scripted_event = {
        "type": f"scripted_lane_{phase}",
        "lane": string_value(params.get("lane_name")),
        "helper": string_value(params.get("helper")),
        "exit_status": params.get("exit_status"),
        "error": string_value(params.get("error")),
        "timestamp": string_value(event.get("timestamp")),
        "source": "scripted_lane",
    }

    if transition:
        for key in ("from", "to", "status", "target_id", "error"):
            if value := string_value(transition.get(key)):
                scripted_event[key] = value

    return [drop_none(scripted_event)]


def transition_texts(event: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    payload = mapping_value(event.get("payload"))

    candidates = [
        (
            "command_output_delta",
            string_value(get_in(payload, ("params", "itemId"))),
            string_value(get_in(payload, ("params", "delta"))),
        ),
        (
            "command_completed",
            string_value(get_in(payload, ("params", "item", "id"))),
            string_value(get_in(payload, ("params", "item", "aggregatedOutput"))),
        ),
    ]

    return [(source, source_id, text) for source, source_id, text in candidates if text]


def parse_linear_state_transition(text: str | None) -> dict[str, str] | None:
    if not text:
        return None

    fields = {key.lower(): value.strip() for key, value in LINEAR_STATE_RE.findall(text)}
    from_state = string_value(fields.get("from"))
    to_state = string_value(fields.get("to"))
    status = string_value(fields.get("status"))

    if not from_state or not to_state or not status:
        return None

    transition = {
        "from": from_state,
        "to": to_state,
        "status": status,
        "target_id": string_value(fields.get("target_id")),
        "error": string_value(fields.get("error")),
    }
    return drop_none(transition)


def command_transition_dedupe_key(transition: dict[str, Any]) -> tuple[str, str | None, str | None, str | None, str | None] | None:
    source = string_value(transition.get("source"))
    source_id = string_value(transition.get("source_id"))
    if source not in {"command_output_delta", "command_completed"} or not source_id:
        return None

    return (
        source_id,
        string_value(transition.get("from")),
        string_value(transition.get("to")),
        string_value(transition.get("target_id")),
        string_value(transition.get("status")),
    )


def first_string(events: list[dict[str, Any]], key: str) -> str | None:
    for event in events:
        if value := string_value(event.get(key)):
            return value
    return None


def get_in(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for segment in path:
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def mapping_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    return stripped or None


def drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


if __name__ == "__main__":
    raise SystemExit(main())
