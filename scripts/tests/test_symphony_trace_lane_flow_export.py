#!/usr/bin/env python3
"""Tests for the Symphony trace lane-flow exporter."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPORTER = REPO_ROOT / "scripts" / "utilities" / "symphony_trace_lane_flow_export.py"


def write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def run_export(trace_root: pathlib.Path, *extra_args: str) -> dict:
    output = subprocess.check_output(  # noqa: S603
        [sys.executable, str(EXPORTER), "--trace-root", str(trace_root), *extra_args],
        text=True,
    )
    return json.loads(output)


def test_reconstructs_explicit_linear_transition() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-588" / "2026-06-14T14-42-48Z-30"
    run_dir.mkdir(parents=True)
    (run_dir / "session-meta.json").write_text(
        json.dumps(
            {
                "issue_identifier": "ALL-588",
                "started_at": "2026-06-14T14:42:48Z",
                "finished_at": "2026-06-14T14:49:00Z",
            }
        ),
        encoding="utf-8",
    )

    linear_output = "\n".join(
        [
            "LINEAR_STATE_STATUS=ok",
            "LINEAR_STATE_ISSUE_ID=b3e6fb6e-7039-4632-9374-032e1961914d",
            "LINEAR_STATE_ISSUE_IDENTIFIER=ALL-588",
            "LINEAR_STATE_FROM=In Progress",
            "LINEAR_STATE_TO=Needs Review",
            "LINEAR_STATE_TARGET_ID=f6f49282-eba6-47d4-ad91-15fa3be6e073",
        ]
    )

    write_jsonl(
        run_dir / "trace.ndjson",
        [
            {
                "timestamp": "2026-06-14T14:42:49Z",
                "issue_identifier": "ALL-588",
                "issue_state": "In Progress",
                "method": "item/started",
                "payload": {"method": "item/started", "params": {}},
            },
            {
                "timestamp": "2026-06-14T14:48:59Z",
                "issue_identifier": "ALL-588",
                "issue_state": "In Progress",
                "method": "item/commandExecution/outputDelta",
                "payload": {
                    "method": "item/commandExecution/outputDelta",
                    "params": {
                        "itemId": "call-linear-state",
                        "delta": linear_output,
                    },
                },
            },
            {
                "timestamp": "2026-06-14T14:49:00Z",
                "issue_identifier": "ALL-588",
                "issue_state": "In Progress",
                "method": "item/completed",
                "payload": {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "call-linear-state",
                            "aggregatedOutput": linear_output,
                        }
                    },
                },
            },
            {
                "timestamp": "2026-06-14T14:49:01Z",
                "issue_identifier": "ALL-588",
                "issue_state": "Needs Review",
                "method": "item/completed",
                "payload": {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "post-transition-summary",
                            "aggregatedOutput": "Final summary after transition.",
                        }
                    },
                },
            },
        ],
    )

    payload = run_export(tmp_path, "--issue", "ALL-588")

    assert len(payload["issue_flows"]) == 1
    assert payload["issue_flows"][0]["lane_flow"] == [
        {
            "lane": "In Progress",
            "source": "trace_issue_state",
            "timestamp": "2026-06-14T14:42:49Z",
            "type": "run_start",
        },
        {
            "from": "In Progress",
            "source": "command_output_delta",
            "source_id": "call-linear-state",
            "status": "ok",
            "target_id": "f6f49282-eba6-47d4-ad91-15fa3be6e073",
            "timestamp": "2026-06-14T14:48:59Z",
            "to": "Needs Review",
            "type": "linear_transition",
        },
    ]


def test_reconstructs_scripted_lane_completion() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-589" / "2026-06-14T15-00-00Z-01"
    run_dir.mkdir(parents=True)

    write_jsonl(
        run_dir / "trace.ndjson",
        [
            {
                "timestamp": "2026-06-14T15:00:00Z",
                "issue_identifier": "ALL-589",
                "issue_state": "Needs Review",
                "payload": {"method": "item/started", "params": {}},
            },
            {
                "timestamp": "2026-06-14T15:00:05Z",
                "issue_identifier": "ALL-589",
                "issue_state": "Needs Review",
                "event": "scripted_lane_completed",
                "payload": {
                    "method": "symphony/scripted_lane/completed",
                    "params": {
                        "lane_name": "Needs Review claim",
                        "helper": "scripts/utilities/symphony_needs_review_claim.sh",
                        "output": "\n".join(
                            [
                                "LINEAR_STATE_STATUS=ok",
                                "LINEAR_STATE_FROM=Needs Review",
                                "LINEAR_STATE_TO=In Review",
                                "LINEAR_STATE_TARGET_ID=state-in-review",
                            ]
                        ),
                    },
                },
            },
        ],
    )

    payload = run_export(tmp_path / "issues")

    assert payload["issue_flows"][0]["lane_flow"] == [
        {
            "lane": "Needs Review",
            "source": "trace_issue_state",
            "timestamp": "2026-06-14T15:00:00Z",
            "type": "run_start",
        },
        {
            "from": "Needs Review",
            "helper": "scripts/utilities/symphony_needs_review_claim.sh",
            "lane": "Needs Review claim",
            "source": "scripted_lane",
            "status": "ok",
            "target_id": "state-in-review",
            "timestamp": "2026-06-14T15:00:05Z",
            "to": "In Review",
            "type": "scripted_lane_completed",
        },
    ]


def main() -> int:
    test_reconstructs_explicit_linear_transition()
    test_reconstructs_scripted_lane_completion()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
