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


def run_export_failure(trace_root: pathlib.Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(EXPORTER), "--trace-root", str(trace_root), *extra_args],
        capture_output=True,
        check=False,
        text=True,
    )


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
                "timestamp": "2026-06-14T15:00:01Z",
                "issue_identifier": "ALL-589",
                "issue_state": "Needs Review",
                "event": "scripted_lane_started",
                "payload": {
                    "method": "symphony/scripted_lane/started",
                    "params": {
                        "lane_name": "Needs Review claim",
                        "helper": "scripts/utilities/symphony_needs_review_claim.sh",
                    },
                },
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
                        "exit_status": 0,
                        "transition": {
                            "from": "Needs Review",
                            "to": "In Review",
                            "status": "claimed",
                        },
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
            "helper": "scripts/utilities/symphony_needs_review_claim.sh",
            "lane": "Needs Review claim",
            "source": "scripted_lane",
            "timestamp": "2026-06-14T15:00:01Z",
            "type": "scripted_lane_started",
        },
        {
            "exit_status": 0,
            "from": "Needs Review",
            "helper": "scripts/utilities/symphony_needs_review_claim.sh",
            "lane": "Needs Review claim",
            "source": "scripted_lane",
            "status": "claimed",
            "timestamp": "2026-06-14T15:00:05Z",
            "to": "In Review",
            "type": "scripted_lane_completed",
        },
    ]


def test_reconstructs_scripted_lane_failure() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-590" / "2026-06-14T15-30-00Z-01"
    run_dir.mkdir(parents=True)

    write_jsonl(
        run_dir / "trace.ndjson",
        [
            {
                "timestamp": "2026-06-14T15:30:00Z",
                "issue_identifier": "ALL-590",
                "issue_state": "Ready for PR",
                "payload": {"method": "item/started", "params": {}},
            },
            {
                "timestamp": "2026-06-14T15:30:03Z",
                "issue_identifier": "ALL-590",
                "issue_state": "Ready for PR",
                "event": "scripted_lane_failed",
                "payload": {
                    "method": "symphony/scripted_lane/failed",
                    "params": {
                        "lane_name": "Ready for PR",
                        "helper": "scripts/utilities/symphony_ready_for_pr_lane.sh",
                        "exit_status": 3,
                        "transition": {
                            "from": "Ready for PR",
                            "status": "error",
                            "error": "Linear state update failed",
                        },
                    },
                },
            },
        ],
    )

    payload = run_export(tmp_path / "issues")

    assert payload["issue_flows"][0]["lane_flow"][-1] == {
        "error": "Linear state update failed",
        "exit_status": 3,
        "from": "Ready for PR",
        "helper": "scripts/utilities/symphony_ready_for_pr_lane.sh",
        "lane": "Ready for PR",
        "source": "scripted_lane",
        "status": "error",
        "timestamp": "2026-06-14T15:30:03Z",
        "type": "scripted_lane_failed",
    }


def test_corrupt_trace_line_fails_with_path_and_line() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-590" / "2026-06-14T16-00-00Z-01"
    run_dir.mkdir(parents=True)
    (run_dir / "trace.ndjson").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-14T16:00:00Z",
                        "issue_identifier": "ALL-590",
                        "issue_state": "In Progress",
                    }
                ),
                '{"timestamp": "2026-06-14T16:00:01Z"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_export_failure(tmp_path, "--issue", "ALL-590")

    assert result.returncode == 1
    assert "invalid JSON in" in result.stderr
    assert "trace.ndjson:2:" in result.stderr


def test_corrupt_session_metadata_fails_with_path() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-591" / "2026-06-14T17-00-00Z-01"
    run_dir.mkdir(parents=True)
    (run_dir / "session-meta.json").write_text('{"issue_identifier": "ALL-591"', encoding="utf-8")
    write_jsonl(
        run_dir / "trace.ndjson",
        [
            {
                "timestamp": "2026-06-14T17:00:00Z",
                "issue_identifier": "ALL-591",
                "issue_state": "In Progress",
            }
        ],
    )

    result = run_export_failure(tmp_path, "--issue", "ALL-591")

    assert result.returncode == 1
    assert "invalid JSON in" in result.stderr
    assert "session-meta.json" in result.stderr


def test_preserves_failed_linear_transition_attempt() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-592" / "2026-06-14T18-00-00Z-01"
    run_dir.mkdir(parents=True)
    failed_output = "\n".join(
        [
            "LINEAR_STATE_STATUS=error",
            "LINEAR_STATE_FROM=Ready for PR",
            "LINEAR_STATE_TO=Human Review Prep",
            "LINEAR_STATE_TARGET_ID=state-human-review-prep",
            "LINEAR_STATE_ERROR=Linear issueUpdate did not succeed.",
        ]
    )
    write_jsonl(
        run_dir / "trace.ndjson",
        [
            {
                "timestamp": "2026-06-14T18:00:00Z",
                "issue_identifier": "ALL-592",
                "issue_state": "Ready for PR",
                "payload": {"method": "item/started", "params": {}},
            },
            {
                "timestamp": "2026-06-14T18:00:10Z",
                "issue_identifier": "ALL-592",
                "issue_state": "Ready for PR",
                "payload": {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "failed-linear-state",
                            "aggregatedOutput": failed_output,
                        }
                    },
                },
            },
        ],
    )

    payload = run_export(tmp_path, "--issue", "ALL-592")

    assert payload["issue_flows"][0]["lane_flow"][1] == {
        "error": "Linear issueUpdate did not succeed.",
        "from": "Ready for PR",
        "source": "command_completed",
        "source_id": "failed-linear-state",
        "status": "error",
        "target_id": "state-human-review-prep",
        "timestamp": "2026-06-14T18:00:10Z",
        "to": "Human Review Prep",
        "type": "linear_transition",
    }


def test_preserves_repeated_transitions_without_command_id() -> None:
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="symphony-lane-flow-test-"))
    run_dir = tmp_path / "issues" / "ALL-593" / "2026-06-14T19-00-00Z-01"
    run_dir.mkdir(parents=True)
    linear_output = "\n".join(
        [
            "LINEAR_STATE_STATUS=ok",
            "LINEAR_STATE_FROM=In Progress",
            "LINEAR_STATE_TO=Needs Review",
            "LINEAR_STATE_TARGET_ID=state-needs-review",
        ]
    )
    write_jsonl(
        run_dir / "trace.ndjson",
        [
            {
                "timestamp": "2026-06-14T19:00:00Z",
                "issue_identifier": "ALL-593",
                "issue_state": "In Progress",
                "payload": {"method": "item/started", "params": {}},
            },
            {
                "timestamp": "2026-06-14T19:00:10Z",
                "issue_identifier": "ALL-593",
                "issue_state": "In Progress",
                "payload": {
                    "method": "item/commandExecution/outputDelta",
                    "params": {"delta": linear_output},
                },
            },
            {
                "timestamp": "2026-06-14T19:00:20Z",
                "issue_identifier": "ALL-593",
                "issue_state": "In Progress",
                "payload": {
                    "method": "item/commandExecution/outputDelta",
                    "params": {"delta": linear_output},
                },
            },
        ],
    )

    payload = run_export(tmp_path, "--issue", "ALL-593")

    assert [
        entry["timestamp"]
        for entry in payload["issue_flows"][0]["lane_flow"]
        if entry["type"] == "linear_transition"
    ] == ["2026-06-14T19:00:10Z", "2026-06-14T19:00:20Z"]


def main() -> int:
    test_reconstructs_explicit_linear_transition()
    test_reconstructs_scripted_lane_completion()
    test_reconstructs_scripted_lane_failure()
    test_corrupt_trace_line_fails_with_path_and_line()
    test_corrupt_session_metadata_fails_with_path()
    test_preserves_failed_linear_transition_attempt()
    test_preserves_repeated_transitions_without_command_id()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
